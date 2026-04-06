"""Minimal echo skill for Rust bridge smoke testing."""

SKILL_NAME = "smoke_echo"
DESCRIPTION = "Returns input payload back — used for bridge verification."
VERSION = "1.0.0"
COMPLEXITY = "trivial"
REQUIRES_NETWORK = False
SUITE = "test"


def run(payload, config):
    return {
        "status": "ok",
        "echo": payload,
        "bridge": "rust-pyo3",
        "message": "Skill executed successfully via Rust bridge",
    }
