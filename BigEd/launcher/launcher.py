"""BigEd CC launcher -- dispatches to PyWebView (preferred) or tkinter (fallback)."""

try:
    import webview  # noqa: F401 -- probe availability
    from launcher_webview import main
except ImportError:
    from launcher_tkinter import main


if __name__ == "__main__":
    main()
