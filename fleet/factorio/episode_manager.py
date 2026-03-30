"""Episode lifecycle manager for the Factorio RL training loop.

Handles soft/hard reset between training episodes, game speed control,
step counting, and phase-appropriate starting item distribution.
"""
import logging
import time
from typing import Optional

log = logging.getLogger("biged.factorio.episode_manager")

# Starting items per training phase
PHASE_ITEMS: dict[int, dict[str, int]] = {
    1: {
        "iron-plate": 50,
        "copper-plate": 20,
        "coal": 30,
        "stone": 20,
        "stone-furnace": 4,
        "burner-mining-drill": 2,
        "wooden-chest": 2,
    },
    2: {
        "iron-plate": 20,
        "copper-plate": 10,
        "stone-furnace": 2,
        "burner-mining-drill": 2,
        "transport-belt": 20,
        "inserter": 5,
    },
    3: {
        "iron-plate": 50,
        "copper-plate": 30,
        "stone-furnace": 4,
        "transport-belt": 50,
        "inserter": 10,
        "small-electric-pole": 10,
        "boiler": 2,
        "steam-engine": 2,
    },
    4: {
        "iron-plate": 100,
        "copper-plate": 50,
        "electronic-circuit": 20,
        "transport-belt": 100,
        "inserter": 20,
        "assembling-machine-1": 5,
    },
}

# Lua snippet to perform a soft reset: clear entities, reset inventory, teleport to spawn.
# Must be a single /c command — RCON splits on newlines, so join with semicolons.
_SOFT_RESET_LUA = (
    '/c local surface = game.get_surface("nauvis"); '
    'local force = game.forces["player"]; '
    # Clear non-infrastructure entities
    'local keep = {["character"]=true, ["resource"]=true, ["tree"]=true, '
    '["furnace"]=true, ["mining-drill"]=true, ["assembling-machine"]=true, '
    '["transport-belt"]=true, ["inserter"]=true, ["electric-pole"]=true, '
    '["boiler"]=true, ["generator"]=true, ["lab"]=true, ["container"]=true}; '
    'for _, ent in pairs(surface.find_entities()) do '
    'if ent.valid and not keep[ent.name] and not keep[ent.type] then '
    'pcall(function() ent.destroy() end) end '
    'end; '
    # Clear and teleport — works with player or headless character
    'local player = game.players[1]; '
    'if player and player.character then '
    '  player.get_main_inventory().clear(); '
    '  player.teleport({0, 0}, surface); '
    'end; '
    'if storage.biged_character and storage.biged_character.valid then '
    '  storage.biged_character.teleport({0, 0}, surface); '
    'end; '
    # Clear script inventory too
    'if storage.biged_inventory and storage.biged_inventory.valid then '
    '  storage.biged_inventory.clear(); '
    'end; '
    # Replenish ore near spawn (5M per tile, never runs dry)
    'local s = player.surface; '
    'local ores = {{"iron-ore",3,0},{"iron-ore",-3,0},{"copper-ore",0,3},'
    '{"copper-ore",0,-3},{"coal",4,4},{"coal",-4,-4},{"stone",-4,4},{"stone",4,-4}}; '
    'for _,o in pairs(ores) do for dx=-3,3 do for dy=-3,3 do '
    'local pos = {o[2]+dx, o[3]+dy}; '
    'local existing = s.find_entities_filtered{name=o[1], position=pos, radius=0.5}; '
    'if #existing == 0 then s.create_entity{name=o[1], position=pos, amount=5000000} '
    'else for _,e in pairs(existing) do if e.amount < 1000000 then e.amount = 5000000 end end end '
    'end end end; '
    'rcon.print("soft_reset_done")'
)


