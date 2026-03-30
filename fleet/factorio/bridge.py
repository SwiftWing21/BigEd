# fleet/factorio/bridge.py
"""Main Factorio bridge process — tick loop + RCON + API server."""
import asyncio
import json
import logging
import queue
import threading
from pathlib import Path

from factorio.bridge_config import load_factorio_config, BridgeConfig
from factorio.rcon_client import RCONClient
from factorio.state_parser import parse_state, parse_metrics, state_to_markdown
from factorio.action_translator import translate_batch
from factorio.world_model import WorldModel
from factorio.cadence import CadenceController
from factorio.bridge_api import create_api, update_status, store_result, update_training_status
from factorio.agent_brain import AgentBrain

log = logging.getLogger("biged.factorio.bridge")


class FactorioBridge:
    """Main bridge between BigEd fleet and Factorio headless server."""

    def __init__(self, config: BridgeConfig):
        self.config = config
        self.rcon = RCONClient(
            config.rcon_host, config.rcon_port, config.rcon_password,
            timeout=config.rcon_timeout_secs,
        )
        self.world_model = WorldModel()
        self.cadence = CadenceController(
            fast_ms=config.cadence_fast_ms,
            medium_ms=config.cadence_medium_ms,
            slow_ms=config.cadence_slow_ms,
            boost_ms=config.adaptive_boost_ms,
            boost_hold_secs=config.adaptive_boost_hold_secs,
            adaptive_events=config.adaptive_events,
        )
        self.cadence.set_mode(config.cadence)
        self.command_queue: queue.Queue = queue.Queue()
        self.brain = AgentBrain(config, self.world_model)
        self._running = False
        self._consecutive_failures = 0
        self._tick_count = 0

        # Hybrid teacher: LLM intervenes when RL is stuck on a lesson
        self._teacher_stuck_threshold = 500  # steps on same lesson before LLM help
        self._teacher_lesson_step_count = 0
        self._teacher_last_lesson = -1
        self._teacher_cooldown = 0  # skip N ticks after teacher intervention

        if self.config.mode == "ml":
            from factorio.state_encoder import StateEncoder
            from factorio.ml_policy import FactorioPolicy
            from factorio.action_space import ActionSpace
            from factorio.reward import RewardComputer
            from factorio.trainer import PPOTrainer, TrajectoryBuffer
            from factorio.episode_manager import EpisodeManager
            from factorio.curriculum_manager import CurriculumManager

            self._encoder = StateEncoder(phase=config.current_phase)
            self._action_space = ActionSpace(phase=config.current_phase)
            self._policy = FactorioPolicy(
                grid_channels=4, grid_size=64,
                feature_dim=self._encoder.feature_dim,
                num_action_types=8,
                num_entities=self._action_space.num_entity_types,
                num_recipes=self._action_space.num_recipe_types,
                num_techs=self._action_space.num_tech_types,
            )
            self._reward = RewardComputer(phase=config.current_phase)
            self._trainer = PPOTrainer(
                self._policy, lr=config.ml_learning_rate,
                gamma=config.ml_gamma, gae_lambda=config.ml_gae_lambda,
                clip_ratio=config.ml_clip_ratio,
                entropy_coeff=config.ml_entropy_coeff,
                value_coeff=config.ml_value_coeff,
                checkpoint_dir=config.ml_checkpoint_dir,
            )
            self._episode_mgr = EpisodeManager(
                rcon=self.rcon, phase=config.current_phase,
                max_steps=config.ml_max_episode_steps,
            )
            self._curriculum = CurriculumManager(
                current_phase=config.current_phase,
                curricula_dir=config.curriculum_dir,
            )
            self._trajectory_buf = TrajectoryBuffer()
            self._prev_state = None
            self._ml_step_count = 0
            self._last_ppo_stats: dict = {}
            self._last_reward: float = 0.0

    async def connect_with_retry(self) -> bool:
        """Connect to RCON with exponential backoff."""
        delay = 1.0
        max_delay = 30.0
        while self._running:
            try:
                await self.rcon.connect()
                self._consecutive_failures = 0
                return True
            except Exception as e:
                log.warning("RCON connect failed: %s. Retrying in %ss", e, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, max_delay)
        return False

    async def tick(self) -> None:
        """Run a single perception -> action tick."""
        self._tick_count += 1

        # 1. Get state via remote interface
        try:
            state_raw = await self.rcon.remote_call("get_state")
            state = parse_state(state_raw)
            self._consecutive_failures = 0
        except Exception as e:
            self._consecutive_failures += 1
            log.warning("RCON state fetch failed (%d): %s",
                        self._consecutive_failures, e)
            if self._consecutive_failures >= self.config.rcon_max_retries:
                log.error("Circuit breaker tripped — pausing ticks")
                await asyncio.sleep(self.config.rcon_circuit_breaker_secs)
                self._consecutive_failures = 0
            return

        # 0. Ensure agent has a body (craft/move/mine require it)
        if not state.player_alive:
            log.warning("LLM tick %d: agent has no body — calling ensure_player",
                        self._tick_count)
            try:
                await self.rcon.remote_call("ensure_player")
                state_raw = await self.rcon.remote_call("get_state")
                state = parse_state(state_raw)
                if not state.player_alive:
                    log.error("Agent still has no body after ensure_player — skipping tick")
                    return
            except Exception:
                log.warning("ensure_player failed — skipping tick", exc_info=True)
                return

        # 2. Get metrics (every 5th tick)
        metrics = None
        if self._tick_count % 5 == 0:
            try:
                metrics_raw = await self.rcon.remote_call("get_metrics")
                metrics = parse_metrics(metrics_raw)
            except Exception:
                log.warning("Metrics fetch failed, skipping")

        # 3. Update world model + detect events
        events = self.world_model.update(state, metrics)
        for event in events:
            self.cadence.on_event(event.event_type)
            log.info("Event: %s — %s", event.event_type, event.detail)

        # 4. Write state file (debug/dashboard)
        try:
            md = state_to_markdown(state, metrics)
            state_path = Path(self.config.state_file)
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(md, encoding="utf-8")
        except Exception:
            log.warning("Failed to write state file", exc_info=True)

        # 5a. Drain human command queue first (priority) — EXISTING CODE, KEEP AS-IS
        while not self.command_queue.empty():
            try:
                cmd = self.command_queue.get_nowait()
                actions = cmd.get("actions", [])
                translated = translate_batch(actions)
                results = []
                for ta in translated:
                    if ta.action_type == "wait":
                        ticks = 60
                        await asyncio.sleep(ticks / 60.0)
                        results.append({"action": "wait", "success": True})
                        continue
                    if not ta.rcon_command:
                        continue
                    try:
                        cmd_json = ta.rcon_command.split(" ", 1)[1] if " " in ta.rcon_command else "{}"
                        resp = await self.rcon.remote_call("exec_cmd", cmd_json)
                        result = json.loads(resp)
                    except json.JSONDecodeError:
                        result = {"raw": resp}
                    except Exception as e:
                        result = {"error": str(e), "success": False}
                    result["description"] = ta.description
                    results.append(result)
                store_result(cmd["id"], {"results": results})
            except queue.Empty:
                break

        # 5b. Ask brain for next autonomous action
        if self.command_queue.empty():
            action = await asyncio.to_thread(self.brain.next_action, state, events)
            if action and action.rcon_command:
                try:
                    cmd_json = action.rcon_command.split(" ", 1)[1] if " " in action.rcon_command else "{}"
                    resp = await self.rcon.remote_call("exec_cmd", cmd_json)
                    try:
                        result = json.loads(resp)
                    except json.JSONDecodeError:
                        result = {"raw": resp}
                except Exception as e:
                    result = {"error": str(e), "success": False}
                self.brain.report_result(action, result)
                log.info("Brain action: %s — %s",
                         action.description,
                         "OK" if result.get("success") else result.get("error", "unknown"))

        # 5c. Check curriculum progress
        progress = self.brain.check_progress(state)
        if progress.get("lesson_passed"):
            log.info("Lesson passed: %s", progress.get("lesson_name"))
        if progress.get("phase_complete"):
            log.info("Phase %d complete!", progress.get("phase"))
            if self.config.auto_advance:
                self.brain.curriculum.advance_phase()

        # 6. Update bridge status
        update_status(True, state.tick, self.cadence.mode)

    async def _teacher_intervention(self, state) -> bool:
        """LLM teacher generates and executes actions when RL agent is stuck.

        Uses the existing AgentBrain (Ollama LLM) to read the curriculum hint,
        understand the game state, and produce a plan of concrete actions.
        Returns True if the teacher acted (caller should skip RL action).
        """
        objective = self._curriculum.get_current_objective()
        hint = objective.get("hint", "")
        lesson = objective.get("lesson_name", "?")
        log.info("Teacher intervention for lesson '%s' — hint: %s", lesson, hint)

        try:
            # Sync curriculum state so the brain sees the current lesson
            self.brain.curriculum._phase = self._curriculum._phase
            self.brain.curriculum._tracker = self._curriculum._tracker
            self.brain.curriculum._lessons = self._curriculum._lessons
            self.brain.curriculum._meta = self._curriculum._meta

            # Generate plan via Ollama (blocking — runs in executor)
            plan = await asyncio.get_event_loop().run_in_executor(
                None, self.brain._generate_plan, state)
            if not plan:
                log.warning("Teacher produced no plan")
                return False

            # Execute up to 10 actions from the teacher plan
            executed = 0
            from factorio.action_translator import translate_action
            for action_dict in plan[:10]:
                translated = translate_action(action_dict)
                if translated.rcon_command:
                    try:
                        resp = await self.rcon.remote_call("exec_cmd", translated.rcon_command)
                        log.info("Teacher action: %s → %s",
                                 translated.description, str(resp)[:100])
                        executed += 1
                    except Exception:
                        log.warning("Teacher action failed", exc_info=True)
            log.info("Teacher executed %d/%d planned actions", executed, len(plan))
            self._teacher_cooldown = 50  # let RL observe results for 50 ticks
            return executed > 0
        except Exception:
            log.warning("Teacher intervention failed", exc_info=True)
            return False

    def _sample_params(self, action_type: int, params: dict):
        """Sample concrete parameter values from policy head logits."""
        import torch
        from torch.distributions import Categorical
        from factorio.action_space import EncodedAction, ActionType

        encoded = EncodedAction(action_type=action_type)

        def _sample(logits_key: str) -> int:
            if logits_key in params:
                dist = Categorical(logits=params[logits_key])
                return dist.sample().item()
            return 0

        if action_type == ActionType.PLACE:
            encoded.entity_id = _sample("entity_logits")
            encoded.dx = _sample("dx_logits")
            encoded.dy = _sample("dy_logits")
            encoded.direction = _sample("direction_logits")
        elif action_type == ActionType.CRAFT:
            encoded.recipe_id = _sample("recipe_logits")
            encoded.count = _sample("count_logits") + 1
        elif action_type == ActionType.RESEARCH:
            encoded.tech_id = _sample("tech_logits")
        elif action_type == ActionType.MOVE:
            encoded.dx = _sample("dx_logits")
            encoded.dy = _sample("dy_logits")
        elif action_type == ActionType.SET_RECIPE:
            encoded.grid_x = _sample("gx_logits")
            encoded.grid_y = _sample("gy_logits")
            encoded.recipe_id = _sample("recipe_logits")
        elif action_type == ActionType.REMOVE:
            encoded.grid_x = _sample("gx_logits")
            encoded.grid_y = _sample("gy_logits")
        elif action_type == ActionType.MINE:
            encoded.dx = _sample("dx_logits")
            encoded.dy = _sample("dy_logits")

        return encoded

    async def ml_tick(self) -> None:
        """Single ML-mode perception -> action cycle."""
        import torch

        # 0. Get state and verify agent has a body
        raw_state = await self.rcon.remote_call("get_state")
        state = parse_state(raw_state)

        if not state.player_alive:
            log.warning("ML tick %d: agent has no body — calling ensure_player", self._tick_count)
            try:
                result = await self.rcon.remote_call("ensure_player")
                log.info("Body check result: %s", str(result)[:200])
                # Re-fetch state after respawn
                raw_state = await self.rcon.remote_call("get_state")
                state = parse_state(raw_state)
                if not state.player_alive:
                    log.error("Agent still has no body after ensure_player — skipping tick")
                    self._tick_count += 1
                    return
            except Exception:
                log.warning("ensure_player failed — skipping tick", exc_info=True)
                self._tick_count += 1
                return

        # 0b. Hybrid teacher: track lesson progress and intervene if stuck
        current_lesson = self._curriculum.get_progress().get("completed", 0)
        if current_lesson != self._teacher_last_lesson:
            self._teacher_last_lesson = current_lesson
            self._teacher_lesson_step_count = 0
        self._teacher_lesson_step_count += 1

        if self._teacher_cooldown > 0:
            self._teacher_cooldown -= 1
        elif self._teacher_lesson_step_count >= self._teacher_stuck_threshold:
            log.info("RL stuck on lesson %d for %d steps — calling LLM teacher",
                     current_lesson, self._teacher_lesson_step_count)
            taught = await self._teacher_intervention(state)
            if taught:
                self._teacher_lesson_step_count = 0  # reset counter
                self._tick_count += 1
                return  # skip RL action this tick, let teacher results settle

        # 0c. Update bridge status so dashboard shows Running
        update_status(True, state.tick, self.cadence.mode)

        # 1. Fetch metrics
        raw_metrics = None
        if self._tick_count % 5 == 0:
            try:
                raw_metrics_str = await self.rcon.remote_call("get_metrics")
                raw_metrics = parse_metrics(raw_metrics_str)
            except Exception:
                log.warning("Metrics fetch failed in ml_tick, skipping")
        self.world_model.update(state, raw_metrics)

        # 2. Encode state
        grid, features = self._encoder.encode(state, raw_metrics)
        grid_t = torch.tensor(grid).unsqueeze(0)
        feat_t = torch.tensor(features).unsqueeze(0)

        # 3. Get action from policy
        mask = self._action_space.get_action_type_mask(state.inventory, self.config.current_phase)
        mask_t = torch.tensor([mask], dtype=torch.bool)
        action_type, log_prob, value, params = self._policy.act(grid_t, feat_t, mask_t)

        # 4. Sample action parameters and decode
        encoded = self._sample_params(action_type.item(), params)
        action_dict = self._action_space.decode_action(encoded)

        # 5. Execute via RCON
        from factorio.action_translator import translate_action
        translated = translate_action(action_dict)
        result = {"success": False}
        if translated.rcon_command:
            try:
                resp = await self.rcon.remote_call("exec_cmd", translated.rcon_command)
                resp_str = str(resp).lower()
                result = {"success": "error" not in resp_str}
                if self._tick_count <= 20 or self._tick_count % 50 == 0:
                    log.info("ML step %d: %s → %s",
                             self._tick_count, translated.description,
                             "OK" if result["success"] else resp_str[:100])
            except Exception:
                log.warning("Action execution failed", exc_info=True)

        # 6. Check curriculum progress (always — even first tick for body check lesson)
        resource_totals = {}
        for r in state.resources:
            resource_totals[r.get("name", "")] = r.get("total_amount", 0)
        flat_state = {
            "inventory": state.inventory,
            "entities": {},
            "research": {"name": state.research_name, "progress": state.research_progress},
            "player": {
                "health": state.player_health,
                "max_health": state.player_max_health,
                "alive": 1 if state.player_alive else 0,
                "has_character": 1 if state.player_has_character else 0,
            },
            "resources": resource_totals,
        }
        # Count entities by name
        for e in state.entities:
            flat_state["entities"][e.name] = flat_state["entities"].get(e.name, 0) + 1
        progress = self._curriculum.check_progress(flat_state)
        lesson_passed = progress.get("lesson_passed", False)
        phase_complete = progress.get("phase_complete", False)

        # 7. Compute reward
        reward = 0.0
        if self._prev_state is not None:
            reward = self._reward.compute(
                self._prev_state, state, result["success"],
                lesson_passed, phase_complete,
            )

        # 8. Store transition
        from factorio.trainer import Transition
        done = phase_complete or self._episode_mgr.is_episode_done()
        self._trajectory_buf.add(Transition(
            grid=grid, features=features,
            action_type=action_type.item(),
            log_prob=log_prob.item(),
            value=value.item(),
            reward=reward, done=done,
        ))
        self._episode_mgr.record_step()
        self._prev_state = state
        self._ml_step_count += 1
        self._tick_count += 1

        # 9. PPO update if enough steps
        self._last_reward = reward
        if self._ml_step_count % self.config.ml_update_every == 0:
            try:
                stats = self._trainer.update(self._trajectory_buf)
                self._trajectory_buf.clear()
                self._last_ppo_stats = stats
                log.info("PPO update: %s", stats)
            except Exception:
                log.warning("PPO update failed", exc_info=True)

        # 9b. Push training metrics to API
        progress = self._curriculum.get_progress()
        update_training_status({
            "mode": "ml",
            "episode": self._episode_mgr.episode_count,
            "step": self._ml_step_count,
            "phase": progress.get("phase", self.config.current_phase),
            "phase_name": progress.get("phase_name", ""),
            "lessons_completed": progress.get("completed", 0),
            "lessons_total": progress.get("total_lessons", 0),
            "last_reward": self._last_reward,
            "policy_loss": self._last_ppo_stats.get("policy_loss"),
            "value_loss": self._last_ppo_stats.get("value_loss"),
            "entropy": self._last_ppo_stats.get("entropy"),
            "total_updates": self._trainer.total_updates,
            "total_episodes": self._trainer.total_episodes,
            "buffer_size": len(self._trajectory_buf),
        })

        # 10. Episode end check
        if done:
            self._trainer.total_episodes += 1
            if self._trainer.total_episodes % self.config.ml_checkpoint_every == 0:
                try:
                    self._trainer.save_checkpoint(self._trainer.total_episodes)
                except Exception:
                    log.warning("Checkpoint save failed", exc_info=True)

            # Phase advancement
            if phase_complete:
                from factorio.action_space import ActionSpace
                if self._curriculum.advance_phase():
                    new_phase = self._curriculum._phase
                    log.info("Advancing to phase %d", new_phase)
                    self._encoder.set_phase(new_phase)
                    self._action_space = ActionSpace(phase=new_phase)
                    self._reward.set_phase(new_phase)
                    self._reward.reset_normalizer()
                    self._episode_mgr.set_phase(new_phase)

            await self._episode_mgr.reset()
            self._prev_state = None

    async def run(self) -> None:
        """Main loop — connect, then tick at cadence interval."""
        self._running = True
        log.info("Factorio bridge starting...")

        if not await self.connect_with_retry():
            log.error("Failed to connect to RCON, exiting")
            return

        log.info("Bridge connected, entering tick loop")

        # Ensure agent player exists (both modes)
        try:
            result = await self.rcon.remote_call("ensure_player")
            log.info("Player init: %s", str(result)[:200])
        except Exception as e:
            log.warning("Player init failed: %s", e)

        if self.config.mode == "ml":
            await self._episode_mgr.set_game_speed(self.config.game_speed)
            await self._episode_mgr.reset()
            log.info("ML training ready — phase %d, game speed %dx",
                     self.config.current_phase, self.config.game_speed)

        while self._running:
            if self.config.mode == "ml":
                await self.ml_tick()
                # ML mode: use dedicated tick delay (default 0 = max throughput)
                # instead of cadence system (designed for slow LLM brain)
                ml_delay = self.config.ml_tick_delay_ms / 1000.0
                if ml_delay > 0:
                    await asyncio.sleep(ml_delay)
                else:
                    await asyncio.sleep(0)  # yield to event loop (API server, etc.)
            else:
                await self.tick()
                interval = self.cadence.get_interval_secs()
                await asyncio.sleep(interval)

    def stop(self) -> None:
        """Signal the bridge to stop."""
        self._running = False


def _run_api_server(app, port: int) -> None:
    """Run Flask API in a daemon thread."""
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True,
            use_reloader=False)


def main():
    """Entry point — load config, start API thread, run bridge loop."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    config = load_factorio_config()
    if not config.enabled:
        log.info("Factorio bridge is disabled in fleet.toml")
        return
    if config.role != "host":
        log.info("This node is a compute-only role, not starting bridge")
        return

    bridge = FactorioBridge(config)

    # Start localhost API server
    api_app = create_api(bridge.world_model, bridge.command_queue, bridge.brain)
    api_thread = threading.Thread(
        target=_run_api_server, args=(api_app, config.bridge_port),
        daemon=True, name="factorio-api",
    )
    api_thread.start()
    log.info("Bridge API running on http://127.0.0.1:%d", config.bridge_port)

    # Run bridge loop
    try:
        asyncio.run(bridge.run())
    except KeyboardInterrupt:
        log.info("Bridge interrupted")
    finally:
        bridge.stop()


if __name__ == "__main__":
    main()
