"""Unit tests for fleet/providers.py — circuit breaker and routing."""
import os
import sys
import time
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fleet"))

import providers


def _reset_circuit(provider="test_provider"):
    """Clear circuit breaker state for a provider."""
    with providers._circuit_lock:
        providers._circuit_state.pop(provider, None)


class TestCircuitBreakerClosed:
    """Circuit starts closed (not open)."""

    def test_starts_closed(self):
        _reset_circuit()
        assert not providers._circuit_is_open("test_provider")


class TestCircuitBreakerOpens:
    """Opens after CIRCUIT_FAILURE_THRESHOLD failures."""

    def test_opens_after_threshold(self):
        _reset_circuit()
        for _ in range(providers.CIRCUIT_FAILURE_THRESHOLD):
            providers._circuit_record_failure("test_provider")
        assert providers._circuit_is_open("test_provider")


class TestCircuitBreakerHalfOpen:
    """Half-open after cooldown expires — allows retry."""

    def test_half_open_after_cooldown(self):
        _reset_circuit()
        for _ in range(providers.CIRCUIT_FAILURE_THRESHOLD):
            providers._circuit_record_failure("test_provider")
        assert providers._circuit_is_open("test_provider")

        # Simulate cooldown expiry by moving open_until into the past
        with providers._circuit_lock:
            providers._circuit_state["test_provider"]["open_until"] = time.time() - 1

        # Should now be closed (half-open reset)
        assert not providers._circuit_is_open("test_provider")
        # Failures should have been reset
        with providers._circuit_lock:
            assert providers._circuit_state["test_provider"]["failures"] == 0


class TestCircuitBreakerResetsOnSuccess:
    """Record success resets failure count."""

    def test_resets_on_success(self):
        _reset_circuit()
        # Record some failures (below threshold)
        providers._circuit_record_failure("test_provider")
        providers._circuit_record_failure("test_provider")
        # Record success
        providers._circuit_record_success("test_provider")
        with providers._circuit_lock:
            assert providers._circuit_state["test_provider"]["failures"] == 0
        assert not providers._circuit_is_open("test_provider")


class TestAgentAffinity:
    """get_agent_affinity returns False for unknown agents."""

    def test_unknown_agent_returns_false(self, tmp_db):
        result = providers.get_agent_affinity("nonexistent_agent", "summarize", {})
        assert result is False


class TestComplexityRouting:
    """LOCAL_COMPLEXITY_ROUTING maps complexity tiers correctly."""

    def test_simple_maps_to_mid(self):
        assert providers.LOCAL_COMPLEXITY_ROUTING["simple"] == "mid"

    def test_medium_maps_to_default(self):
        assert providers.LOCAL_COMPLEXITY_ROUTING["medium"] == "default"

    def test_complex_maps_to_default(self):
        assert providers.LOCAL_COMPLEXITY_ROUTING["complex"] == "default"


class TestSkillComplexityClassification:
    """SKILL_COMPLEXITY dict covers known skills."""

    def test_tiers_are_valid(self):
        assert set(providers.SKILL_COMPLEXITY.keys()) == {"simple", "medium", "complex"}

    def test_known_skill_classified(self):
        all_skills = []
        for tier_skills in providers.SKILL_COMPLEXITY.values():
            all_skills.extend(tier_skills)
        assert "summarize" in all_skills
        assert "plan_workload" in all_skills
