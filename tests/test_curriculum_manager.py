"""Tests for CurriculumManager — TOML loading, lesson tracking, phase advancement."""
import pytest
from pathlib import Path


PHASE1_TOML = b"""
[meta]
phase = 1
name = "Bootstrap"
description = "Hand-craft basics"

[[lessons]]
name = "Craft gears"
description = "Craft 10 iron gear wheels"
criteria = "inventory.iron-gear-wheel >= 10"
hint = "craft iron-gear-wheel count=10"
max_attempts = 20

[[lessons]]
name = "Place furnaces"
description = "Place 3 stone furnaces"
criteria = "entities.stone-furnace >= 3"
hint = "place stone-furnace near ore"
max_attempts = 30
"""

PHASE2_TOML = b"""
[meta]
phase = 2
name = "Automate"
description = "Build power and automation"

[[lessons]]
name = "Build power"
description = "Place boiler + steam engine"
criteria = "entities.boiler >= 1 AND entities.steam-engine >= 1"
hint = "offshore-pump -> boiler -> steam-engine"
max_attempts = 30
"""


@pytest.fixture
def curricula_dir(tmp_path):
    """Create a temp dir with phase TOML files."""
    (tmp_path / "phase1_bootstrap.toml").write_bytes(PHASE1_TOML)
    (tmp_path / "phase2_automate.toml").write_bytes(PHASE2_TOML)
    return str(tmp_path)


def test_load_phase(curricula_dir):
    from factorio.curriculum_manager import CurriculumManager
    cm = CurriculumManager(current_phase=1, curricula_dir=curricula_dir)
    obj = cm.get_current_objective()
    assert obj["phase"] == 1
    assert obj["lesson_name"] == "Craft gears"
    assert "iron-gear-wheel" in obj["criteria"]


def test_check_progress_lesson_not_passed(curricula_dir):
    from factorio.curriculum_manager import CurriculumManager
    cm = CurriculumManager(current_phase=1, curricula_dir=curricula_dir)
    state = {"inventory": {"iron-gear-wheel": 5}, "entities": {}}
    result = cm.check_progress(state)
    assert result["lesson_passed"] is False
    assert result["phase_complete"] is False


def test_check_progress_lesson_passed(curricula_dir):
    from factorio.curriculum_manager import CurriculumManager
    cm = CurriculumManager(current_phase=1, curricula_dir=curricula_dir)
    state = {"inventory": {"iron-gear-wheel": 15}, "entities": {}}
    result = cm.check_progress(state)
    assert result["lesson_passed"] is True
    assert result["phase_complete"] is False
    obj = cm.get_current_objective()
    assert obj["lesson_name"] == "Place furnaces"


def test_check_progress_phase_complete(curricula_dir):
    from factorio.curriculum_manager import CurriculumManager
    cm = CurriculumManager(current_phase=1, curricula_dir=curricula_dir)
    cm.check_progress({"inventory": {"iron-gear-wheel": 15}, "entities": {}})
    result = cm.check_progress({"inventory": {}, "entities": {"stone-furnace": 5}})
    assert result["lesson_passed"] is True
    assert result["phase_complete"] is True


def test_advance_phase(curricula_dir):
    from factorio.curriculum_manager import CurriculumManager
    cm = CurriculumManager(current_phase=1, curricula_dir=curricula_dir)
    cm.check_progress({"inventory": {"iron-gear-wheel": 15}, "entities": {}})
    cm.check_progress({"inventory": {}, "entities": {"stone-furnace": 5}})
    ok = cm.advance_phase()
    assert ok is True
    obj = cm.get_current_objective()
    assert obj["phase"] == 2
    assert obj["lesson_name"] == "Build power"


def test_advance_phase_at_max(curricula_dir):
    from factorio.curriculum_manager import CurriculumManager
    cm = CurriculumManager(current_phase=2, curricula_dir=curricula_dir)
    cm.check_progress({"inventory": {}, "entities": {"boiler": 1, "steam-engine": 1}})
    ok = cm.advance_phase()
    assert ok is False


def test_get_progress(curricula_dir):
    from factorio.curriculum_manager import CurriculumManager
    cm = CurriculumManager(current_phase=1, curricula_dir=curricula_dir)
    p = cm.get_progress()
    assert p["phase"] == 1
    assert p["total_lessons"] == 2
    assert p["completed"] == 0
    assert p["current_lesson"] == 0
