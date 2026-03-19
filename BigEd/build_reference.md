# Build Reference — BigEd CC

> Multi-platform build guide. Currently Windows-only in production; Linux and macOS builds planned.

---

## 1. Windows (Current — Production)

All commands run from Windows cmd (not WSL) inside the `launcher/` directory.

```
cd C:\Users\max\Projects\Education\BigEd\launcher
```

### Full Build (build.bat)

Runs all steps: install deps → generate icons → build BigEdCC.exe → build Updater.exe.

```bat
.\build.bat
```

Output: `dist\BigEdCC.exe` and `dist\Updater.exe`

### Individual Steps

**Install / update dependencies:**
```bat
pip install -r requirements.txt
```

**Regenerate icons (brick_banner.png, brick.ico):**
```bat
python generate_icon.py
```

**Build BigEdCC.exe only:**
```bat
python -m PyInstaller --onefile --windowed --name "BigEdCC" --icon "brick.ico" --add-data "brick_banner.png;." --add-data "brick.ico;." --collect-all customtkinter --hidden-import psutil --hidden-import pynvml launcher.py
```

**Build Updater.exe only:**
```bat
python -m PyInstaller --onefile --windowed --name "Updater" --icon "brick.ico" --add-data "brick.ico;." --collect-all customtkinter updater.py
```

### Clean Build

```bat
rmdir /s /q dist build __pycache__
del *.spec
.\build.bat
```

### Notes

- Run cmd as **Administrator** if GPU power limit features need to write via NVML
- `dist\` output is what gets installed — ship `dist\BigEdCC.exe` + `dist\Updater.exe`
- `brick.ico` and `brick_banner.png` must exist before building — run `generate_icon.py` first
- PyInstaller `--add-data` uses **`;`** separator on Windows (not `:` like Linux/macOS)
- If a build fails with `PermissionError` on an exe, that exe is still running — close it first

---

## 2. Linux (Planned)

### AppImage Build

```bash
cd BigEd/launcher
pip install -r requirements.txt

# Build with : separator (Linux/macOS PyInstaller convention)
python -m PyInstaller --onefile --windowed --name "BigEdCC" \
  --icon "brick.ico" \
  --add-data "brick_banner.png:." \
  --add-data "brick.ico:." \
  --collect-all customtkinter \
  --hidden-import psutil --hidden-import pynvml \
  launcher.py

# Package as AppImage (using appimage-builder or similar)
# Output: BigEdCC-x86_64.AppImage
```

### Desktop Integration

```bash
# Install .desktop file
cp BigEdCC.desktop ~/.local/share/applications/
update-desktop-database ~/.local/share/applications/
```

### Notes

- Requires `python3-tk` system package (`sudo apt install python3-tk`)
- No WSL layer — fleet runs natively alongside launcher
- `--add-data` separator is **`:`** (not `;`)

---

## 3. macOS (Planned)

### .app Bundle

```bash
cd BigEd/launcher
pip install -r requirements.txt

python -m PyInstaller --onefile --windowed --name "BigEdCC" \
  --icon "brick.icns" \
  --add-data "brick_banner.png:." \
  --add-data "brick.ico:." \
  --collect-all customtkinter \
  --hidden-import psutil \
  launcher.py

# Note: --hidden-import pynvml omitted (no NVIDIA on Mac)
```

### DMG Packaging

```bash
# Create DMG for distribution (using create-dmg or hdiutil)
create-dmg --volname "BigEdCC" --window-size 600 400 \
  --app-drop-link 400 200 \
  BigEdCC.dmg dist/BigEdCC.app
```

### Code Signing

```bash
# Sign for Gatekeeper (requires Apple Developer ID)
codesign --deep --force --sign "Developer ID Application: ..." dist/BigEdCC.app

# Notarize (optional, prevents "unidentified developer" warning)
xcrun notarytool submit BigEdCC.dmg --apple-id ... --password ... --team-id ...
```

### Notes

- Need `brew install python-tk@3.11` if tkinter import fails
- `--add-data` separator is **`:`** (same as Linux)
- No `pynvml` — GPU stats disabled on macOS
- Convert `brick.ico` to `brick.icns` for native macOS icon

---

## 4. Planned: Cross-Platform `build.py`

Replace `build.bat` with a Python build script that works on all platforms:

```python
# build.py (planned)
import sys, subprocess, shutil
from pathlib import Path

SEP = ";" if sys.platform == "win32" else ":"
NAME = "BigEdCC"
ICON = "brick.ico" if sys.platform != "darwin" else "brick.icns"

cmd = [
    sys.executable, "-m", "PyInstaller",
    "--onefile", "--windowed",
    "--name", NAME,
    "--icon", ICON,
    f"--add-data=brick_banner.png{SEP}.",
    f"--add-data=brick.ico{SEP}.",
    "--collect-all", "customtkinter",
    "--hidden-import", "psutil",
]

if sys.platform != "darwin":
    cmd += ["--hidden-import", "pynvml"]

cmd.append("launcher.py")
subprocess.run(cmd, check=True)
```

Benefits:
- Single entry point: `python build.py` on any OS
- Auto-detects `--add-data` separator (`;` on Windows, `:` elsewhere)
- Skips `pynvml` on macOS (no NVIDIA GPU)
- Replaces Windows-only `build.bat`

---

## 5. CI/CD Build Matrix (Planned)

GitHub Actions workflow targeting all 3 platforms:

```yaml
# .github/workflows/build.yml
name: Build
on: [push, pull_request]

jobs:
  build:
    strategy:
      matrix:
        os: [windows-latest, ubuntu-latest, macos-latest]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -r BigEd/launcher/requirements.txt
      - run: python BigEd/launcher/build.py
      - uses: actions/upload-artifact@v4
        with:
          name: BigEdCC-${{ matrix.os }}
          path: BigEd/launcher/dist/
```

### Build Matrix

| Platform | Output | Packaging | Separator |
|----------|--------|-----------|-----------|
| Windows | `BigEdCC.exe` + `Updater.exe` | PyInstaller `.exe` | `;` |
| Linux | `BigEdCC` (binary) | AppImage | `:` |
| macOS | `BigEdCC.app` | `.app` + DMG | `:` |

---

## Python Setup (First Time)

**Windows:**
```bat
winget install Python.Python.3.11
pip install -r requirements.txt
```

**Linux (Ubuntu/Debian):**
```bash
sudo apt install python3.11 python3-tk python3-pip
pip install -r requirements.txt
```

**macOS:**
```bash
brew install python@3.11 python-tk@3.11
pip install -r requirements.txt
```
