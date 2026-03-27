"""BigEd CC launcher -- dispatches to PyWebView (preferred) or tkinter (fallback)."""
import os, sys

# Use Qt backend for pywebview on Windows (pythonnet doesn't support Python 3.14)
if sys.platform == "win32":
    os.environ.setdefault("PYWEBVIEW_GUI", "qt")

def main():
    try:
        import webview  # noqa: F401
        from launcher_webview import main as wv_main
        wv_main()
    except Exception:
        from launcher_tkinter import main as tk_main
        tk_main()


if __name__ == "__main__":
    main()
