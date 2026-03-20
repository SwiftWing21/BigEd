#!/usr/bin/env python3
"""
Pre-release validation — run before tagging a GitHub release.
Checks that the build will succeed on GitHub Actions (windows-latest).

Usage:
    python scripts/pre_release_check.py
    python scripts/pre_release_check.py --tag v0.50.02b
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent.parent
LAUNCHER = HERE / "BigEd" / "launcher"
FLEET = HERE / "fleet"

GREEN = "\033[32m"
RED = "\033[31m"
GOLD = "\033[33m"
DIM = "\033[2m"
RESET = "\033[0m"

errors = []
warnings = []


def check(label, ok, detail=""):
    status = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
    print(f"  {status}  {label}")
    if detail and not ok:
        print(f"         {DIM}{detail}{RESET}")
    if not ok:
        errors.append(label)


def warn(label, detail=""):
    print(f"  {GOLD}WARN{RESET}  {label}")
    if detail:
        print(f"         {DIM}{detail}{RESET}")
    warnings.append(label)


def main():
    tag = ""
    if "--tag" in sys.argv:
        idx = sys.argv.index("--tag")
        if idx + 1 < len(sys.argv):
            tag = sys.argv[idx + 1]

    print(f"\n{GOLD}Pre-Release Check{RESET}")
    if tag:
        print(f"  Tag: {tag}")
    print()

    # 1. Required files exist
    print(f"{GOLD}[1/6] Required files{RESET}")
    check("requirements.txt", (LAUNCHER / "requirements.txt").exists())
    check("generate_icon.py", (LAUNCHER / "generate_icon.py").exists())
    check("build.py", (LAUNCHER / "build.py").exists())
    check("launcher.py", (LAUNCHER / "launcher.py").exists())
    check("installer.py", (LAUNCHER / "installer.py").exists())
    check("updater.py", (LAUNCHER / "updater.py").exists())
    check("release.yml", (HERE / ".github" / "workflows" / "release.yml").exists())

    # 2. Python compilation
    print(f"\n{GOLD}[2/6] Compilation check{RESET}")
    import py_compile
    py_files = list(LAUNCHER.rglob("*.py")) + list(FLEET.rglob("*.py"))
    compile_ok = 0
    compile_fail = 0
    for f in py_files:
        try:
            py_compile.compile(str(f), doraise=True)
            compile_ok += 1
        except py_compile.PyCompileError as e:
            compile_fail += 1
            if compile_fail <= 3:
                print(f"  {RED}FAIL{RESET}  {f.relative_to(HERE)}: {e}")
    check(f"{compile_ok} files compile", compile_fail == 0,
          f"{compile_fail} files failed")

    # 3. PyInstaller available
    print(f"\n{GOLD}[3/6] Build tools{RESET}")
    r = subprocess.run([sys.executable, "-m", "PyInstaller", "--version"],
                       capture_output=True, text=True)
    check("PyInstaller installed", r.returncode == 0,
          "pip install pyinstaller" if r.returncode != 0 else f"v{r.stdout.strip()}")

    # 4. Icon generation
    print(f"\n{GOLD}[4/6] Icon generation{RESET}")
    r = subprocess.run([sys.executable, str(LAUNCHER / "generate_icon.py")],
                       capture_output=True, text=True, cwd=str(LAUNCHER))
    check("generate_icon.py runs", r.returncode == 0,
          r.stderr[:100] if r.returncode != 0 else "")
    check("brick.ico exists", (LAUNCHER / "brick.ico").exists(),
          "generate_icon.py must create brick.ico")
    check("brick_banner.png exists", (LAUNCHER / "brick_banner.png").exists(),
          "generate_icon.py must create brick_banner.png")

    # 5. Requirements installable
    print(f"\n{GOLD}[5/6] Dependencies{RESET}")
    r = subprocess.run([sys.executable, "-m", "pip", "check"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        warn("pip check has issues", r.stdout[:200])
    else:
        check("pip check clean", True)

    # 6. GitHub Actions config
    print(f"\n{GOLD}[6/6] GitHub Actions{RESET}")
    release_yml = (HERE / ".github" / "workflows" / "release.yml").read_text()
    check("Node.js 24 flag set", "FORCE_JAVASCRIPT_ACTIONS_TO_NODE24" in release_yml,
          "Add env: FORCE_JAVASCRIPT_ACTIONS_TO_NODE24: true")
    check("brick.ico copy step", "Copy-Item" in release_yml and "brick.ico" in release_yml,
          "Workflow must copy icons to dist/ before zipping")
    check("Python 3.12 specified", "python-version: '3.12'" in release_yml)

    # Check gh CLI for Actions status
    try:
        r = subprocess.run(["gh", "api", "repos/SwiftWing21/BigEd/actions/runs",
                           "--jq", ".workflow_runs[0].conclusion"],
                          capture_output=True, text=True, timeout=10)
        last = r.stdout.strip()
        check(f"Last CI run: {last}", last == "success",
              "Fix CI failures before releasing")
    except Exception:
        warn("gh CLI not available — can't check CI status")

    # Summary
    print(f"\n{'=' * 40}")
    if errors:
        print(f"{RED}BLOCKED: {len(errors)} check(s) failed{RESET}")
        for e in errors:
            print(f"  - {e}")
        print(f"\nFix these before tagging a release.")
        return 1
    elif warnings:
        print(f"{GOLD}READY with {len(warnings)} warning(s){RESET}")
    else:
        print(f"{GREEN}ALL CHECKS PASSED — ready to release{RESET}")

    if tag:
        print(f"\nTo release:  git tag {tag} && git push origin {tag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
