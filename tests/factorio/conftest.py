"""Shared fixtures and path setup for Factorio ML tests."""
import sys
from pathlib import Path

# Add fleet/ to sys.path so 'from factorio.X import Y' works
fleet_dir = str(Path(__file__).resolve().parent.parent.parent / "fleet")
if fleet_dir not in sys.path:
    sys.path.insert(0, fleet_dir)
