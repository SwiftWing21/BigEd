"""Tests for CurriculumManager checkpoint properties and phases 5-8 TOML loading."""
import sys
from pathlib import Path

# Ensure fleet/ is on sys.path so 'factorio.*' imports resolve.
_fleet_dir = str(Path(__file__).resolve().parent.parent.parent / "fleet")
if _fleet_dir not in sys.path:
    sys.path.insert(0, _fleet_dir)

from factorio.curriculum_manager import CurriculumManager

CURRICULA_DIR = str(Path(__file__).resolve().parent.parent.parent / "fleet" / "factorio" / "curricula")


# --- checkpoint property ---

def test_curriculum_manager_has_checkpoint_property():
    cm = CurriculumManager(current_phase=1, curricula_dir=CURRICULA_DIR)
    assert hasattr(cm, "checkpoint")
    assert cm.checkpoint == 0


def test_checkpoint_maps_phases_correctly():
    for phase, expected_cp in [(1, 0), (2, 1), (3, 2), (4, 3)]:
        cm = CurriculumManager(current_phase=phase, curricula_dir=CURRICULA_DIR)
        assert cm.checkpoint == expected_cp, f"Phase {phase} should be checkpoint {expected_cp}"


# --- checkpoint_bonus property ---

def test_checkpoint_bonus_scales():
    cm = CurriculumManager(current_phase=1, curricula_dir=CURRICULA_DIR)
    assert cm.checkpoint_bonus == 10.0
    cm4 = CurriculumManager(current_phase=4, curricula_dir=CURRICULA_DIR)
    assert cm4.checkpoint_bonus == 40.0


def test_checkpoint_bonus_high_phases():
    """Phases 5-8 should give bonuses 50-80."""
    for phase, expected_bonus in [(5, 50.0), (6, 60.0), (7, 70.0), (8, 80.0)]:
        cm = CurriculumManager(current_phase=phase, curricula_dir=CURRICULA_DIR)
        assert cm.checkpoint_bonus == expected_bonus, f"Phase {phase} bonus should be {expected_bonus}"


# --- TOML loading for phases 5-8 ---

def test_phase5_loads():
    cm = CurriculumManager(current_phase=5, curricula_dir=CURRICULA_DIR)
    assert cm._meta.get("name") == "Blue Science (Chemical)"
    assert len(cm._lessons) == 4


def test_phase6_loads():
    cm = CurriculumManager(current_phase=6, curricula_dir=CURRICULA_DIR)
    assert cm._meta.get("name") == "Purple Science (Production)"
    assert len(cm._lessons) == 4


def test_phase7_loads():
    cm = CurriculumManager(current_phase=7, curricula_dir=CURRICULA_DIR)
    assert cm._meta.get("name") == "Yellow Science (Utility)"
    assert len(cm._lessons) == 3


def test_phase8_loads():
    cm = CurriculumManager(current_phase=8, curricula_dir=CURRICULA_DIR)
    assert cm._meta.get("name") == "Rocket & Space Science"
    assert len(cm._lessons) == 5


# --- max_attempts enforcement ---

def test_max_attempts_auto_skip():
    """LessonTracker should auto-pass lesson when max_attempts exceeded."""
    from factorio.curriculum import LessonTracker

    tracker = LessonTracker(total_lessons=3, max_attempts=[100, 100, 100])
    # Simulate 101 attempts on lesson 0
    for _ in range(101):
        tracker.mark_attempt(0)
    assert tracker._passed[0] is True, "Lesson should auto-pass after max_attempts"
    assert tracker.current_index == 1, "Should advance to lesson 1"


def test_max_attempts_zero_means_no_limit():
    """max_attempts=0 means no auto-skip."""
    from factorio.curriculum import LessonTracker

    tracker = LessonTracker(total_lessons=2, max_attempts=[0, 0])
    for _ in range(10000):
        tracker.mark_attempt(0)
    assert tracker._passed[0] is False, "Should never auto-pass with max_attempts=0"


def test_full_curriculum_advance():
    """Verify we can advance through all 8 phases."""
    cm = CurriculumManager(current_phase=1, curricula_dir=CURRICULA_DIR)
    phases_loaded = [1]
    for _ in range(7):
        ok = cm.advance_phase()
        assert ok, f"Failed to advance past phase {cm._phase}"
        phases_loaded.append(cm._phase)
    assert phases_loaded == [1, 2, 3, 4, 5, 6, 7, 8]
    # Phase 9 should not exist
    assert not cm.advance_phase()
