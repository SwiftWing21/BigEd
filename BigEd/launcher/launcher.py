"""BigEd CC launcher."""
import os, sys

if sys.platform in ("win32", "linux"):
    os.environ.setdefault("PYWEBVIEW_GUI", "qt")

def main():
    from launcher_webview import main as wv_main
    wv_main()

if __name__ == "__main__":
    main()
