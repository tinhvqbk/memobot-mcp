"""Browser-based login for Memobot.

Memobot has no OAuth/device-code flow, so instead we run a tiny local HTTP
server that serves a login page and print/open its URL. That page's JS calls
Memobot's login endpoint directly from the user's own browser — the password
goes straight from their browser to Memobot's real server (CORS on that
endpoint is wide open, so this works) and never touches our process — and
once Memobot responds, the page POSTs just the resulting token JSON back to
our local server. The token is then cached to disk and reused for its ~1
year validity.

When a display is available we open that URL automatically in the system's
default browser (whatever's already installed — Chrome, Safari, Firefox...);
there's no bundled/downloaded browser to install or launch. On a headless
machine (no GUI — e.g. a server reached over plain SSH) we just print the
URL for the user to open in any browser, anywhere, using SSH port-forwarding
if the server isn't local.

Once expired, we first try a silent refresh via the cached refresh_token
(POST /authen/api/v1/auth/token) before falling back to another interactive
login — that only reappears if there's no cache at all or the refresh_token
itself no longer works (e.g. the password was changed).
"""

import json
import os
import stat
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

LOGIN_ENDPOINT = "https://sohoa.memobot.io/authen/api/v1/auth/login"
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
    avoiding an interactive login. The response only carries access_token/
    expire_at/expire_after — refresh_token and user_id don't rotate, so
    callers must merge this into the previously cached data rather than
    replace it."""
    response = httpx.post(REFRESH_URL, json={"refresh_token": refresh_token})
    response.raise_for_status()
    return response.json()


def _save_token(data):
    CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CREDENTIALS_PATH.write_text(json.dumps(data, indent=2))
    CREDENTIALS_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600, owner-only


def _has_display():
    """Best-effort check for whether we can auto-open a browser here.
    MEMOBOT_MCP_HEADLESS=1 forces manual mode (just print the URL) regardless
    of platform (e.g. a macOS box that's technically "darwin" but has no real
    interactive session). Otherwise macOS/Windows are assumed to always have
    one; on Linux/X11/Wayland we check the usual env vars a headless SSH
    session won't have set."""
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


def _interactive_login(timeout_seconds=600, host="127.0.0.1", _on_ready=None):
    """Starts a throwaway local HTTP server serving a login page, and waits
    for it to receive the resulting token via its /callback endpoint. Opens
    the page automatically in the system's default browser when a display
    is available; otherwise (or if that fails) just prints the URL for the
    user to open in any browser, anywhere — using SSH port-forwarding if
    this host isn't their own.

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

    opened = _has_display() and webbrowser.open(url)
    if opened:
        print(f"\nOpened {url} in your browser to log in to Memobot.", file=sys.stderr)
    else:
        print("\nMemobot login needed.", file=sys.stderr)
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
    cached refresh_token; an interactive login (a local login page, opened
    automatically in the system browser when possible). That interactive
    step only happens when there's no usable cache at all or the
    refresh_token itself has expired/been revoked (e.g. after a password
    change).

    Pass invalidate_cache=True when the caller already knows the cached
    access token was rejected (e.g. an API call got a 401) — this skips
    straight to the refresh/interactive-login path instead of trusting the
    cache's own (possibly stale) expire_at."""
    if not invalidate_cache:
        cached = _load_cached_token()
        if cached:
            return cached["access_token"]

    return _refresh_or_relogin()
