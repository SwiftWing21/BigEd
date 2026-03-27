"""BigEd CC launcher -- dispatches to PyWebView (preferred) or tkinter (fallback)."""

def main():
    # PyWebView needs pythonnet on Windows, which requires Python <=3.13
    # Fall back to tkinter if webview can't initialize
    try:
        import webview
        # Probe that the GUI backend actually works (not just importable)
        webview.guilib.initialize()
        from launcher_webview import main as wv_main
        wv_main()
    except Exception:
        from launcher_tkinter import main as tk_main
        tk_main()


if __name__ == "__main__":
    main()
