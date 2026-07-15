"""Browser-based login for Memobot.

Memobot has no OAuth/device-code flow. On a machine with a display, we pop a
real Chromium window at the login page and wait for the user to sign in by
hand (email/password or Google/Facebook/Apple button) — we never see or
store the password ourselves, just sniff the JSON response of the login XHR
the app itself fires.

On a headless machine (no GUI to show Chromium on — e.g. a server reached
over plain SSH), we instead start a tiny local HTTP server and print a URL
for the user to open in *any* browser, anywhere (their own laptop/phone, via
SSH port-forwarding if the server isn't local). That page's JS calls
Memobot's login endpoint directly from the user's browser — the password
goes straight from their browser to Memobot's real server (CORS on that
endpoint is wide open, so this works), never through our process — and once
Memobot responds, the page POSTs just the resulting token JSON back to our
local server. Either way, the token is cached to disk and reused for its
~1 year validity.

Once expired, we first try a silent refresh via the cached refresh_token
(POST /authen/api/v1/auth/token) before falling back to another interactive
login — that only reappears if there's no cache at all or the refresh_token
itself no longer works (e.g. the password was changed).
"""

import json
import os
import stat
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

LOGIN_URL = "https://app.memobot.io/dang-nhap"
LOGIN_ENDPOINT = "https://sohoa.memobot.io/authen/api/v1/auth/login"
LOGIN_ENDPOINT_SUFFIX = "/authen/api/v1/auth/login"
REFRESH_URL = "https://sohoa.memobot.io/authen/api/v1/auth/token"
CREDENTIALS_PATH = Path.home() / ".config" / "memobot-mcp" / "credentials.json"

# Refresh a bit before actual expiry to avoid racing a nearly-expired token.
EXPIRY_SAFETY_MARGIN_SECONDS = 300


