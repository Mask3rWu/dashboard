"""Flight Analyzer — FastAPI backend + pywebview desktop shell."""

import os
import sys
import threading
import time as _time
import traceback
import ctypes

# Add parent to path for PyInstaller compatibility
if getattr(sys, 'frozen', False):
    BASE_DIR = sys._MEIPASS
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Ensure BASE_DIR and backend are importable
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from backend.config import load_app_config

CONFIG_PATH = load_app_config()

from backend.database import init_db, DATA_DIR, DB_PATH
from backend.api.desktop.app import (
    STARTUP_LOG_PATH,
    create_app,
    startup_log as _startup_log,
)


# ─── Serve Frontend ────────────────────────────────────────

FRONTEND_DIR = os.path.join(BASE_DIR, "frontend", "dist")


# ─── App Setup ─────────────────────────────────────────────

app = create_app(FRONTEND_DIR)




# ─── Entry Point ───────────────────────────────────────────

def _show_error(title, msg):
    """Show error to user — MessageBox in GUI mode, stderr otherwise."""
    full_msg = f"{msg}\n\n日志文件: {STARTUP_LOG_PATH}"
    _startup_log(f"{title}: {msg}")
    if sys.platform == 'win32':
        try:
            ctypes.windll.user32.MessageBoxW(0, str(full_msg), str(title), 0x10)
            return
        except Exception:
            pass
    try:
        if sys.stderr:
            print(f"[{title}] {full_msg}", file=sys.stderr)
    except Exception:
        pass


_server_error = None


def _build_log_config():
    """Build a uvicorn log config for PyInstaller frozen (no-console) mode.

    Why this exists: under ``console=False`` (FlightAnalyzer.spec), PyInstaller
    leaves ``sys.stdout`` as ``None``. uvicorn's default formatter calls
    ``sys.stdout.isatty()`` and crashes with ``AttributeError: 'NoneType'
    object has no attribute 'isatty'``. This config forces ``use_colors=False``
    and routes both handlers to ``ext://sys.stderr`` (which is still attached),
    sidestepping the ``None`` stdout. Dev mode (non-frozen) keeps uvicorn's
    default config via ``run_server`` so colored output is preserved.
    """
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "()": "uvicorn.logging.DefaultFormatter",
                "fmt": "%(levelprefix)s %(message)s",
                "use_colors": False,
            },
            "access": {
                "()": "uvicorn.logging.AccessFormatter",
                "fmt": '%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
                "use_colors": False,
            },
        },
        "handlers": {
            "default": {
                "formatter": "default",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stderr",
            },
            "access": {
                "formatter": "access",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stderr",
            },
        },
        "loggers": {
            "uvicorn": {"handlers": ["default"], "level": "INFO", "propagate": False},
            "uvicorn.error": {"level": "INFO"},
            "uvicorn.access": {"handlers": ["access"], "level": "INFO", "propagate": False},
        },
    }


def _find_available_port(start=18520, max_attempts=10):
    """Return the first available localhost port in a small range."""
    import socket
    for offset in range(max_attempts):
        port = start + offset
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(("127.0.0.1", port))
            _startup_log(f"Port {port} is available")
            return port
        except OSError as e:
            _startup_log(f"Port {port} is unavailable: {e}")
        finally:
            sock.close()
    return None


def _sleep_forever():
    try:
        while True:
            _time.sleep(1)
    except KeyboardInterrupt:
        pass


def run_server(port=18520):
    """Start uvicorn in a daemon thread."""
    global _server_error
    import uvicorn
    _startup_log(f"Starting uvicorn on http://127.0.0.1:{port}")
    try:
        if getattr(sys, 'frozen', False):
            uvicorn.run(
                app, host="127.0.0.1", port=port,
                log_config=_build_log_config(),
            )
        else:
            uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
    except Exception as e:
        _server_error = f"{e}\n{traceback.format_exc()}"
        _startup_log(f"Server failed to start: {_server_error}")
        _show_error("Server Error", f"Server failed to start:\n{_server_error}")


def _wait_for_server(server_thread, port, timeout=10):
    """Wait until the server thread is actually listening on the port."""
    import socket
    start = _time.time()
    while _time.time() - start < timeout:
        if not server_thread.is_alive() or _server_error is not None:
            return False
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=0.3)
            s.close()
            return True
        except (OSError, ConnectionRefusedError):
            _time.sleep(0.2)
    return False


def main():
    _startup_log("=== Starting Flight Analyzer ===")
    _startup_log(f"BASE_DIR={BASE_DIR}")
    _startup_log(f"FRONTEND_DIR={FRONTEND_DIR} exists={os.path.isdir(FRONTEND_DIR)}")
    _startup_log(f"DATA_DIR={DATA_DIR}")
    _startup_log(f"DB_PATH={DB_PATH}")

    try:
        db_result = init_db()
        _startup_log(f"Database initialized: {db_result}")
    except Exception as e:
        details = f"{e}\n{traceback.format_exc()}"
        _startup_log(f"Database initialization failed: {details}")
        _show_error(
            "Database Error",
            "数据库初始化失败，应用无法启动。\n\n"
            f"数据目录: {DATA_DIR}\n"
            f"数据库: {DB_PATH}\n\n"
            f"错误: {e}",
        )
        sys.exit(1)

    port = _find_available_port(18520, 10)
    if port is None:
        _show_error(
            "Startup Error",
            "无法找到可用的本地端口（18520-18529）。\n"
            "请关闭其他实例或占用这些端口的程序后重试。",
        )
        sys.exit(1)

    server_thread = threading.Thread(target=run_server, args=(port,), daemon=True)
    server_thread.start()

    if not _wait_for_server(server_thread, port, timeout=15):
        msg = f"Server did not start on port {port} within timeout.\n\n"
        if _server_error:
            msg += f"Server error:\n{_server_error}"
        else:
            msg += "Check startup.log for backend import or startup errors."
        _startup_log(msg)
        _show_error("Startup Error", msg)
        sys.exit(1)

    app_url = f"http://127.0.0.1:{port}"
    _startup_log(f"Server ready at {app_url}")

    if os.path.isdir(FRONTEND_DIR):
        try:
            import webview
            _startup_log("Opening pywebview window")
            webview.create_window(
                "Flight Analyzer",
                app_url,
                width=1400, height=900,
                min_size=(1024, 680),
            )
            # Keep DevTools available on demand (F12/right-click -> Inspect)
            # without opening the developer console on every launch.
            webview.settings['OPEN_DEVTOOLS_IN_DEBUG'] = False
            webview.start(debug=True)
        except ImportError as e:
            _startup_log(f"pywebview unavailable, falling back to browser: {e}")
            import webbrowser
            webbrowser.open(app_url)
            _sleep_forever()
        except Exception as e:
            _startup_log(f"pywebview failed: {e}\n{traceback.format_exc()}")
            _show_error("WebView Error", f"桌面窗口启动失败：\n{e}")
            sys.exit(1)
    else:
        _startup_log("Frontend dist not found, falling back to browser")
        import webbrowser
        webbrowser.open(app_url)
        _sleep_forever()


if __name__ == "__main__":
    main()
