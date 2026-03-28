"""Tests for prompt template loading and rendering."""
import os
import pytest


def test_load_template_returns_system_and_user():
    from factorio.prompt_loader import load_prompt_template
    tmpl = load_prompt_template("test_prompt", prompts_dir="tests/fixtures/prompts")
    assert "system_template" in tmpl
    assert "user_template" in tmpl


def test_load_template_missing_raises():
    from factorio.prompt_loader import load_prompt_template
    with pytest.raises(FileNotFoundError):
        load_prompt_template("nonexistent", prompts_dir="tests/fixtures/prompts")


def test_render_prompt_substitutes_placeholders():
    from factorio.prompt_loader import load_prompt_template, render_prompt
    tmpl = load_prompt_template("test_prompt", prompts_dir="tests/fixtures/prompts")
    system, user = render_prompt(tmpl, state="iron=42", objective="craft gears", previous_results="none")
    assert "iron=42" in user
    assert "craft gears" in user