def _load_raw_cached_data():
    if not CREDENTIALS_PATH.exists():
        return None
    try:
        return json.loads(CREDENTIALS_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _load_cached_token():
    data = _load_raw_cached_data()
    if data is None:
        return None
    if time.time() >= data.get("expire_at", 0) - EXPIRY_SAFETY_MARGIN_SECONDS:
        return None
    return data


def _refresh_access_token(refresh_token):
    """Silently exchanges a still-valid refresh_token for a new access_token,
    avoiding a browser popup. The response only carries access_token/expire_at/
    expire_after — refresh_token and user_id don't rotate, so callers must
    merge this into the previously cached data rather than replace it."""
    response = httpx.post(REFRESH_URL, json={"refresh_token": refresh_token})
    response.raise_for_status()
    return response.json()


def _save_token(data):
    CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CREDENTIALS_PATH.write_text(json.dumps(data, indent=2))
    CREDENTIALS_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600, owner-only


def _has_display():
    """Best-effort check for whether we can pop a real browser window here.
    MEMOBOT_MCP_HEADLESS=1 forces the local-callback path regardless of
    platform (e.g. a macOS box that's technically "darwin" but has no real
    interactive session). Otherwise macOS/Windows are assumed to always have
    a display; on Linux/X11/Wayland we check the usual env vars a headless
    SSH session won't have set."""
    if os.environ.get("MEMOBOT_MCP_HEADLESS"):
        return False
    if sys.platform in ("darwin", "win32"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


_LOGIN_PAGE_HTML = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Memobot login</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 22rem; margin: 4rem auto; padding: 0 1rem; }
  input {
    display: block; width: 100%; box-sizing: border-box;
    margin-bottom: 0.75rem; padding: 0.5rem;
  }
  button { padding: 0.5rem 1rem; }
  #status { margin-top: 1rem; white-space: pre-wrap; }
  .error { color: #c0392b; }
  .ok { color: #1a7a3d; }
</style>
</head>
<body>
<h3>Log in to Memobot</h3>
<p>This submits straight to Memobot's own login endpoint from your browser —
this page (running locally on the machine that needs the token) never sees
your password.</p>
<form id="f">
  <input id="email" type="email" autocomplete="username" placeholder="Email" required>
  <input id="password" type="password" autocomplete="current-password"
         placeholder="Password" required>
  <button type="submit">Log in</button>
</form>
<div id="status"></div>
<script>
const LOGIN_ENDPOINT = "__LOGIN_ENDPOINT__";
const statusEl = document.getElementById("status");
function setStatus(text, cls) {
  statusEl.textContent = text;
  statusEl.className = cls || "";
}
document.getElementById("f").addEventListener("submit", async (e) => {
  e.preventDefault();
  setStatus("Logging in...");
  const email = document.getElementById("email").value;
  const password = document.getElementById("password").value;
  try {
    const res = await fetch(LOGIN_ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    const data = await res.json();
    if (!res.ok) {
      setStatus("Login failed: " + (data.message || res.status), "error");
      return;
    }
    await fetch("/callback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    setStatus("Logged in — you can close this tab.", "ok");
  } catch (err) {
    setStatus("Error: " + err.message, "error");
  }
});
</script>
</body>
</html>
""".replace("__LOGIN_ENDPOINT__", LOGIN_ENDPOINT)


class _CallbackHandler(BaseHTTPRequestHandler):
    def _send(self, status, content_type, body):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, "text/html; charset=utf-8", _LOGIN_PAGE_HTML.encode())
        else:
            self._send(404, "text/plain", b"not found")

    def do_POST(self):
        if self.path != "/callback":
            self._send(404, "text/plain", b"not found")
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self._send(400, "application/json", b'{"error":"invalid json"}')
            return
        self.server.captured_data = data
        self.server.captured_event.set()
        self._send(200, "application/json", b'{"ok":true}')

    def log_message(self, format, *args):
        pass  # keep stderr clean — this isn't a debugging server


def _local_callback_login(timeout_seconds=600, host="127.0.0.1", _on_ready=None):
    """Starts a throwaway local HTTP server serving a login page, and waits
    for it to receive the resulting token via its /callback endpoint. Used
    when there's no display to pop a real browser on (or the attempt to do
    so failed) — the user opens the printed URL in any browser on any
    device, using SSH port-forwarding if this host isn't their own.

    _on_ready, if given, is called with the server's URL as soon as it's
    listening — a test-only hook, since the port is chosen dynamically."""
    event = threading.Event()
    server = ThreadingHTTPServer((host, 0), _CallbackHandler)
    server.captured_event = event
    server.captured_data = None

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    port = server.server_address[1]
    url = f"http://{host}:{port}/"
    if _on_ready:
        _on_ready(url)
    print("\nMemobot login needed — no display available here.", file=sys.stderr)
    print(
        "Open this URL in any browser (use SSH port-forwarding if this host is remote):",
        file=sys.stderr,
    )
    print(f"\n    {url}\n", file=sys.stderr)

    try:
        if not event.wait(timeout=timeout_seconds):
            raise TimeoutError(
                f"Timed out waiting for Memobot login via {url} after {timeout_seconds}s"
            )
    finally:
        server.shutdown()
        thread.join(timeout=5)

    return server.captured_data


def _interactive_login(timeout_seconds=300):
    """Logs in interactively: pops a real headed browser when a display is
    available, falling back to a local-callback login page (print a URL,
    open it in any browser anywhere) if there's no display or the browser
    attempt fails for any reason."""
    if _has_display():
        try:
            return _browser_login(timeout_seconds=timeout_seconds)
        except Exception as e:
            print(
                f"Headed browser login failed ({e}); falling back to a login link.",
                file=sys.stderr,
            )
    return _local_callback_login()


def _launch_chromium(playwright):
    """Launches Chromium, installing the browser binary on first use if it's
    missing — so a fresh `uvx` install works with no separate setup step."""
    try:
        return playwright.chromium.launch(headless=False)
    except Exception as e:
        if "Executable doesn't exist" not in str(e):
            raise
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
        return playwright.chromium.launch(headless=False)


def _browser_login(timeout_seconds=300):
    """Open a real browser at the login page and wait for the app's own
    login response, capturing it directly instead of touching credentials."""
    captured = {}

    with sync_playwright() as playwright:
        browser = _launch_chromium(playwright)
        page = browser.new_page()

        def on_response(response):
            if response.request.method == "POST" and response.url.endswith(LOGIN_ENDPOINT_SUFFIX):
                try:
                    captured["data"] = response.json()
                except Exception:
                    pass

        page.on("response", on_response)
        page.goto(LOGIN_URL)

        deadline = time.time() + timeout_seconds
        while "data" not in captured and time.time() < deadline:
            page.wait_for_timeout(500)

        browser.close()

    if "data" not in captured:
        raise TimeoutError(
            f"Timed out waiting for Memobot login in the browser after {timeout_seconds}s"
        )
    return captured["data"]


def _refresh_or_relogin():
    """Tries a silent refresh via the cached refresh_token regardless of what
    the cached access token's own expire_at claims — used when the caller
    already knows that cached access token is bad (e.g. it got a 401).
    Falls back to an interactive login only if there's no refresh_token
    cached or the refresh_token itself has been revoked (e.g. after a
    password change)."""
    raw = _load_raw_cached_data()
    if raw and raw.get("refresh_token"):
        try:
            refreshed = _refresh_access_token(raw["refresh_token"])
        except httpx.HTTPStatusError:
            pass
        else:
            merged = {**raw, **refreshed}
            _save_token(merged)
            return merged["access_token"]

    data = _interactive_login()
    _save_token(data)
    return data["access_token"]


def get_access_token(invalidate_cache=False):
    """Returns a valid Memobot access token.

    Prefers, in order: the cached access token; a silent refresh via the
    cached refresh_token; an interactive login (headed browser, or a local
    login link on headless machines). That interactive step only happens
    when there's no usable cache at all or the refresh_token itself has
    expired/been revoked (e.g. after a password change).

    Pass invalidate_cache=True when the caller already knows the cached
    access token was rejected (e.g. an API call got a 401) — this skips
    straight to the refresh/interactive-login path instead of trusting the
    cache's own (possibly stale) expire_at."""
    if not invalidate_cache:
        cached = _load_cached_token()
        if cached:
            return cached["access_token"]

    return _refresh_or_relogin()
