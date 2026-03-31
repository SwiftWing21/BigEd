"""Curriculum manager — phase lifecycle, TOML loading, lesson evaluation."""
import logging
from pathlib import Path

from factorio.curriculum import evaluate_criteria, LessonTracker

log = logging.getLogger("biged.factorio.curriculum_mgr")


class CurriculumManager:
    """Manages curriculum phases and lesson progression."""

    def __init__(self, current_phase: int = 1, curricula_dir: str = "fleet/factorio/curricula"):
        self._phase = current_phase
        path = Path(curricula_dir)
        if not path.is_absolute():
            # Resolve relative to project root (grandparent of this file: fleet/factorio/ -> fleet/ -> project/)
            fleet_dir = Path(__file__).parent.parent
            path = fleet_dir.parent / curricula_dir
        self._curricula_dir = path
        self._meta: dict = {}
        self._lessons: list[dict] = []
        self._tracker: LessonTracker | None = None
        self._completed_phases: list[int] = []
        self._load_phase(current_phase)

    def _load_phase(self, phase: int) -> bool:
        """Load a phase TOML by scanning for phase{N}_*.toml."""
        try:
            import tomllib
        except ImportError:
            try:
                import tomli as tomllib
            except ImportError:
                log.warning("No TOML library available")
                return False

        pattern = f"phase{phase}_*.toml"
        matches = list(self._curricula_dir.glob(pattern))
        if not matches:
            log.warning("No curriculum found for phase %d in %s", phase, self._curricula_dir)
            return False

        path = matches[0]
        try:
            with open(path, "rb") as f:
                data = tomllib.load(f)
        except Exception:
            log.warning("Failed to load %s", path, exc_info=True)
            return False

        self._meta = data.get("meta", {})
        self._lessons = data.get("lessons", [])
        self._tracker = LessonTracker(total_lessons=len(self._lessons))
        self._phase = phase
        log.info("Loaded phase %d: %s (%d lessons)", phase, self._meta.get("name", "?"), len(self._lessons))
        return True

    @property
    def checkpoint(self) -> int:
        """Map phase number (1-8) to checkpoint index (0-7)."""
        return self._phase - 1

    @property
    def checkpoint_bonus(self) -> float:
        """Scaling completion bonus: checkpoint 0→+10, 1→+20, ... 7→+80."""
        return (self.checkpoint + 1) * 10.0

    def current_lesson_index(self) -> int:
        """Return the index of the current lesson (0-based)."""
        if not self._tracker:
            return 0
        return self._tracker.current_index

    def get_current_objective(self) -> dict:
        """Return the current lesson objective for the LLM prompt."""
        if not self._tracker or not self._lessons:
            return {"phase": self._phase, "lesson_name": "No curriculum loaded",
                    "criteria": "", "description": "", "hint": ""}
        idx = self._tracker.current_index
        if idx >= len(self._lessons):
            return {"phase": self._phase, "lesson_name": "Phase complete",
                    "criteria": "", "description": "", "hint": ""}
        lesson = self._lessons[idx]
        return {
            "phase": self._phase,
            "phase_name": self._meta.get("name", f"Phase {self._phase}"),
            "lesson_name": lesson["name"],
            "criteria": lesson["criteria"],
            "description": lesson.get("description", ""),
            "hint": lesson.get("hint", ""),
        }

    def check_progress(self, state_dict: dict) -> dict:
        """Evaluate current lesson criteria against flattened state dict."""
        if not self._tracker or not self._lessons:
            return {"lesson_passed": False, "phase_complete": False, "progress": self.get_progress()}

        idx = self._tracker.current_index
        if idx >= len(self._lessons):
            return {"lesson_passed": False, "phase_complete": True, "progress": self.get_progress()}

        lesson = self._lessons[idx]
        self._tracker.mark_attempt(idx)

        if evaluate_criteria(lesson["criteria"], state_dict):
            self._tracker.mark_passed(idx)
            log.info("Lesson %d passed: %s", idx, lesson["name"])
            phase_complete = self._tracker.all_passed
            return {
                "lesson_passed": True,
                "lesson_name": lesson["name"],
                "phase_complete": phase_complete,
                "phase": self._phase,
                "progress": self.get_progress(),
            }

        return {"lesson_passed": False, "phase_complete": False, "progress": self.get_progress()}

    def advance_phase(self) -> bool:
        """Advance to next phase. Returns False if no next phase exists."""
        if self._phase not in self._completed_phases:
            self._completed_phases.append(self._phase)
        next_phase = self._phase + 1
        if self._load_phase(next_phase):
            log.info("Advanced to phase %d", next_phase)
            return True
        log.info("No phase %d found — curriculum complete", next_phase)
        return False

    def get_progress(self) -> dict:
        """Full progress snapshot for dashboard/logging."""
        tracker_progress = self._tracker.get_progress() if self._tracker else {}
        return {
            "phase": self._phase,
            "phase_name": self._meta.get("name", ""),
            "total_lessons": tracker_progress.get("total", 0),
            "completed": tracker_progress.get("completed", 0),
            "current_lesson": tracker_progress.get("current", 0),
            "attempts": tracker_progress.get("attempts", []),
            "completed_phases": list(self._completed_phases),
        }
