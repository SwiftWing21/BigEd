# Build Reference — Fleet Manager App

All commands run from Windows cmd (not WSL) inside the `launcher/` directory.

```
cd C:\Users\max\Projects\Education\Max Stuff\launcher
```

---

## Full Build (build.bat)

Runs all steps: install deps → generate icons → build FleetControl.exe → build Updater.exe.

```bat
.\build.bat
```

Output: `dist\FleetControl.exe` and `dist\Updater.exe`

---

## Individual Steps

### Install / update dependencies
```bat
pip install -r requirements.txt
```

### Regenerate icons (brick_banner.png, brick.ico)
```bat
python generate_icon.py
```

### Build FleetControl.exe only
```bat
python -m PyInstaller --onefile --windowed --name "FleetControl" --icon "brick.ico" --add-data "brick_banner.png;." --add-data "brick.ico;." --collect-all customtkinter --hidden-import psutil --hidden-import pynvml launcher.py
```

### Build Updater.exe only
```bat
python -m PyInstaller --onefile --windowed --name "Updater" --icon "brick.ico" --add-data "brick.ico;." --collect-all customtkinter updater.py
```

---

## Rebuild Updater (rare)

Updater.exe can't overwrite itself while running. Close it first, then:

```bat
python -m PyInstaller --onefile --windowed --name "Updater" --icon "brick.ico" --add-data "brick.ico;." --collect-all customtkinter updater.py
```

---

## Clean Build (if PyInstaller acts stale)

```bat
rmdir /s /q dist build __pycache__
del *.spec
.\build.bat
```

---

## Python Setup (first time on a new machine)

```bat
winget install Python.Python.3.11
pip install -r requirements.txt
```

---

## Notes

- Run cmd as **Administrator** if GPU power limit features need to write via NVML
- `dist\` output is what gets installed — ship `dist\FleetControl.exe` + `dist\Updater.exe`
- `brick.ico` and `brick_banner.png` must exist before building — run `generate_icon.py` first
- PyInstaller `--add-data` uses `;` separator on Windows (not `:` like Linux)
- If a build fails with a `PermissionError` on an exe, that exe is still running — close it first
