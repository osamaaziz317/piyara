"""Run the FM Solutions desktop prototype with an isolated browser profile."""

import argparse
import hmac
import json
import mimetypes
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import threading
from urllib.parse import unquote, urlsplit


WEB_ROOT = Path(__file__).resolve().parent / "web"
ADDRESS = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_WINDOW_SIZE = (1440, 940)
WINDOW_ACTIONS = {"minimize", "maximize", "restore", "close-window", "resize-login", "default-size"}
WEB_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}


def installed_browser():
    names = ("msedge.exe", "chrome.exe") if sys.platform == "win32" else (
        "google-chrome", "chromium", "chromium-browser", "microsoft-edge"
    )
    for name in names:
        program = shutil.which(name)
        if program:
            return program
    if sys.platform == "win32":
        locations = ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA")
        for relative in ("Microsoft/Edge/Application/msedge.exe", "Google/Chrome/Application/chrome.exe"):
            for location in locations:
                if location in os.environ:
                    candidate = Path(os.environ[location]) / relative
                    if candidate.is_file():
                        return str(candidate)
    return None


def desktop_profile():
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "FM Solutions" / "Prototype"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "fm-solutions" / "prototype"


def control_windows_browser(process_id, action, size=None):
    from ctypes import WINFUNCTYPE, POINTER, Structure, byref, create_unicode_buffer, sizeof, windll, wintypes

    user32 = windll.user32
    windows = []
    enum_callback = WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, wintypes.LPDWORD]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, wintypes.INT]
    user32.ShowWindowAsync.argtypes = [wintypes.HWND, wintypes.INT]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.EnumWindows.argtypes = [enum_callback, wintypes.LPARAM]
    user32.GetWindowRect.argtypes = [wintypes.HWND, POINTER(wintypes.RECT)]
    user32.GetClientRect.argtypes = [wintypes.HWND, POINTER(wintypes.RECT)]
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.MonitorFromWindow.restype = wintypes.HANDLE

    class MonitorInfo(Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
        ]

    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, POINTER(MonitorInfo)]
    user32.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND, wintypes.INT, wintypes.INT,
        wintypes.INT, wintypes.INT, wintypes.UINT,
    ]

    @enum_callback
    def collect(window, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(window, byref(owner))
        if owner.value == process_id and user32.IsWindowVisible(window):
            title_class = create_unicode_buffer(80)
            user32.GetClassNameW(window, title_class, len(title_class))
            if title_class.value == "Chrome_WidgetWin_1":
                windows.append(window)
        return True

    user32.EnumWindows(collect, 0)
    if not windows:
        return False
    if action == "close-window":
        return bool(user32.PostMessageW(windows[0], 0x0010, 0, 0))
    if action in {"resize-login", "default-size"}:
        window = windows[0]
        if action == "default-size":
            width, height = DEFAULT_WINDOW_SIZE
        else:
            width, height, viewport_width, viewport_height = size
            outer = wintypes.RECT()
            client = wintypes.RECT()
            if not user32.GetWindowRect(window, byref(outer)) or not user32.GetClientRect(window, byref(client)):
                return False
            client_width = client.right - client.left
            client_height = client.bottom - client.top
            scale_x = client_width / viewport_width
            scale_y = client_height / viewport_height
            width = round(width * scale_x) + (outer.right - outer.left - client_width)
            height = round(height * scale_y) + (outer.bottom - outer.top - client_height)
            monitor = user32.MonitorFromWindow(window, 2)
            monitor_info = MonitorInfo()
            monitor_info.cbSize = sizeof(MonitorInfo)
            if not monitor or not user32.GetMonitorInfoW(monitor, byref(monitor_info)):
                return False
            work = monitor_info.rcWork
            x = work.left + (work.right - work.left - width) // 2
            y = work.top + (work.bottom - work.top - height) // 2
            flags = 0x0004 | 0x0010
        if action == "default-size":
            x = y = 0
            flags = 0x0002 | 0x0004 | 0x0010
        return bool(user32.SetWindowPos(window, None, x, y, width, height, flags))
    show_command = {"minimize": 6, "maximize": 3, "restore": 9}[action]
    return bool(user32.ShowWindowAsync(windows[0], show_command))


class PrototypeServer(ThreadingHTTPServer):
    daemon_threads = True
    browser = None

    def __init__(self, port, desktop_window):
        super().__init__((ADDRESS, port), PrototypeHandler)
        self.host_header = f"{ADDRESS}:{self.server_port}"
        self.origin = f"http://{self.host_header}"
        self.window_token = secrets.token_urlsafe(24) if desktop_window and sys.platform == "win32" else ""


class PrototypeHandler(BaseHTTPRequestHandler):
    """Serve only the embedded frontend and the optional native window controls."""

    def log_message(self, _format, *args):
        pass

    def do_HEAD(self):
        self.serve_asset(head_only=True)

    def do_GET(self):
        self.serve_asset()

    def serve_asset(self, head_only=False):
        if self.headers.get("Host") != self.server.host_header:
            self.send_error(HTTPStatus.BAD_REQUEST)
            return

        path = unquote(urlsplit(self.path).path)
        if path == "/desktop.js":
            config = json.dumps({"ip": ADDRESS, "windowToken": self.server.window_token})
            self.respond(f"window.fmDesktop = {config};\n".encode(), "text/javascript", head_only)
            return

        try:
            target = (WEB_ROOT / (path.lstrip("/") or "index.html")).resolve()
            if not target.is_relative_to(WEB_ROOT) or not target.is_file():
                raise ValueError("Asset not found")
            body = target.read_bytes()
        except (OSError, ValueError):
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        content_type = WEB_TYPES.get(target.suffix) or mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.respond(body, content_type, head_only)

    def respond(self, body, content_type, head_only):
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'; "
            "img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'"
        )
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def do_POST(self):
        server = self.server
        browser = server.browser
        if (
            self.path != "/window"
            or self.headers.get("Host") != server.host_header
            or self.headers.get("Origin") != server.origin
            or not server.window_token
            or not hmac.compare_digest(self.headers.get("X-FM-Desktop", ""), server.window_token)
            or not browser
            or browser.poll() is not None
        ):
            self.send_error(HTTPStatus.FORBIDDEN)
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if 0 < length <= 512 else b""
            request = json.loads(body) if self.headers.get("Content-Type") == "application/json" else {
                "action": body.decode("ascii")
            }
            action = request.get("action") if isinstance(request, dict) else ""
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            request = {}
            action = ""
        if action not in WINDOW_ACTIONS:
            self.send_error(HTTPStatus.BAD_REQUEST)
            return

        size = None
        if action == "resize-login":
            dimensions = [request.get(key) for key in ("width", "height", "viewportWidth", "viewportHeight")]
            if any(type(value) is not int for value in dimensions):
                self.send_error(HTTPStatus.BAD_REQUEST)
                return
            width, height, viewport_width, viewport_height = dimensions
            if not (350 <= width <= 2000 and 250 <= height <= 1600
                    and 350 <= viewport_width <= 10000 and 250 <= viewport_height <= 10000):
                self.send_error(HTTPStatus.BAD_REQUEST)
                return
            size = dimensions

        if not control_windows_browser(browser.pid, action, size):
            self.send_error(HTTPStatus.CONFLICT, "Desktop window not available")
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()


