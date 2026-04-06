"""BigEd CC launcher."""
import argparse
import os
import sys

if sys.platform in ("win32", "linux"):
    os.environ.setdefault("PYWEBVIEW_GUI", "qt")


def main():
    parser = argparse.ArgumentParser(description="BigEd CC Launcher")
    parser.add_argument(
        "--connect-to",
        default=None,
        help="Service URL (e.g. http://localhost:5555). "
             "When set, the launcher skips spawning its own supervisor "
             "and connects to the Rust service tier instead.",
    )
    args = parser.parse_args()

    if args.connect_to:
        os.environ["BIGED_SERVICE_URL"] = args.connect_to

    from launcher_webview import main as wv_main
    wv_main()


if __name__ == "__main__":
    main()
