"""BigEd CC — Dialog windows extracted from launcher.py (TECH_DEBT 4.2)."""

from .thermal import ThermalDialog
from .model_selector import ModelSelectorDialog, OLLAMA_MODELS
from .review import ReviewDialog
from .walkthrough import (
    WalkthroughDialog,
    _detect_system_profile,
    _apply_system_profile,
    _should_show_walkthrough,
)

__all__ = [
    "ThermalDialog",
    "ModelSelectorDialog",
    "OLLAMA_MODELS",
    "ReviewDialog",
    "WalkthroughDialog",
    "_detect_system_profile",
    "_apply_system_profile",
    "_should_show_walkthrough",
]
