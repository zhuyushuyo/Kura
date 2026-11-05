"""Start the real main window offscreen and click through the pages/dialogs."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

SCRIPT = textwrap.dedent(r"""
    import os, sys
    sys.path.insert(0, sys.argv[1])
    from kura import paths
    from kura.config import Settings
    paths.ensure_dirs()
    s = Settings()
    s.update({"auto_update": False, "first_run_done": True, "home_url": "about:blank", "language": "ja",
              "clipboard_monitor": False})
    from kura.app import _set_environment
    _set_environment(s)
    from PyQt6 import QtWebEngineWidgets  # noqa
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    from kura.ui.theme import THEME
    THEME.apply("dark")
    from kura.core.library import Database
    from kura.ui.context import AppContext
    from kura.ui.main_window import MainWindow
    from kura.ui import dialogs
    ctx = AppContext(s, Database())
    win = MainWindow(ctx)
    win.show()
    errors = []

    def step():
        try:
            for i in range(4):
                win.show_page(i)
                app.processEvents()
            win.settings_page.scroll_to("engine")
            THEME.apply("light")
            for cls, args in ((dialogs.AddUrlsDialog, ("https://youtu.be/abcdefghijk",)),
                              (dialogs.FirstRunDialog, ()), (dialogs.FfmpegSetupDialog, ())):
                d = cls(ctx, *args, parent=win) if args else cls(ctx, parent=win)
                d.show(); app.processEvents(); d.close()
            p = dialogs.PresetEditorDialog(ctx, ctx.preset("edit1080"), win)
            p.show(); app.processEvents(); assert p.result_preset().vcodec == "h264"; p.close()
            win.browse.panel.set_url("https://www.youtube.com/playlist?list=PLx")
            assert win.browse.panel.state == "collection"
            win.browse.panel.set_url("")
            assert win.browse.panel.state == "empty"
            ctx.notify("toast test", "success")
            app.processEvents()
        except Exception as e:  # noqa
            import traceback; traceback.print_exc(); errors.append(e)
        finally:
            win.queue.stop_all_for_exit()
            app.quit()

    QTimer.singleShot(500, step)
    app.exec()
    win.shutdown()
    sys.exit(1 if errors else 0)
""")


def test_main_window_smoke(tmp_path):
    pytest.importorskip("PyQt6.QtWebEngineWidgets")
    env = dict(os.environ, KURA_HOME=str(tmp_path / "home"), QT_QPA_PLATFORM="offscreen")
    script = tmp_path / "smoke.py"
    script.write_text(SCRIPT, encoding="utf-8")
    proc = subprocess.run([sys.executable, str(script), str(ROOT)], env=env, capture_output=True, text=True,
                          timeout=120)
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]
    assert "Traceback" not in proc.stderr, proc.stderr[-3000:]
