"""Tests for LLM Orchestrator — diagnostics and narration between training episodes."""
import pytest
from unittest.mock import patch, MagicMock
from factorio.llm_orchestrator import LLMOrchestrator, DiagnosticResult


def test_should_diagnose_after_stall():
    orch = LLMOrchestrator(ollama_url="http://localhost:11434")
    history = [{"episode": i, "lessons_passed": 0, "reward": -0.5} for i in range(10)]
    assert orch.should_diagnose(history, stall_threshold=10) is True


def test_should_not_diagnose_when_progressing():
    orch = LLMOrchestrator(ollama_url="http://localhost:11434")
    history = [{"episode": i, "lessons_passed": i // 3, "reward": 0.5} for i in range(10)]
    assert orch.should_diagnose(history, stall_threshold=10) is False


def test_format_diagnosis_prompt():
    orch = LLMOrchestrator(ollama_url="http://localhost:11434")
    history = [{"episode": 1, "lessons_passed": 0, "reward": -0.3,
                "action_distribution": {"place": 5, "wait": 90, "move": 5}}]
    prompt = orch._format_diagnosis_prompt(history, phase=1)
    assert "Phase 1" in prompt
    assert "wait" in prompt.lower()


def test_format_narration_prompt():
    orch = LLMOrchestrator(ollama_url="http://localhost:11434")
    episode_summary = {"episode": 10, "reward": 2.5, "lessons_passed": 1,
                       "steps": 500, "actions": {"craft": 30, "place": 20}}
    prompt = orch._format_narration_prompt(episode_summary)
    assert "Episode 10" in prompt


def test_parse_diagnostic_result():
    orch = LLMOrchestrator(ollama_url="http://localhost:11434")
    raw = "DIAGNOSIS: Agent is stuck looping wait actions.\nSUGGESTION: Increase entropy bonus to 0.03"
    result = orch._parse_diagnostic(raw)
    assert isinstance(result, DiagnosticResult)
    assert "stuck" in result.diagnosis.lower() or "wait" in result.diagnosis.lower()


@patch("factorio.llm_orchestrator.urllib.request.urlopen")
def test_narrate_calls_ollama(mock_urlopen):
    mock_resp = MagicMock()
    mock_resp.read.return_value = b'{"response": "The agent made progress on crafting."}'
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_urlopen.return_value = mock_resp

    orch = LLMOrchestrator(ollama_url="http://localhost:11434")
    result = orch.narrate({"episode": 5, "reward": 1.0, "lessons_passed": 1, "steps": 200, "actions": {}})
    assert isinstance(result, str)
    assert len(result) > 0
