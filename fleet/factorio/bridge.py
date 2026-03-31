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
from factorio.bridge_api import create_api, update_status, store_result, update_training_status, update_player_position
from factorio.agent_brain import AgentBrain
from factorio.reward import _PACK_COMPLETE_BONUS, _PACK_ABORT_PENALTY

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
        self._teacher_stuck_threshold = 100  # steps on same lesson before LLM help (was 500)
        self._teacher_lesson_step_count = 0
        self._teacher_last_lesson = -1
        self._teacher_cooldown = 0  # skip N ticks after teacher intervention
        self._teacher_pending: asyncio.Task | None = None  # background LLM task
        self._teacher_actions: list = []  # queued actions from teacher

        if self.config.mode == "ml":
            from factorio.state_encoder import StateEncoder
            from factorio.ml_policy import FactorioPolicy
            from factorio.action_space import ActionSpace, ActionType
            from factorio.reward import RewardComputer
            from factorio.trainer import PPOTrainer, TrajectoryBuffer
            from factorio.episode_manager import EpisodeManager
            from factorio.curriculum_manager import CurriculumManager
            from factorio.spatial_memory import SpatialMemory

            # Per-agent spatial memory + reward — prevents convergence to same spot
            num_agents = getattr(config, 'num_agents', 1)
            from factorio.economic_scorer import EconomicScorer
            self._economic_scorer = EconomicScorer()
            self._agent_spatial: dict[int, SpatialMemory] = {}
            self._agent_reward: dict[int, RewardComputer] = {}
            self._agent_positions: dict[int, tuple[float, float]] = {}
            for aid in range(1, num_agents + 1):
                self._agent_spatial[aid] = SpatialMemory()
                self._agent_reward[aid] = RewardComputer(
                    phase=config.current_phase,
                    spatial_memory=self._agent_spatial[aid],
                    economic_scorer=self._economic_scorer,
                )
            # Shared encoder (spatial memory passed per-call) and policy
            self._spatial_memory = self._agent_spatial.get(1, SpatialMemory())  # compat
            self._encoder = StateEncoder(
                phase=config.current_phase,
                spatial_memory=self._spatial_memory,
                num_agents=num_agents,
            )
            self._action_space = ActionSpace(phase=config.current_phase)
            self._policy = FactorioPolicy(
                grid_channels=5, grid_size=64,
                feature_dim=self._encoder.feature_dim,
                num_action_types=len(ActionType),  # 10 with PLACE_NEAR
                num_entities=self._action_space.num_entity_types,
                num_recipes=self._action_space.num_recipe_types,
                num_techs=self._action_space.num_tech_types,
                world_grid_channels=self._encoder.world_grid_channels,
            )
            self._reward = self._agent_reward.get(1, RewardComputer(
                phase=config.current_phase,
                spatial_memory=self._spatial_memory,
                economic_scorer=self._economic_scorer,
            ))  # compat fallback
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

            from factorio.pack_registry import PackRegistry
            from factorio.pack_executor import PackExecutor

            self._pack_registry = PackRegistry()
            packs_dir = Path(__file__).parent / "packs"
            self._pack_registry.load_packs(packs_dir / "hardcoded")
            self._pack_registry.load_stamps(packs_dir / "blueprints")
            self._pack_registry.load_packs(packs_dir / "learned")
            log.info("PackRegistry loaded: %d items", len(self._pack_registry._items))

            from factorio.pack_recorder import PackRecorder
            self._pack_recorder = PackRecorder(max_length=100)

            self._pack_executors: dict[int, PackExecutor] = {}
            self._pack_pending_transition: dict[int, dict] = {}
            self._pack_prev_results: dict[int, dict] = {}
            self._insert_count: int = 0
            self._production_snapshot: dict = {}
            self._last_checkpoint_save: int = 0

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
            log.warning("LLM tick %d: agent has no body — calling ensure_agent",
                        self._tick_count)
            try:
                await self.rcon.remote_call("ensure_agent")
                state_raw = await self.rcon.remote_call("get_state")
                state = parse_state(state_raw)
                if not state.player_alive:
                    log.error("Agent still has no body after ensure_agent — skipping tick")
                    return
            except Exception:
                log.warning("ensure_agent failed — skipping tick", exc_info=True)
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

    async def _teacher_generate_plan(self, state) -> list[dict]:
        """Background task: ask LLM to generate an action plan.

        Runs Ollama inference in a thread executor so it doesn't block
        the RL tick loop. Returns a list of action dicts (or empty).

        If the dependency resolver can fully satisfy the goal with craft
        actions alone, the LLM call is bypassed entirely.
        """
        objective = self._curriculum.get_current_objective()
        hint = objective.get("hint", "")
        lesson = objective.get("lesson_name", "?")
        log.info("Teacher thinking about lesson '%s' — hint: %s", lesson, hint)

        # --- Dependency resolver shortcut ---
        try:
            from factorio.dependency_resolver import (
                resolve, parse_criteria_to_items, entities_to_counts,
            )
            from factorio.recipe_dag import RecipeDAG

            criteria = objective.get("criteria", "")
            goals = parse_criteria_to_items(criteria)
            if goals:
                dag = RecipeDAG(str(Path(__file__).resolve().parent / "data" / "recipes.json"))
                entity_counts = entities_to_counts(state.entities)
                # Try to resolve each goal item
                all_craft_only = True
                combined_actions: list[dict] = []
                for item, amount in goals.items():
                    plan = resolve(item, amount, dict(state.inventory),
                                   entity_counts, dag)
                    if plan.is_complete():
                        actions = plan.to_actions()
                        # Check if all actions are craft (no acquire/smelt/build)
                        if any(a["action"] not in ("craft",) for a in actions):
                            all_craft_only = False
                        combined_actions.extend(actions)
                    else:
                        all_craft_only = False

                if all_craft_only and combined_actions:
                    log.info("Resolver fully satisfied lesson '%s' with %d craft "
                             "actions — bypassing LLM", lesson, len(combined_actions))
                    return combined_actions

                # Partial resolution — inject summary into brain context for LLM
                if combined_actions:
                    # Build a summary from the last plan (best effort)
                    summary_lines = []
                    for a in combined_actions:
                        summary_lines.append(
                            f"- {a['action']}: {a.get('recipe') or a.get('item', '?')} "
                            f"x{a.get('count', 1)}")
                    summary = "\n".join(summary_lines)
                    self.brain.add_context("dependency_plan", summary)
                    log.info("Resolver injected %d-action plan into brain context",
                             len(combined_actions))
        except Exception:
            log.warning("Dependency resolver failed — falling through to LLM",
                        exc_info=True)

        # --- LLM fallback ---
        try:
            # Sync curriculum state so the brain sees the current lesson
            self.brain.curriculum._phase = self._curriculum._phase
            self.brain.curriculum._tracker = self._curriculum._tracker
            self.brain.curriculum._lessons = self._curriculum._lessons
            self.brain.curriculum._meta = self._curriculum._meta

            # Generate plan via Ollama (blocking call, runs in executor)
            plan = await asyncio.get_event_loop().run_in_executor(
                None, self.brain._generate_plan, state)
            if plan:
                log.info("Teacher generated %d actions for '%s'", len(plan), lesson)
            else:
                log.warning("Teacher produced no plan for '%s'", lesson)
            return plan or []
        except Exception:
            log.warning("Teacher plan generation failed", exc_info=True)
            return []

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
        elif action_type == ActionType.INSERT:
            encoded.dx = _sample("dx_logits")
            encoded.dy = _sample("dy_logits")
            encoded.recipe_id = _sample("recipe_logits")  # item selector
            encoded.count = _sample("count_logits") + 1
        elif action_type == ActionType.PLACE_NEAR:
            # Reuse PLACE entity selection; recipe selects resource type
            encoded.entity_id = _sample("entity_logits")
            encoded.recipe_id = _sample("recipe_logits")
        elif action_type == ActionType.PACK or action_type == ActionType.STAMP:
            encoded.entity_id = _sample("pack_logits")   # reuse entity_id for pack_id
            encoded.dx = _sample("offset_dx_logits")
            encoded.dy = _sample("offset_dy_logits")

        return encoded

    async def ml_tick(self) -> None:
        """Run one ML tick for ALL agents, then advance counters."""
        import torch

        # 0e. Drain human command queue (plans, demonstrations) — once per tick
        if not hasattr(self, '_pending_demo_actions'):
            self._pending_demo_actions = []
            self._pending_demo_cmd_id = None

        if not self._pending_demo_actions and not self.command_queue.empty():
            try:
                cmd = self.command_queue.get_nowait()
                from factorio.action_translator import translate_batch
                translated = translate_batch(cmd.get("actions", []))
                self._pending_demo_actions = [ta for ta in translated if ta.rcon_command]
                self._pending_demo_cmd_id = cmd.get("id")
                log.info("Demo queue: %d actions loaded", len(self._pending_demo_actions))
            except Exception:
                log.warning("Queue command load failed", exc_info=True)

        if self._pending_demo_actions:
            ta = self._pending_demo_actions.pop(0)
            try:
                rcon_cmd = ta.rcon_command
                if rcon_cmd.startswith("/biged-cmd "):
                    rcon_cmd = rcon_cmd[len("/biged-cmd "):]
                resp = await self.rcon.remote_call("exec_cmd", rcon_cmd)
                log.info("Demo step: %s -> %s", ta.description, str(resp)[:100])
            except Exception:
                log.warning("Demo action failed: %s", ta.description, exc_info=True)
            if not self._pending_demo_actions and self._pending_demo_cmd_id:
                store_result(self._pending_demo_cmd_id, {"results": "executed"})
                self._pending_demo_cmd_id = None
            return  # Demo action takes priority this tick

        # Run ML tick for each agent
        num_agents = getattr(self.config, 'num_agents', 1)
        for agent_id in range(1, num_agents + 1):
            await self._ml_tick_agent(agent_id)

    async def _ml_tick_agent(self, agent_id: int = 1) -> None:
        """Single ML-mode perception -> action cycle for one agent."""
        import torch

        # 0. Get state and verify agent has a body
        raw_state = await self.rcon.remote_call("get_state", str(agent_id))
        state = parse_state(raw_state)

        if not state.player_alive:
            log.warning("ML tick %d agent %d: no body — calling ensure_agent",
                        self._tick_count, agent_id)
            try:
                result = await self.rcon.remote_call("ensure_agent", str(agent_id))
                log.info("Agent %d body check: %s", agent_id, str(result)[:200])
                raw_state = await self.rcon.remote_call("get_state", str(agent_id))
                state = parse_state(raw_state)
                if not state.player_alive:
                    log.error("Agent %d still has no body — skipping", agent_id)
                    self._tick_count += 1
                    return
            except Exception:
                log.warning("ensure_agent(%d) failed — skipping", agent_id, exc_info=True)
                self._tick_count += 1
                return

        # 0a. Periodic resupply — creative mode: stock agents with all building materials
        if self._tick_count % 200 == 0:
            try:
                resupply_lua = (
                    f'/c local chars = game.surfaces[1].find_entities_filtered{{type="character"}}; '
                    f'for _, c in pairs(chars) do '
                    f'  local inv = c.get_inventory(defines.inventory.character_main); '
                    f'  if inv then '
                    f'    local items = {{["stone-furnace"]=50,["burner-mining-drill"]=50,'
                    f'["electric-mining-drill"]=50,["assembling-machine-1"]=50,'
                    f'["assembling-machine-2"]=20,["transport-belt"]=200,'
                    f'["fast-transport-belt"]=100,["inserter"]=100,["fast-inserter"]=50,'
                    f'["long-handed-inserter"]=50,["burner-inserter"]=50,'
                    f'["small-electric-pole"]=100,["medium-electric-pole"]=50,'
                    f'["substation"]=20,["pipe"]=100,["pipe-to-ground"]=50,'
                    f'["offshore-pump"]=10,["boiler"]=20,["steam-engine"]=20,'
                    f'["lab"]=10,["radar"]=10,["wooden-chest"]=50,["iron-chest"]=50,'
                    f'["splitter"]=50,["underground-belt"]=50,'
                    f'["coal"]=200,["iron-plate"]=200,["copper-plate"]=200,'
                    f'["steel-plate"]=100,["iron-gear-wheel"]=100,["copper-cable"]=100,'
                    f'["electronic-circuit"]=100,["solar-panel"]=50,["accumulator"]=50,'
                    f'["solid-fuel"]=100}}; '
                    f'    for name, count in pairs(items) do '
                    f'      if inv.get_item_count(name) < count then '
                    f'        inv.insert{{name=name, count=count - inv.get_item_count(name)}} '
                    f'      end '
                    f'    end '
                    f'  end '
                    f'end'
                )
                await self.rcon.command(resupply_lua)
            except Exception:
                log.warning("Resupply failed for agent %d", agent_id, exc_info=True)

        # 0a2. Spawn leash — keep agent near resources during early training
        #     Uses same phase-based radius as the per-action leash (line ~578)
        _LEASH_RADIUS = {1: 30, 2: 60, 3: 200, 4: 500}
        leash_r = _LEASH_RADIUS.get(self.config.current_phase, 200)
        pos = state.player_position
        if pos:
            px = pos.get("x", 0) if isinstance(pos, dict) else getattr(pos, "x", 0)
            py = pos.get("y", 0) if isinstance(pos, dict) else getattr(pos, "y", 0)
            if abs(px) > leash_r or abs(py) > leash_r:
                try:
                    cmd = json.dumps({"action": "move", "position": {"x": 0, "y": 0},
                                      "agent_id": agent_id})
                    await self.rcon.remote_call("exec_cmd", cmd)
                    log.info("Spawn leash: teleported agent %d from (%d,%d) back to origin (leash=%d)",
                             agent_id, int(px), int(py), leash_r)
                except Exception:
                    log.warning("Spawn leash teleport failed for agent %d", agent_id, exc_info=True)

        # 0b. Update per-agent spatial memory from current state
        agent_mem = self._agent_spatial.get(agent_id, self._spatial_memory)
        agent_mem.update_from_state(state, state.tick)
        # Track agent position for proximity awareness
        px = state.player_position.get("x", 0.0) if isinstance(state.player_position, dict) else 0.0
        py = state.player_position.get("y", 0.0) if isinstance(state.player_position, dict) else 0.0
        self._agent_positions[agent_id] = (px, py)
        update_player_position(px, py)

        # Pack executor for this agent
        if agent_id not in self._pack_executors:
            from factorio.pack_executor import PackExecutor
            self._pack_executors[agent_id] = PackExecutor()
        _executor = self._pack_executors[agent_id]

        # 0c. Hybrid teacher: track lesson progress and intervene if stuck
        #     LLM runs in background — RL keeps ticking while teacher thinks.
        #     When teacher plan arrives, execute actions over the next few ticks.
        current_lesson = self._curriculum.get_progress().get("completed", 0)
        if current_lesson != self._teacher_last_lesson:
            self._teacher_last_lesson = current_lesson
            self._teacher_lesson_step_count = 0
            # Lesson advanced — cancel any pending teacher task
            if self._teacher_pending and not self._teacher_pending.done():
                self._teacher_pending.cancel()
                self._teacher_pending = None
            self._teacher_actions.clear()
        self._teacher_lesson_step_count += 1

        # Check if background teacher finished
        if self._teacher_pending and self._teacher_pending.done():
            try:
                plan = self._teacher_pending.result()
                if plan:
                    self._teacher_actions = list(plan)
                    log.info("Teacher plan ready: %d actions queued", len(plan))
            except Exception:
                log.warning("Teacher background task failed", exc_info=True)
            self._teacher_pending = None

        # Execute one queued teacher action per tick (interleaved with RL)
        if self._teacher_actions:
            from factorio.action_translator import translate_action
            action_dict = self._teacher_actions.pop(0)
            translated = translate_action(action_dict)
            if translated.rcon_command:
                cmd = translated.rcon_command
                if cmd.startswith("/biged-cmd "):
                    cmd = cmd[len("/biged-cmd "):]
                try:
                    resp = await self.rcon.remote_call("exec_cmd", cmd)
                    log.info("Teacher action: %s -> %s",
                             translated.description, str(resp)[:100])
                except Exception:
                    log.warning("Teacher action failed", exc_info=True)
            self._teacher_cooldown = 5  # brief pause after each teacher action
            # Don't return — RL still gets to act this tick too

        # Launch teacher in background if stuck (non-blocking)
        if self._teacher_cooldown > 0:
            self._teacher_cooldown -= 1
        elif (self._teacher_lesson_step_count >= self._teacher_stuck_threshold
              and self._teacher_pending is None):
            log.info("RL stuck on lesson %d for %d steps — launching LLM teacher (background)",
                     current_lesson, self._teacher_lesson_step_count)
            self._teacher_lesson_step_count = 0  # reset so we don't spam
            self._teacher_pending = asyncio.create_task(
                self._teacher_generate_plan(state))

        # 0d. Update bridge status so dashboard shows Running
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

        # If pack is in-flight, execute next step (skip policy)
        if _executor.is_active:
            prev_result = getattr(self, '_pack_prev_results', {}).get(agent_id, {"success": True})
            next_action = _executor.next_step(prev_result)
            if next_action is None:
                # Pack finished — compute cumulative reward and store PPO transition
                pack_completed = _executor.completed
                pack_aborted = _executor.abort_reason is not None
                cum_reward = _executor.cumulative_reward
                if pack_completed:
                    cum_reward += _PACK_COMPLETE_BONUS
                elif pack_aborted:
                    cum_reward += _PACK_ABORT_PENALTY
                saved = self._pack_pending_transition.pop(agent_id, None)
                if saved:
                    from factorio.trainer import Transition
                    self._trajectory_buf.add(Transition(
                        grid=saved["grid"], features=saved["features"],
                        action_type=saved["action_type"],
                        log_prob=saved["log_prob"],
                        value=saved["value"],
                        reward=cum_reward, done=False,
                        world_grid=saved.get("world_grid"),
                        action_mask=saved.get("action_mask"),
                    ))
                self._prev_state = state
                self._tick_count += 1
                return
            else:
                # Execute primitive action from pack
                next_action["agent_id"] = agent_id
                from factorio.action_translator import translate_action as _translate
                translated = _translate(next_action)
                exec_result = {"success": False}
                if translated.rcon_command:
                    try:
                        cmd = translated.rcon_command
                        if cmd.startswith("/biged-cmd "):
                            cmd = cmd[len("/biged-cmd "):]
                        resp = await self.rcon.remote_call("exec_cmd", cmd)
                        resp_str = str(resp).lower()
                        exec_result = {"success": "error" not in resp_str}
                    except Exception:
                        log.warning("Pack step failed", exc_info=True)
                self._pack_prev_results[agent_id] = exec_result
                # Accumulate step reward (not stored in PPO buffer)
                other_positions = [
                    pos for aid, pos in self._agent_positions.items() if aid != agent_id
                ]
                agent_rw = self._agent_reward.get(agent_id, self._reward)
                if self._prev_state is not None:
                    raw_metrics_for_pack = None
                    step_reward = agent_rw.compute(
                        self._prev_state, state, exec_result["success"],
                        False, False, metrics=raw_metrics_for_pack,
                        action_type=next_action.get("action_type_int", 0),
                        other_agent_positions=other_positions,
                    )
                    _executor.accumulate_reward(step_reward)
                self._prev_state = state
                self._tick_count += 1
                return

        # 2. Encode state (local grid + world minimap + features)
        #    Use per-agent spatial memory and inject agent identity + peer positions
        other_positions = [
            pos for aid, pos in self._agent_positions.items() if aid != agent_id
        ]
        pack_progress = _executor.progress if _executor.is_active else 0.0
        grid, world_grid, features = self._encoder.encode(
            state, raw_metrics,
            spatial_memory=agent_mem,
            agent_id=agent_id,
            other_agent_positions=other_positions,
            pack_progress=pack_progress,
        )
        grid_t = torch.from_numpy(grid).unsqueeze(0).float()
        world_t = torch.from_numpy(world_grid).unsqueeze(0).float()
        feat_t = torch.from_numpy(features).unsqueeze(0).float()

        # 3. Get action from policy (with lesson-aware masking)
        current_lesson = self._curriculum.current_lesson_index()
        mask = self._action_space.get_action_type_mask(
            state.inventory, self.config.current_phase, current_lesson)
        # Enable PACK/STAMP only when packs are actually available and affordable
        from factorio.action_space import ActionType as _AT
        pack_mask_list = self._pack_registry.get_pack_mask(
            phase=self.config.current_phase, inventory=state.inventory)
        has_any_pack = any(pack_mask_list)
        if has_any_pack:
            mask[_AT.PACK.value] = 1
            mask[_AT.STAMP.value] = 1
        mask_t = torch.tensor([mask], dtype=torch.bool)
        action_type, log_prob, value, params = self._policy.act(grid_t, feat_t, mask_t, world_grid=world_t)

        # Apply recipe mask for CRAFT/INSERT — block irrelevant recipes in Phase 1
        from factorio.action_space import ActionType as _AT
        recipe_mask = self._action_space.get_recipe_mask(
            self.config.current_phase, current_lesson)
        if action_type.item() in (_AT.CRAFT.value, _AT.INSERT.value):
            if "recipe_logits" in params:
                import torch as _torch
                rmask = _torch.tensor([recipe_mask], dtype=_torch.bool)
                params["recipe_logits"] = params["recipe_logits"].masked_fill(~rmask, -1e8)

        # 4. Sample action parameters and decode
        encoded = self._sample_params(action_type.item(), params)
        action_dict = self._action_space.decode_action(encoded)

        # 4b. Convert relative offsets to absolute world coordinates
        #     Policy outputs [-5, +5] relative to player.
        #     place_near_resource doesn't use position (Lua finds the best spot).
        if "position" in action_dict and state.player_position and action_dict.get("action") != "place_near_resource":
            px = state.player_position.get("x", 0) if isinstance(state.player_position, dict) else getattr(state.player_position, "x", 0)
            py = state.player_position.get("y", 0) if isinstance(state.player_position, dict) else getattr(state.player_position, "y", 0)
            # All actions use 1:1 offset mapping (no scaling).
            # The 11 dx/dy bins map to [-5, +5] tile offsets from player position.
            # 2x scaling was causing odd-offset positions to be unreachable.
            abs_x = action_dict["position"]["x"] + round(px)
            abs_y = action_dict["position"]["y"] + round(py)

            # Leash: FAIL moves outside radius instead of silently clamping.
            # Clamping erased the failure signal — agent never learned boundaries.
            _LEASH = {1: 30, 2: 60, 3: 200, 4: 500}
            max_r = _LEASH.get(self.config.current_phase, 200)
            if abs(abs_x) > max_r or abs(abs_y) > max_r:
                if action_dict.get("action") == "move":
                    # Reject the move — give the policy a clear failure signal
                    from factorio.trainer import Transition
                    agent_rw = self._agent_reward.get(agent_id, self._reward)
                    fail_reward = agent_rw.compute(
                        self._prev_state if self._prev_state else state,
                        state, False, False, False, metrics=raw_metrics,
                        action_type=action_type.item(),
                        other_agent_positions=other_positions,
                    ) if self._prev_state else -0.02
                    self._trajectory_buf.add(Transition(
                        grid=grid, features=features,
                        action_type=action_type.item(),
                        log_prob=log_prob.item(), value=value.item(),
                        reward=fail_reward, done=False,
                        world_grid=world_grid,
                        action_mask=mask,
                    ))
                    self._prev_state = state
                    self._tick_count += 1
                    return
                else:
                    # Non-move actions: clamp position (placement near edge is OK)
                    abs_x = max(-max_r, min(max_r, abs_x))
                    abs_y = max(-max_r, min(max_r, abs_y))

            action_dict["position"]["x"] = abs_x
            action_dict["position"]["y"] = abs_y

        # Handle PACK/STAMP action selection
        from factorio.action_space import ActionType as _AT
        action_type_val = action_type.item()
        if action_type_val == _AT.PACK.value or action_type_val == _AT.STAMP.value:
            pack_id = encoded.entity_id
            if pack_id < len(self._pack_registry._items):
                is_stamp = self._pack_registry.is_stamp(pack_id)
                item = self._pack_registry.get_by_id(pack_id)

                if is_stamp:
                    # STAMP: single RCON call for blueprint placement
                    dx = encoded.dx - 5
                    dy = encoded.dy - 5
                    px = state.player_position.get("x", 0) if isinstance(state.player_position, dict) else 0
                    py = state.player_position.get("y", 0) if isinstance(state.player_position, dict) else 0
                    stamp_cmd = json.dumps({
                        "blueprint": item.blueprint_string,
                        "position": {"x": px + dx, "y": py + dy},
                        "agent_id": agent_id,
                    })
                    stamp_result = {"success": False}
                    try:
                        resp = await self.rcon.remote_call("biged-blueprint", stamp_cmd)
                        stamp_result = {"success": True} if resp else {"success": False}
                    except Exception:
                        log.warning("Blueprint stamp failed", exc_info=True)
                    # Compute reward and store transition
                    agent_rw = self._agent_reward.get(agent_id, self._reward)
                    stamp_reward = 0.0
                    if self._prev_state is not None:
                        stamp_reward = agent_rw.compute(
                            self._prev_state, state, stamp_result["success"],
                            False, False, metrics=raw_metrics,
                            action_type=action_type_val,
                            other_agent_positions=other_positions,
                            pack_completed=stamp_result["success"],
                            pack_aborted=not stamp_result["success"],
                        )
                    from factorio.trainer import Transition
                    self._trajectory_buf.add(Transition(
                        grid=grid, features=features,
                        action_type=action_type_val,
                        log_prob=log_prob.item(), value=value.item(),
                        reward=stamp_reward, done=False,
                        world_grid=world_grid,
                        action_mask=mask,
                    ))
                    self._prev_state = state
                    self._tick_count += 1
                    return
                else:
                    # PACK: start multi-tick execution
                    dx = encoded.dx - 5
                    dy = encoded.dy - 5
                    first_action = _executor.start(item, offset=(dx, dy))
                    # Save transition for deferred storage when pack completes
                    self._pack_pending_transition[agent_id] = {
                        "grid": grid, "features": features,
                        "action_type": action_type_val,
                        "log_prob": log_prob.item(), "value": value.item(),
                        "world_grid": world_grid,
                        "action_mask": mask,
                    }
                    # Execute first primitive
                    first_action["agent_id"] = agent_id
                    from factorio.action_translator import translate_action as _translate
                    translated = _translate(first_action)
                    exec_result = {"success": False}
                    if translated.rcon_command:
                        try:
                            cmd = translated.rcon_command
                            if cmd.startswith("/biged-cmd "):
                                cmd = cmd[len("/biged-cmd "):]
                            resp = await self.rcon.remote_call("exec_cmd", cmd)
                            resp_str = str(resp).lower()
                            exec_result = {"success": "error" not in resp_str}
                        except Exception:
                            log.warning("Pack first step failed", exc_info=True)
                    self._pack_prev_results[agent_id] = exec_result
                    self._prev_state = state
                    self._tick_count += 1
                    return
            else:
                log.warning("Invalid pack_id %d (registry has %d items)",
                            pack_id, len(self._pack_registry._items))
                # Skip this tick — don't fall through with a "pack"/"stamp" action name
                self._prev_state = state
                self._tick_count += 1
                return

        # 5. Execute via RCON (inject agent_id so exec_cmd uses the right character)
        action_dict["agent_id"] = agent_id
        from factorio.action_translator import translate_action
        translated = translate_action(action_dict)
        result = {"success": False}
        if translated.rcon_command:
            try:
                # Strip /biged-cmd prefix — exec_cmd expects raw JSON
                cmd = translated.rcon_command
                if cmd.startswith("/biged-cmd "):
                    cmd = cmd[len("/biged-cmd "):]
                resp = await self.rcon.remote_call("exec_cmd", cmd)
                resp_str = str(resp).lower()
                result = {"success": "error" not in resp_str}
                # Always log place/craft failures for debugging
                if not result["success"] and translated.action_type in ("place", "craft"):
                    log.warning("ML step %d FAILED %s: %s",
                                self._tick_count, translated.description, resp_str[:200])
                elif self._tick_count <= 50 or self._tick_count % 25 == 0:
                    log.info("ML step %d: %s -> %s",
                             self._tick_count, translated.description,
                             "OK" if result["success"] else resp_str[:100])
            except Exception:
                log.warning("Action execution failed", exc_info=True)

        # 5b. Record primitive actions for learned pack discovery
        if result.get("success") and action_dict.get("action") in (
            "place", "craft", "insert", "mine", "set_recipe", "remove"
        ):
            self._pack_recorder.record(action_dict)

        # 5c. Track successful inserts for curriculum
        if result.get("success") and action_dict.get("action") == "insert":
            self._insert_count += 1
        # Track production from metrics
        if raw_metrics and hasattr(raw_metrics, 'total_produced'):
            self._production_snapshot = dict(raw_metrics.total_produced)

        # 5d. Auto-save before checkpoint attempt for learned pack replay
        checkpoint_id = self._curriculum.checkpoint
        if checkpoint_id != self._last_checkpoint_save:
            try:
                save_name = f"checkpoint_{checkpoint_id}_pre"
                await self.rcon.command(f'/c game.auto_save("{save_name}")')
                self._last_checkpoint_save = checkpoint_id
                log.info("Auto-saved game state as '%s'", save_name)
            except Exception:
                log.warning("Checkpoint auto-save failed", exc_info=True)

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
            # Extra tracking for curriculum criteria
            "inserts": self._insert_count,
            "produced": self._production_snapshot,
        }
        # Count entities by name
        for e in state.entities:
            flat_state["entities"][e.name] = flat_state["entities"].get(e.name, 0) + 1
        progress = self._curriculum.check_progress(flat_state)
        lesson_passed = progress.get("lesson_passed", False)
        phase_complete = progress.get("phase_complete", False)

        # 7. Compute reward (per-agent reward computer + proximity penalty)
        agent_rw = self._agent_reward.get(agent_id, self._reward)
        reward = 0.0
        if self._prev_state is not None:
            # Check if agent is near ore (for lesson 2 shaped reward)
            near_ore = False
            if current_lesson == 2 and agent_mem:
                for rtype in ("iron-ore", "copper-ore", "coal", "stone"):
                    try:
                        result_ore = agent_mem.nearest_resource(px, py, rtype)
                        if result_ore is not None and result_ore[1] < 3.0:
                            near_ore = True
                            break
                    except Exception:
                        log.warning("near_ore check failed for %s", rtype, exc_info=True)
            reward = agent_rw.compute(
                self._prev_state, state, result["success"],
                lesson_passed, phase_complete,
                metrics=raw_metrics,
                action_type=action_type.item(),
                other_agent_positions=other_positions,
                lesson_index=current_lesson,
                near_ore=near_ore,
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
            world_grid=world_grid,
            action_mask=mask,
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
                # Try to extract a learned pack from recent actions
                candidate = self._pack_recorder.on_checkpoint_complete(self._curriculum.checkpoint)
                if candidate:
                    slot = self._pack_registry.promote_learned(candidate)
                    if slot is not None:
                        log.info("Promoted learned pack '%s' to slot %d", candidate.name, slot)
                        packs_dir = Path(__file__).parent / "packs" / "learned"
                        self._pack_registry.save_learned(packs_dir)
                self._pack_recorder.clear()

                from factorio.action_space import ActionSpace
                if self._curriculum.advance_phase():
                    new_phase = self._curriculum._phase
                    log.info("Advancing to phase %d", new_phase)
                    self._encoder.set_phase(new_phase)
                    self._action_space = ActionSpace(phase=new_phase)
                    self._reward.set_phase(new_phase)
                    self._reward.reset_normalizer()
                    for agent_rw in self._agent_reward.values():
                        agent_rw.set_phase(new_phase)
                        agent_rw.reset_normalizer()
                    self._episode_mgr.set_phase(new_phase)

            await self._episode_mgr.reset()

            # Survey wide area for spatial memory after reset
            try:
                survey_lua = (
                    '/c local s=game.get_surface("nauvis"); local out={}; '
                    'for _,r in pairs(s.find_entities_filtered{type="resource", '
                    'position={0,0}, radius=200}) do '
                    'out[#out+1]={name=r.name, x=math.floor(r.position.x), '
                    'y=math.floor(r.position.y), amount=r.amount} end; '
                    'rcon.print(game.helpers.table_to_json(out))'
                )
                survey_raw = await self.rcon.command(survey_lua)
                import json as _json
                survey_data = _json.loads(survey_raw)
                self._spatial_memory.update_from_survey(survey_data)
                self._spatial_memory.clear_entities_in_radius((0, 0), 200)
                log.info("Spatial memory survey: %d resources loaded", len(survey_data))
            except Exception:
                log.warning("Post-reset spatial survey failed", exc_info=True)

            self._prev_state = None

    async def run(self) -> None:
        """Main loop — connect, then tick at cadence interval."""
        self._running = True
        log.info("Factorio bridge starting...")

        if not await self.connect_with_retry():
            log.error("Failed to connect to RCON, exiting")
            return

        log.info("Bridge connected, entering tick loop")

        # Remove crash-site wreckage (respawns on every new save)
        try:
            await self.rcon.command(
                '/c for _, e in pairs(game.surfaces[1].find_entities_filtered'
                '{name={"crash-site-spaceship",'
                '"crash-site-spaceship-wreck-small-1","crash-site-spaceship-wreck-small-2",'
                '"crash-site-spaceship-wreck-small-3","crash-site-spaceship-wreck-small-4",'
                '"crash-site-spaceship-wreck-small-5","crash-site-spaceship-wreck-small-6",'
                '"crash-site-spaceship-wreck-big-1","crash-site-spaceship-wreck-big-2",'
                '"crash-site-chest-1","crash-site-chest-2"}}) do e.destroy() end'
            )
            log.info("Crash-site wreckage cleared")
        except Exception:
            log.warning("Failed to clear crash-site wreckage", exc_info=True)

        # Enable creative/sandbox mode — research all, cheat mode, fast crafting
        try:
            await self.rcon.command(
                '/c game.forces["player"].research_all_technologies(); '
                'game.forces["player"].manual_crafting_speed_modifier = 1000; '
                'game.forces["player"].manual_mining_speed_modifier = 1000'
            )
            log.info("Creative mode enabled: all tech researched, instant crafting")
        except Exception:
            log.warning("Failed to enable creative mode", exc_info=True)

        # Ensure all agent characters exist
        num_agents = getattr(self.config, 'num_agents', 1)
        for aid in range(1, num_agents + 1):
            try:
                result = await self.rcon.remote_call("ensure_agent", str(aid))
                log.info("Agent %d init: %s", aid, str(result)[:200])
            except Exception as e:
                log.warning("Agent %d init failed: %s", aid, e)

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
    spatial_mem = getattr(bridge, '_spatial_memory', None)
    api_app = create_api(bridge.world_model, bridge.command_queue, bridge.brain, rcon=bridge.rcon, spatial_memory=spatial_mem)
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
