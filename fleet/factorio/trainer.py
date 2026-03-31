"""
PPO Trainer for the Factorio RL agent.

Components
----------
Transition      — named dataclass for a single environment step
TrajectoryBuffer — on-policy rollout storage; converts to tensors for update
PPOTrainer      — full PPO update loop with GAE, clipped objective, checkpointing
"""

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Transition dataclass
# ---------------------------------------------------------------------------

@dataclass
class Transition:
    """Single environment step stored in the trajectory buffer."""
    grid: np.ndarray          # (C, H, W) float32 — local view
    features: np.ndarray      # (feature_dim,) float32
    action_type: int
    log_prob: float
    value: float
    reward: float
    done: bool
    world_grid: np.ndarray | None = None  # (C, H, W) float32 — zoomed-out minimap
    action_mask: list | None = None       # bool list used during act() — needed for evaluate_action


# ---------------------------------------------------------------------------
# TrajectoryBuffer
# ---------------------------------------------------------------------------

class TrajectoryBuffer:
    """Simple list-based on-policy trajectory buffer."""

    def __init__(self) -> None:
        self._data: list[Transition] = []

    def add(self, transition: Transition) -> None:
        self._data.append(transition)

    def clear(self) -> None:
        self._data.clear()

    def __len__(self) -> int:
        return len(self._data)

    def get_all(self) -> list[Transition]:
        return list(self._data)

    def to_tensors(self, device: torch.device) -> dict[str, torch.Tensor]:
        """Convert all stored transitions to batched tensors."""
        grids = torch.tensor(
            np.stack([t.grid for t in self._data], axis=0),
            dtype=torch.float32, device=device,
        )
        features = torch.tensor(
            np.stack([t.features for t in self._data], axis=0),
            dtype=torch.float32, device=device,
        )
        # World grids — fall back to zeros if any transition lacks them
        has_world = all(t.world_grid is not None for t in self._data)
        if has_world:
            world_grids = torch.tensor(
                np.stack([t.world_grid for t in self._data], axis=0),
                dtype=torch.float32, device=device,
            )
        else:
            world_grids = torch.zeros_like(grids)
        actions = torch.tensor(
            [t.action_type for t in self._data],
            dtype=torch.long, device=device,
        )
        old_log_probs = torch.tensor(
            [t.log_prob for t in self._data],
            dtype=torch.float32, device=device,
        )
        values = torch.tensor(
            [t.value for t in self._data],
            dtype=torch.float32, device=device,
        )
        rewards = torch.tensor(
            [t.reward for t in self._data],
            dtype=torch.float32, device=device,
        )
        dones = torch.tensor(
            [t.done for t in self._data],
            dtype=torch.float32, device=device,
        )
        # Action masks — needed for evaluate_action during PPO update
        has_masks = all(t.action_mask is not None for t in self._data)
        if has_masks:
            action_masks = torch.tensor(
                [t.action_mask for t in self._data],
                dtype=torch.bool, device=device,
            )
        else:
            action_masks = None
        return {
            "grids": grids,
            "world_grids": world_grids,
            "features": features,
            "actions": actions,
            "old_log_probs": old_log_probs,
            "values": values,
            "rewards": rewards,
            "dones": dones,
            "action_masks": action_masks,
        }


# ---------------------------------------------------------------------------
# PPOTrainer
# ---------------------------------------------------------------------------

