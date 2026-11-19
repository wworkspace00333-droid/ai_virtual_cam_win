from __future__ import annotations

import logging
import os
import signal
import sys
from pathlib import Path


def _out_path(argv: list[str], flag: str) -> str | None:
    """The report path that must follow a self-test flag; None (after a usage line) when it is missing."""
    i = argv.index(flag)
    if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
        return argv[i + 1]
    print(f"usage: {flag} <report.json> [options]", file=sys.stderr)
    return None


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    if "--camera-loopback-test" in argv:
        from vcstudio.app.selftest import camera_loopback_selftest
        out = _out_path(argv, "--camera-loopback-test")
        if out is None:
            return 2
        opt = lambda k, d: argv[argv.index(k) + 1] if k in argv else d
        return camera_loopback_selftest(out, float(opt("--seconds", 6)), opt("--device", "AI Virtual Camera"),
                                        opt("--sink", "AI Virtual Camera Input"), float(opt("--fps", 60)))
    if "--leak-bisect" in argv or "--run-script" in argv:
        # dev: run a scripts/*.py tool inside the app bundle (it needs the bundle's camera permission)
        import runpy
        from vcstudio.core.resources import scripts_dir
        scripts = scripts_dir()
        if "--leak-bisect" in argv:
            i, name = argv.index("--leak-bisect"), "leak_bisect.py"
            rest = argv[i + 1:]
        else:
            i = argv.index("--run-script")
            name, rest = argv[i + 1], argv[i + 2:]
        target = (scripts / name).resolve()
        if target.parent != scripts or not target.exists():      # only tools shipped in scripts/
            print(f"unknown script {name}", file=sys.stderr)
            return 2
        sys.argv = [name] + rest
        runpy.run_path(str(target), run_name="__main__")
        return 0
    if "--soak" in argv:
        from vcstudio.app.soak import main_entry
        return main_entry(argv)
    if "--pipeline-e2e-test" in argv:
        from vcstudio.app.selftest import pipeline_e2e_selftest
        out = _out_path(argv, "--pipeline-e2e-test")
        if out is None:
            return 2
        opt = lambda k, d: argv[argv.index(k) + 1] if k in argv else d
        return pipeline_e2e_selftest(out, float(opt("--seconds", 8)), opt("--device", "AI Virtual Camera"),
                                     opt("--sink", "AI Virtual Camera Input"), opt("--input", None),
                                     opt("--pixel-format", None))
    if "--self-check" in argv:
        from vcstudio.app.selftest import bundle_selfcheck
        i = argv.index("--self-check")
        nxt = argv[i + 1] if i + 1 < len(argv) and not argv[i + 1].startswith("--") else None
        return bundle_selfcheck(nxt)
    if "--capture-selftest" in argv:
        from vcstudio.app.selftest import capture_selftest
        out = _out_path(argv, "--capture-selftest")
        if out is None:
            return 2
        secs = float(argv[argv.index("--seconds") + 1]) if "--seconds" in argv else 10.0
        hint = argv[argv.index("--device") + 1] if "--device" in argv else "OBS Virtual Camera"
        ai = argv[argv.index("--with-ai") + 1] if "--with-ai" in argv else None
        return capture_selftest(out, secs, hint, ai)

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from vcstudio import APP_NAME, __version__
    from vcstudio.app.application import AppContext
    from vcstudio.ui.main_window import MainWindow
    from vcstudio.ui.theme import QSS

    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName("AIVCStudio")
    app.setStyleSheet(QSS)
    from vcstudio.core import single_instance
    from vcstudio.core.paths import AppPaths
    other = single_instance.acquire(AppPaths.default().root)
    if other is not None:
        from PySide6.QtWidgets import QMessageBox

        from vcstudio import i18n
        i18n.set_language(i18n.resolve("auto"))
        from vcstudio.platform import text
        msg = i18n.tr(text("another_copy_running"), pid=other or "?")
        print(msg, file=sys.stderr)
        if "--snapshot" not in argv and "--smoke-test" not in argv:
            QMessageBox.information(None, APP_NAME, msg)
        return 3
    ctx = AppContext()
    if sys.platform == "win32":
        try:                                                 # D13: a stale "start with Windows" entry follows this exe
            from vcstudio.platform.win import autostart
            if autostart.repair():
                ctx.db.log_event("INFO", "app", "start-with-Windows entry updated to this installation")
        except OSError as e:
            logging.getLogger(__name__).warning("autostart check failed: %s", e)
    from vcstudio import i18n
    lang = argv[argv.index("--lang") + 1] if "--lang" in argv else ctx.settings.get("ui.language")
    i18n.set_language(i18n.resolve(lang))
    if ("--smoke-test" not in argv and "--snapshot" not in argv and "--lang" not in argv
            and not os.environ.get("VCS_NO_WIZARD")):
        from vcstudio.ui.first_run import run_if_needed
        if run_if_needed(ctx.settings, ctx.models):          # first start: language, source, mode
            i18n.set_language(i18n.resolve(ctx.settings.get("ui.language")))

    def excepthook(t, v, tb):
        logging.getLogger("vcstudio.crash").critical("Unhandled exception", exc_info=(t, v, tb))
        try:
            ctx.db.log_event("ERROR", "app", f"unhandled {t.__name__}: {v}")
        except Exception:
            pass
    sys.excepthook = excepthook

    win = MainWindow(ctx)
    if "--minimized" in argv and win.tray is not None:      # Windows sign-in autostart: start in the tray
        app.setQuitOnLastWindowClosed(False)
    else:
        win.show()
    if win.tray is not None:
        app.setQuitOnLastWindowClosed(False)                 # hiding to the tray must not end the app
    if "--smoke-test" not in argv and "--snapshot" not in argv:
        # first start only: measure this PC and pick the performance mode (vcstudio/ai/auto_profile.py)
        from PySide6.QtCore import QObject, Signal

        from vcstudio.ai import auto_profile

        class _AutoSig(QObject):
            done = Signal(object)
        auto_sig = _AutoSig()

        def _apply(r):
            if auto_profile.apply_result(ctx, r):
                win.performance.sync_mode()
                win.dashboard.refresh_profile()
        auto_sig.done.connect(_apply)
        auto_profile.choose_once(ctx.settings, ctx.models, auto_sig.done.emit)
    # Let Ctrl+C in a terminal quit cleanly
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    keepalive = QTimer(); keepalive.start(250); keepalive.timeout.connect(lambda: None)
    if "--smoke-test" in argv:  # launch, render, quit (used by tests/CI)
        QTimer.singleShot(1500, app.quit)
    if "--autostart-capture" in argv:
        QTimer.singleShot(300, ctx.start_capture)
    if "--snapshot" in argv:  # dev aid: save page screenshots after capture warms up, then quit
        out = Path(argv[argv.index("--snapshot") + 1]); out.mkdir(parents=True, exist_ok=True)

        def snap():
            keys = win.page_keys() if "--snapshot-all" in argv else ("preview", "ai", "detection", "dashboard")
            for key in keys:
                win.goto(key)
                for _ in range(10):
                    app.processEvents(); win.feeder.tick()
                win._tick(); app.processEvents()
                win.grab().save(str(out / f"{key}.png"))
            app.quit()
        QTimer.singleShot(5000, snap)
    rc = app.exec()
    ctx.shutdown()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
