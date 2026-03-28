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
    from factorio.curriculum import load_curriculum
    curriculum = load_curriculum("factorio_01_bootstrap", curriculum_dir="idle_curricula")
    assert curriculum is not None
    assert len(curriculum["tasks"]) > 0
    assert curriculum["tasks"][0]["type"] == "factorio"


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
