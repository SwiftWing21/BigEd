# Rule: Windows Compatibility

BigEd CC is **Windows-native by default**. All core features must work on Windows 11
without WSL. Apply these rules to every Python file in `fleet/` and `BigEd/`.

---

## Process Management

**Do not use:**
- `pkill`, `pgrep`, `kill` (shell commands — POSIX only)
- `os.kill(pid, signal.SIGKILL)` — unreliable on Windows
- `subprocess.run(["kill", ...])` — not available on Windows

**Use instead:**
```python
import psutil
proc = psutil.Process(pid)
proc.terminate()   # sends SIGTERM on POSIX, TerminateProcess on Windows
proc.wait(timeout=5)
```

---

## Subprocess: No Window Flash

All background subprocesses must suppress the console window on Windows:

```python
import subprocess, sys

kwargs = {}
if sys.platform == "win32":
    kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

subprocess.Popen(["some_command", arg], **kwargs)
```

Without `CREATE_NO_WINDOW`, every subprocess spawns a visible console window.

---

## Python Runner

**Do not use `uv run`** on Windows — it is WSL-only in this project.

```python
# Wrong
subprocess.run(["uv", "run", "python", "script.py"])

# Correct
subprocess.run([sys.executable, "script.py"])
```

Or use `python` directly when running from Git Bash:
```bash
python fleet/smoke_test.py
```

---

## Path Handling

- Use `pathlib.Path` everywhere — it normalises separators on all platforms.
- Never hardcode `\` path separators.
- For Ollama on Windows, the binary is not on the default PATH. Use:
  ```python
  import os
  ollama_default = Path(os.getenv("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
  ```

---

## Temp Files

Use `tempfile.mkstemp()` or `tempfile.TemporaryDirectory()` — avoid hardcoded `/tmp/`.

---

## Line Endings

Open text files with `encoding="utf-8"`. For cross-platform consistency, do not rely on
the system default encoding (`locale.getpreferredencoding()`).

---

## Testing on Windows

Run the full smoke suite on Windows before merging:
```bash
python fleet/smoke_test.py --fast
```

All 22 tests must pass. Tests that require POSIX features must be skipped via
`@pytest.mark.skipif(sys.platform != "linux", ...)`.
