"""Curriculum engine — load TOMLs, evaluate criteria, track progress."""
import logging
import re
from pathlib import Path

log = logging.getLogger("biged.factorio.curriculum")

_COMPARISON_RE = re.compile(
    r"([\w.\-]+)\s*(>=|<=|>|<|==)\s*([\d.]+)"
)


def evaluate_criteria(criteria: str, state: dict) -> bool:
    """Evaluate a success criteria string against game state.
    Supports: dotted.path >= N, AND, OR connectors.
    State dict expected keys: inventory, flow, entities, production.
    """
    or_parts = [p.strip() for p in criteria.split(" OR ")]
    for or_part in or_parts:
        and_parts = [p.strip() for p in or_part.split(" AND ")]
        all_true = True
        for clause in and_parts:
            if not _eval_comparison(clause, state):
                all_true = False
                break
        if all_true:
            return True
    return False


def _eval_comparison(clause: str, state: dict) -> bool:
    match = _COMPARISON_RE.match(clause.strip())
    if not match:
        log.warning("Could not parse criteria clause: %s", clause)
        return False

    path, op, threshold_str = match.groups()
    threshold = float(threshold_str)
    value = _resolve_path(path, state)
    if value is None:
        return False

    if op == ">=":
        return value >= threshold
    if op == "<=":
        return value <= threshold
    if op == ">":
        return value > threshold
    if op == "<":
        return value < threshold
    if op == "==":
        return value == threshold
    return False


def _resolve_path(path: str, state: dict):
    parts = path.split(".", 1)
    if len(parts) == 1:
        return state.get(parts[0])
    section, key = parts
    sub = state.get(section)
    if isinstance(sub, dict):
        return sub.get(key, 0)
    return None


def load_curriculum(name: str, curriculum_dir: str = "idle_curricula") -> dict | None:
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib
        except ImportError:
            log.warning("Neither tomllib nor tomli available — cannot load curriculum")
            return None

    path = Path(curriculum_dir) / f"{name}.toml"
    if not path.exists():
        log.warning("Curriculum not found: %s", path)
        return None

    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except Exception:
        log.warning("Failed to load curriculum %s", path, exc_info=True)
        return None


class LessonTracker:
    def __init__(self, total_lessons: int, max_attempts: list[int] | None = None):
        self._passed = [False] * total_lessons
        self._attempts = [0] * total_lessons
        self._max_attempts = max_attempts or [0] * total_lessons  # 0 = no limit

    @property
    def current_index(self) -> int:
        for i, p in enumerate(self._passed):
            if not p:
                return i
        return len(self._passed)

    @property
    def all_passed(self) -> bool:
        return all(self._passed)

    def mark_passed(self, index: int) -> None:
        if 0 <= index < len(self._passed):
            self._passed[index] = True

    def mark_attempt(self, index: int) -> None:
        if 0 <= index < len(self._attempts):
            self._attempts[index] += 1
            # Auto-pass if max_attempts exceeded (0 = no limit)
            limit = self._max_attempts[index] if index < len(self._max_attempts) else 0
            if limit > 0 and self._attempts[index] > limit and not self._passed[index]:
                self._passed[index] = True
                log.info("Lesson %d auto-passed: exceeded max_attempts=%d", index, limit)

    def get_progress(self) -> dict:
        return {
            "total": len(self._passed),
            "completed": sum(self._passed),
            "current": self.current_index,
            "attempts": list(self._attempts),
        }
