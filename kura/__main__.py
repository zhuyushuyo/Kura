"""``python -m kura`` — start the Kura app."""

import os
import sys
import traceback


def _run() -> int:
    # pythonw.exe (Windows, no console) has no stdout/stderr; some libraries write there.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    try:
        from kura.app import main
    except Exception:  # noqa: BLE001 - e.g. a missing package
        _report_startup_failure(traceback.format_exc())
        return 1
    return main()


def _report_startup_failure(text: str) -> None:
    """Write the error somewhere visible even when started without a console."""
    try:
        from kura import paths

        paths.LOG_DIR.mkdir(parents=True, exist_ok=True)
        (paths.LOG_DIR / "startup-error.log").write_text(text, encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    sys.stderr.write(text)
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, text[-1500:], "Kura - startup error", 0x10)
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    sys.exit(_run())
