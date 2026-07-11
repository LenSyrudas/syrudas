"""Syrudas AI desktop app - native window (WebView2) wrapping the local server.

Entry point for the packaged SyrudasAI.exe. Runs uvicorn in a background
thread and shows the UI in a pywebview window; closing the window stops the
server. If the server is already running (another instance owns the port),
this just opens a window onto it. Falls back to the default browser if the
native webview cannot start (e.g. missing WebView2 runtime).
"""
import logging
import socket
import sys
import threading
import time

from server.config import DATA_DIR, HOST, PORT

URL = f"http://{HOST}:{PORT}"
LOG_FILE = DATA_DIR / "syrudas.log"


def _ensure_std_streams() -> None:
    """Windowed exes have no console; give uvicorn's log handlers a real stream."""
    if sys.stdout is None or sys.stderr is None:
        stream = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = stream
        if sys.stderr is None:
            sys.stderr = stream


def port_open() -> bool:
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex((HOST, PORT)) == 0


def wait_for_server(timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_open():
            return True
        time.sleep(0.2)
    return False


def main() -> None:
    _ensure_std_streams()
    logging.basicConfig(level=logging.INFO)
    log = logging.getLogger("syrudas.desktop")

    server = None
    server_thread = None
    if port_open():
        log.info("Server already running at %s - opening a window onto it", URL)
    else:
        import uvicorn

        from server.main import app

        config = uvicorn.Config(app, host=HOST, port=PORT, log_level="info")
        server = uvicorn.Server(config)
        server_thread = threading.Thread(target=server.run, daemon=True)
        server_thread.start()
        if not wait_for_server():
            log.error("Server failed to start within 30s - see %s", LOG_FILE)
            return

    try:
        import webview

        webview.create_window(
            "Syrudas AI", URL,
            width=1280, height=860, min_size=(900, 600),
        )
        # private_mode=False + storage_path: persist localStorage (model
        # picker, agent toggle) across launches
        webview.start(
            private_mode=False,
            storage_path=str(DATA_DIR / "webview"),
        )  # blocks until the window is closed
    except Exception:
        log.exception("Native window failed - falling back to the default browser")
        import webbrowser

        webbrowser.open(URL)
        try:
            while server_thread is not None and server_thread.is_alive():
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    finally:
        if server is not None:
            server.should_exit = True
        if server_thread is not None:
            server_thread.join(timeout=8)


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    main()