class PPOTrainer:
    """
    Proximal Policy Optimization trainer.

    Parameters
    ----------
    policy          : FactorioPolicy (actor-critic)
    lr              : Adam learning rate
    gamma           : discount factor
    gae_lambda      : GAE lambda
    clip_ratio      : PPO clip epsilon
    entropy_coeff   : entropy bonus coefficient
    value_coeff     : value loss coefficient
    max_grad_norm   : gradient clipping max norm
    ppo_epochs      : number of update epochs per call to update()
    batch_size      : minibatch size
    checkpoint_dir  : directory to save .pt checkpoint files
    device          : 'cpu' | 'cuda' | None (auto-detect)
    """

    def __init__(
        self,
        policy,
        lr: float = 3e-4,
        gamma: float = 0.99,
        gae_lambda: float = 0.95,
        clip_ratio: float = 0.2,
        entropy_coeff: float = 0.01,
        value_coeff: float = 0.5,
        max_grad_norm: float = 0.5,
        ppo_epochs: int = 4,
        batch_size: int = 64,
        checkpoint_dir: str = "fleet/factorio/checkpoints",
        device: Optional[str] = None,
    ) -> None:
        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"

        self._device = torch.device(device)
        self._gamma = gamma
        self._gae_lambda = gae_lambda
        self._clip_ratio = clip_ratio
        self._entropy_coeff = entropy_coeff
        self._value_coeff = value_coeff
        self._max_grad_norm = max_grad_norm
        self._ppo_epochs = ppo_epochs
        self._batch_size = batch_size
        self._checkpoint_dir = Path(checkpoint_dir)
        self._checkpoint_dir.mkdir(parents=True, exist_ok=True)

        self.policy = policy.to(self._device)
        self.optimizer = optim.Adam(self.policy.parameters(), lr=lr)

        self.total_updates: int = 0
        self.total_episodes: int = 0

        log.info(
            "PPOTrainer init — device=%s lr=%.1e clip=%.2f epochs=%d batch=%d",
            self._device, lr, clip_ratio, ppo_epochs, batch_size,
        )

    # ------------------------------------------------------------------
    # GAE computation
    # ------------------------------------------------------------------

    def _compute_gae(
        self,
        rewards: list,
        values: list,
        dones: list,
        next_value: float,
    ) -> list[float]:
        """
        Compute Generalised Advantage Estimates.

        delta_t = r_t + gamma * V(t+1) * (1 - done_t) - V(t)
        A_t     = delta_t + gamma * lambda * (1 - done_t) * A(t+1)
        """
        T = len(rewards)
        advantages = [0.0] * T
        gae = 0.0
        for t in reversed(range(T)):
            next_val = next_value if t == T - 1 else values[t + 1]
            mask = 0.0 if dones[t] else 1.0
            delta = rewards[t] + self._gamma * next_val * mask - values[t]
            gae = delta + self._gamma * self._gae_lambda * mask * gae
            advantages[t] = float(gae)
        return advantages

    # ------------------------------------------------------------------
    # PPO update
    # ------------------------------------------------------------------

    def update(self, buffer: "TrajectoryBuffer") -> dict:
        """
        Run PPO update over the trajectory in *buffer*.

        Returns a dict with keys: policy_loss, value_loss, entropy, total_loss.
        Skips and returns empty dict if buffer is smaller than batch_size.
        """
        if len(buffer) < self._batch_size:
            log.warning(
                "PPOTrainer.update skipped: buffer size %d < batch_size %d",
                len(buffer), self._batch_size,
            )
            return {}

        tensors = buffer.to_tensors(self._device)

        # --- Compute GAE on CPU-side lists ---
        rewards_list = [t.reward for t in buffer.get_all()]
        values_list = [t.value for t in buffer.get_all()]
        dones_list = [t.done for t in buffer.get_all()]
        next_value = 0.0  # terminal bootstrap
        advantages_list = self._compute_gae(rewards_list, values_list, dones_list, next_value)

        advantages = torch.tensor(advantages_list, dtype=torch.float32, device=self._device)
        returns = advantages + tensors["values"]

        # Normalize advantages
        adv_mean = advantages.mean()
        adv_std = advantages.std().clamp(min=1e-8)
        advantages = (advantages - adv_mean) / adv_std

        grids = tensors["grids"]
        world_grids = tensors["world_grids"]
        features = tensors["features"]
        actions = tensors["actions"]
        old_log_probs = tensors["old_log_probs"]
        action_masks = tensors.get("action_masks")  # may be None if not stored

        N = len(buffer)
        total_policy_loss = 0.0
        total_value_loss = 0.0
        total_entropy = 0.0
        update_count = 0

        self.policy.train()
        for _ in range(self._ppo_epochs):
            indices = torch.randperm(N, device=self._device)
            for start in range(0, N, self._batch_size):
                idx = indices[start: start + self._batch_size]
                if len(idx) == 0:
                    continue

                mb_grids = grids[idx]
                mb_world_grids = world_grids[idx]
                mb_features = features[idx]
                mb_actions = actions[idx]
                mb_old_log_probs = old_log_probs[idx]
                mb_advantages = advantages[idx]
                mb_returns = returns[idx]
                mb_masks = action_masks[idx] if action_masks is not None else None

                new_log_probs, new_values, entropy = self.policy.evaluate_action(
                    mb_grids, mb_features, mb_actions,
                    world_grid=mb_world_grids,
                    action_mask=mb_masks,
                )

                ratio = torch.exp(new_log_probs - mb_old_log_probs)
                clipped_ratio = torch.clamp(ratio, 1.0 - self._clip_ratio, 1.0 + self._clip_ratio)
                policy_loss = -torch.min(
                    ratio * mb_advantages,
                    clipped_ratio * mb_advantages,
                ).mean()

                value_loss = nn.functional.mse_loss(new_values, mb_returns)

                loss = (
                    policy_loss
                    + self._value_coeff * value_loss
                    - self._entropy_coeff * entropy
                )

                self.optimizer.zero_grad()
                loss.backward()
                nn.utils.clip_grad_norm_(self.policy.parameters(), self._max_grad_norm)
                self.optimizer.step()

                total_policy_loss += policy_loss.item()
                total_value_loss += value_loss.item()
                total_entropy += entropy.item()
                update_count += 1

        self.total_updates += 1
        n = max(update_count, 1)
        stats = {
            "policy_loss": total_policy_loss / n,
            "value_loss": total_value_loss / n,
            "entropy": total_entropy / n,
            "total_loss": (total_policy_loss + total_value_loss) / n,
        }
        log.debug("PPO update #%d — %s", self.total_updates, stats)
        return stats

    # ------------------------------------------------------------------
    # Checkpointing
    # ------------------------------------------------------------------

    def save_checkpoint(self, episode: int) -> str:
        """Save trainer state to a .pt file. Returns the file path."""
        path = self._checkpoint_dir / f"ppo_ep{episode:06d}.pt"
        try:
            torch.save(
                {
                    "episode": episode,
                    "total_updates": self.total_updates,
                    "total_episodes": self.total_episodes,
                    "policy_state": self.policy.state_dict(),
                    "optimizer_state": self.optimizer.state_dict(),
                },
                str(path),
            )
            log.info("Checkpoint saved: %s", path)
        except Exception:
            log.warning("save_checkpoint failed for episode=%d", episode, exc_info=True)
            raise
        return str(path)

    def load_checkpoint(self, path: str) -> int:
        """Load trainer state from *path*. Returns the episode number."""
        try:
            ckpt = torch.load(path, map_location=self._device, weights_only=True)
            self.policy.load_state_dict(ckpt["policy_state"])
            self.optimizer.load_state_dict(ckpt["optimizer_state"])
            self.total_updates = ckpt.get("total_updates", 0)
            self.total_episodes = ckpt.get("total_episodes", 0)
            episode = ckpt.get("episode", 0)
            log.info("Checkpoint loaded: %s (episode=%d)", path, episode)
            return episode
        except Exception:
            log.warning("load_checkpoint failed for path=%s", path, exc_info=True)
            raise

    def get_latest_checkpoint(self) -> Optional[str]:
        """Return path to the most recent checkpoint, or None if none exist."""
        try:
            pts = sorted(self._checkpoint_dir.glob("ppo_ep*.pt"))
            if not pts:
                return None
            return str(pts[-1])
        except Exception:
            log.warning("get_latest_checkpoint failed", exc_info=True)
            return None