class EpisodeManager:
    """Manages the training episode lifecycle for the Factorio RL agent.

    Args:
        rcon:      An async RCON client with a ``command(cmd)`` coroutine.
        phase:     Current curriculum phase (1-4).
        max_steps: Maximum steps per episode before auto-done (default 2000).
    """

    def __init__(self, rcon, phase: int, max_steps: int = 2000) -> None:
        self._rcon = rcon
        self._phase = phase
        self._max_steps = max_steps
        self._episode_count = 0
        self._step_count = 0
        self._episode_start: float = time.monotonic()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def episode_count(self) -> int:
        """Total episodes completed (resets excluded from count until done)."""
        return self._episode_count

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def reset(self) -> None:
        """Begin a new episode.

        Attempts soft_reset(); falls back to hard_reset() on failure.
        Ensures player has a body, then distributes starting items.
        """
        success = await self.soft_reset()
        if not success:
            log.warning("soft_reset failed — falling back to hard_reset")
            await self.hard_reset()

        # Ensure the agent has a body before inserting items — soft_reset
        # preserves "character" entities, but if the character died during
        # the episode, the body is gone and items would be lost.
        try:
            await self._rcon.remote_call("ensure_player")
        except Exception:
            log.warning("ensure_player in reset failed", exc_info=True)

        await self._give_starting_items()
        self._episode_count += 1
        self._step_count = 0
        self._episode_start = time.monotonic()

    async def soft_reset(self) -> bool:
        """Send a Lua script via RCON to reset game state in-place.

        Returns:
            True if the server responded with "soft_reset_done", False otherwise.
        """
        try:
            response = await self._rcon.command(_SOFT_RESET_LUA)
            if response and "soft_reset_done" in str(response):
                log.info("soft_reset succeeded (episode %d)", self._episode_count + 1)
                return True
            log.warning("soft_reset: unexpected response: %r", response)
            return False
        except Exception:
            log.warning("soft_reset: RCON command failed", exc_info=True)
            return False

    async def hard_reset(self) -> None:
        """Perform a hard reset via save/load cycle.

        This is a best-effort fallback — the server restart and RCON reconnect
        are handled externally (e.g., by the bridge supervisor). Here we simply
        issue a save command and log the intent.
        """
        log.info("hard_reset: issuing /save and signalling restart")
        try:
            await self._rcon.command("/save biged_episode_reset")
        except Exception:
            log.warning("hard_reset: /save command failed", exc_info=True)

    async def _give_starting_items(self) -> None:
        """Insert phase-appropriate starting items into the agent inventory.

        Works headless: uses player inventory if connected, falls back to
        the script-owned agent inventory (same one exec_cmd uses).
        """
        items = PHASE_ITEMS.get(self._phase, PHASE_ITEMS[1])
        inserts = "; ".join(
            f'inv.insert{{name="{name}", count={count}}}'
            for name, count in items.items()
        )
        lua = (
            "/c local inv; "
            "local p = game.players[1]; "
            "if p and p.character then inv = p.get_main_inventory() end; "
            "if not inv then "
            "  if not storage.biged_inventory or not storage.biged_inventory.valid then "
            "    storage.biged_inventory = game.create_inventory(80) "
            "  end; "
            "  inv = storage.biged_inventory "
            "end; "
            f"{inserts}"
        )
        try:
            await self._rcon.command(lua)
            log.debug("Starting items given for phase %d", self._phase)
        except Exception:
            log.warning(
                "_give_starting_items: failed to insert items for phase %d",
                self._phase,
                exc_info=True,
            )

    async def set_game_speed(self, speed: float) -> None:
        """Set the Factorio simulation speed multiplier.

        Args:
            speed: Multiplier (1.0 = normal, 10.0 = 10x fast-forward).
        """
        cmd = f"/c game.speed = {speed}"
        try:
            await self._rcon.command(cmd)
            log.info("Game speed set to %s", speed)
        except Exception:
            log.warning("set_game_speed: RCON command failed", exc_info=True)

    def record_step(self) -> None:
        """Increment the step counter for the current episode."""
        self._step_count += 1

    def is_episode_done(self, max_steps: Optional[int] = None) -> bool:
        """Return True if the episode step limit has been reached.

        Args:
            max_steps: Override the instance max_steps for this check.
        """
        limit = max_steps if max_steps is not None else self._max_steps
        return self._step_count >= limit

    def get_episode_info(self) -> dict:
        """Return a snapshot of the current episode state.

        Returns:
            dict with keys: episode, phase, step, max_steps, elapsed_secs.
        """
        return {
            "episode": self._episode_count,
            "phase": self._phase,
            "step": self._step_count,
            "max_steps": self._max_steps,
            "elapsed_secs": round(time.monotonic() - self._episode_start, 3),
        }

    def set_phase(self, phase: int) -> None:
        """Update the current training phase.

        Args:
            phase: New phase number (1-4).
        """
        log.info("Phase updated: %d -> %d", self._phase, phase)
        self._phase = phase
