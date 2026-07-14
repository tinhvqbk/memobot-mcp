"""Browser-based login for Memobot.

Memobot has no OAuth/device-code flow, so this pops a real Chromium window at
the login page and waits for the user to sign in by hand (email/password or
Google/Facebook/Apple button). We never see or store the password — we just
sniff the JSON response of the login XHR the app itself fires, cache it to
disk, and reuse it (it's valid for ~1 year) until it expires.

Once expired, we first try a silent refresh via the cached refresh_token
(POST /authen/api/v1/auth/token) before falling back to another browser
login — the browser only reappears if there's no cache at all or the
refresh_token itself no longer works (e.g. the password was changed).
"""

import json
import stat
import subprocess
import sys
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

LOGIN_URL = "https://app.memobot.io/dang-nhap"
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
    Falls back to a browser login only if there's no refresh_token cached or
    the refresh_token itself has been revoked (e.g. after a password change)."""
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

    data = _browser_login()
    _save_token(data)
    return data["access_token"]


def get_access_token(invalidate_cache=False):
    """Returns a valid Memobot access token.

    Prefers, in order: the cached access token; a silent refresh via the
    cached refresh_token; a popped-up browser login. The browser only
    appears when there's no usable cache at all or the refresh_token itself
    has expired/been revoked (e.g. after a password change).

    Pass invalidate_cache=True when the caller already knows the cached
    access token was rejected (e.g. an API call got a 401) — this skips
    straight to the refresh/browser-login path instead of trusting the
    cache's own (possibly stale) expire_at."""
    if not invalidate_cache:
        cached = _load_cached_token()
        if cached:
            return cached["access_token"]

    return _refresh_or_relogin()