def main():
    parser = argparse.ArgumentParser(description="Open the FM Solutions Windows desktop prototype.")
    parser.add_argument("--no-launch", action="store_true", help="Serve a browser preview without opening a window.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Loopback port (default {DEFAULT_PORT}).")
    parser.add_argument("--profile", type=Path, help="Override the desktop browser profile directory.")
    args = parser.parse_args()

    browser = None if args.no_launch else installed_browser()
    if not browser and not args.no_launch:
        parser.exit(1, "Edge or Chrome could not be found. Run with --no-launch for a browser preview.\n")

    try:
        server = PrototypeServer(args.port, bool(browser))
    except OSError as error:
        parser.exit(1, f"Could not start the prototype on port {args.port}. {error}\n")

    with server:
        url = server.origin + "/"
        print(f"FM Solutions is available at {url}", flush=True)
        if not browser:
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            return

        profile = args.profile or desktop_profile()
        try:
            profile.mkdir(parents=True, exist_ok=True)
            server.browser = subprocess.Popen([
                browser, f"--app={url}", f"--user-data-dir={profile.resolve()}",
                "--no-first-run", "--no-default-browser-check", "--disable-background-mode",
                f"--window-size={DEFAULT_WINDOW_SIZE[0]},{DEFAULT_WINDOW_SIZE[1]}",
            ])
        except OSError as error:
            parser.exit(1, f"Could not open the FM Solutions desktop window. {error}\n")

        service = threading.Thread(target=server.serve_forever, daemon=True)
        service.start()
        try:
            server.browser.wait()
        except KeyboardInterrupt:
            server.browser.terminate()
        finally:
            server.shutdown()
            service.join()


if __name__ == "__main__":
    main()
