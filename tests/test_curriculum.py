"""Tests for curriculum engine — criteria parsing, lesson tracking, graduation."""
import pytest


def test_evaluate_simple_criteria():
    from factorio.curriculum import evaluate_criteria
    state = {"inventory": {"iron-gear-wheel": 15}, "flow": {}, "entities": {}, "production": {}}
    assert evaluate_criteria("inventory.iron-gear-wheel >= 10", state) is True
    assert evaluate_criteria("inventory.iron-gear-wheel >= 20", state) is False


def test_evaluate_and_criteria():
    from factorio.curriculum import evaluate_criteria
    state = {"inventory": {"iron-plate": 50}, "flow": {"iron-plate": 12.5},
             "entities": {}, "production": {}}
    assert evaluate_criteria("inventory.iron-plate >= 20 AND flow.iron-plate > 5", state) is True
    assert evaluate_criteria("inventory.iron-plate >= 100 AND flow.iron-plate > 5", state) is False


def test_evaluate_missing_key_returns_false():
    from factorio.curriculum import evaluate_criteria
    state = {"inventory": {}, "flow": {}, "entities": {}, "production": {}}
    assert evaluate_criteria("inventory.iron-plate >= 10", state) is False


def test_load_curriculum():
    from pathlib import Path
    from factorio.curriculum import load_curriculum
    curricula_dir = Path(__file__).resolve().parent.parent / "fleet" / "factorio" / "curricula"
    curriculum = load_curriculum("phase1_bootstrap", curriculum_dir=str(curricula_dir))
    assert curriculum is not None
    assert len(curriculum["lessons"]) > 0
    assert curriculum["meta"]["name"] is not None


def test_lesson_tracker():
    from factorio.curriculum import LessonTracker
    tracker = LessonTracker(total_lessons=3)
    assert tracker.current_index == 0
    assert not tracker.all_passed
    tracker.mark_passed(0)
    assert tracker.current_index == 1
    tracker.mark_passed(1)
    tracker.mark_passed(2)
    assert tracker.all_passed
