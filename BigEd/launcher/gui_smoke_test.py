#!/usr/bin/env python3
"""
Headless GUI smoke test — validates launcher components without display.
Run: python gui_smoke_test.py [--smoke]

Checks:
1. launcher.py imports cleanly
2. Module discovery finds all mod_*.py files
3. Module manifest loads and validates
4. Fleet dir detection resolves correctly
5. All UI extraction modules import (consoles, settings, boot, omnibox)
6. DataAccess layer CRUD works
7. SSE client imports and initializes
8. Config detection (detect_cli) works
"""
import ast
import importlib
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
PROJECT_ROOT = HERE.parent.parent
FLEET_DIR = PROJECT_ROOT / "fleet"

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"

results = []

def check(name, fn):
    try:
        ok, detail = fn()
        status = PASS if ok else FAIL
        print(f"  [{status}] {name}: {detail}")
        results.append(ok)
        return ok
    except Exception as e:
        print(f"  [{FAIL}] {name}: {e}")
        results.append(False)
        return False


def test_launcher_syntax():
    """1. launcher.py parses without syntax errors."""
    source = (HERE / "launcher.py").read_text(encoding="utf-8")
    ast.parse(source)
    lines = len(source.splitlines())
    return True, f"{lines} lines, syntax OK"


def test_module_discovery():
    """2. Module discovery finds mod_*.py files."""
    modules_dir = HERE / "modules"
    mods = list(modules_dir.glob("mod_*.py"))
    names = [m.stem for m in mods]
    return len(mods) >= 6, f"{len(mods)} modules: {', '.join(sorted(names))}"


def test_module_manifest():
    """3. Module manifest loads and has valid structure."""
    manifest_path = HERE / "modules" / "manifest.json"
    if not manifest_path.exists():
        return False, "manifest.json not found"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    modules = data.get("modules", [])
    return len(modules) >= 1, f"{len(modules)} modules in manifest"


def test_fleet_dir():
    """4. Fleet dir resolves to a directory with fleet.toml."""
    fleet_toml = FLEET_DIR / "fleet.toml"
    return fleet_toml.exists(), f"fleet.toml at {FLEET_DIR}"


def test_ui_imports():
    """5. All UI extraction modules import cleanly."""
    sys.path.insert(0, str(HERE))
    failures = []
    for mod_name in ["ui.consoles", "ui.settings", "ui.boot", "ui.omnibox", "ui.sse_client"]:
        try:
            importlib.import_module(mod_name)
        except Exception as e:
            failures.append(f"{mod_name}: {e}")
    if failures:
        return False, "; ".join(failures)
    return True, "5 UI modules imported"


def test_data_access():
    """6. DataAccess CRUD round-trip."""
    sys.path.insert(0, str(HERE))
    from data_access import DataAccess
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    try:
        dal = DataAccess(tmp.name)
        dal.ensure_table("gui_test", {"name": "TEXT", "val": "INTEGER"})
        rid = dal.insert("gui_test", {"name": "smoke", "val": 1})
        rows = dal.query("gui_test")
        dal.close()
        import os
        os.unlink(tmp.name)
        return len(rows) == 1, f"insert id={rid}, query returned {len(rows)} row(s)"
    except Exception as e:
        import os
        os.unlink(tmp.name)
        raise


def test_sse_client():
    """7. SSE client initializes without connecting."""
    sys.path.insert(0, str(HERE))
    from ui.sse_client import SSEClient
    client = SSEClient("http://localhost:99999")  # bogus port, won't connect
    client.on("test", lambda d: None)
    assert not client.connected
    return True, "SSEClient created, not connected (expected)"


def test_detect_cli():
    """8. CLI detection returns valid result."""
    sys.path.insert(0, str(FLEET_DIR))
    from config import detect_cli
    info = detect_cli()
    return info.get("platform") is not None, f"{info['platform']}/{info['shell']}/{info['bridge']}"


def main():
    print("BigEd CC GUI Smoke Test (headless)")
    print("=" * 45)

    check("Launcher syntax", test_launcher_syntax)
    check("Module discovery", test_module_discovery)
    check("Module manifest", test_module_manifest)
    check("Fleet dir", test_fleet_dir)
    check("UI module imports", test_ui_imports)
    check("DataAccess CRUD", test_data_access)
    check("SSE client init", test_sse_client)
    check("CLI detection", test_detect_cli)

    print("=" * 45)
    passed = sum(results)
    total = len(results)
    print(f"Result: {passed}/{total} passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
