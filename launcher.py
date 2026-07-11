"""Syrudas AI one-click launcher - entry point for the packaged exe.

Starts the server and opens the browser. If Syrudas is already running,
just brings up the UI instead of starting a second instance.
"""
import socket
import threading
import time
import webbrowser

from server.config import HOST, PORT

URL = f"http://{HOST}:{PORT}"


def port_open() -> bool:
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex((HOST, PORT)) == 0


def open_browser_when_ready(timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_open():
            webbrowser.open(URL)
            return
        time.sleep(0.3)


def main() -> None:
    if port_open():
        print(f"Syrudas AI is already running - opening {URL}")
        webbrowser.open(URL)
        time.sleep(2)
        return

    print("=" * 46)
    print(f"  Syrudas AI  -  {URL}")
    print("  Close this window to stop the server.")
    print("=" * 46)
    threading.Thread(target=open_browser_when_ready, daemon=True).start()

    import uvicorn

    from server.main import app

    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    main()
