import json
import stat
import threading

import httpx
import pytest

from memobot_mcp import auth


@pytest.fixture(autouse=True)
def isolated_credentials_path(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "CREDENTIALS_PATH", tmp_path / "credentials.json")
    return auth.CREDENTIALS_PATH


def _token(expire_at):
    return {
        "access_token": "access-jwt",
        "refresh_token": "refresh-jwt",
        "expire_at": expire_at,
        "expire_after": 31536000,
        "user_id": "u1",
    }


def test_load_cached_token_missing_file_returns_none():
    assert auth._load_cached_token() is None


def test_save_then_load_round_trips(monkeypatch):
    monkeypatch.setattr(auth.time, "time", lambda: 1000)
    data = _token(expire_at=1000 + auth.EXPIRY_SAFETY_MARGIN_SECONDS + 100)

    auth._save_token(data)
    loaded = auth._load_cached_token()

    assert loaded == data


def test_save_token_sets_owner_only_permissions(isolated_credentials_path):
    auth._save_token(_token(expire_at=9999999999))

    mode = isolated_credentials_path.stat().st_mode
    assert stat.S_IMODE(mode) == stat.S_IRUSR | stat.S_IWUSR


def test_load_cached_token_expired_returns_none(monkeypatch):
    monkeypatch.setattr(auth.time, "time", lambda: 1000)
    auth._save_token(_token(expire_at=1000 + auth.EXPIRY_SAFETY_MARGIN_SECONDS - 1))

    assert auth._load_cached_token() is None


def test_load_cached_token_malformed_json_returns_none(isolated_credentials_path):
    isolated_credentials_path.parent.mkdir(parents=True, exist_ok=True)
    isolated_credentials_path.write_text("not json")

    assert auth._load_cached_token() is None


def test_get_access_token_uses_cache_without_browser_login(monkeypatch):
    monkeypatch.setattr(auth.time, "time", lambda: 1000)
    auth._save_token(_token(expire_at=1000 + auth.EXPIRY_SAFETY_MARGIN_SECONDS + 100))

    def fail_if_called(*args, **kwargs):
        raise AssertionError("_browser_login should not be called when cache is valid")

    monkeypatch.setattr(auth, "_interactive_login", fail_if_called)

    assert auth.get_access_token() == "access-jwt"


def test_get_access_token_logs_in_and_caches_when_no_cache(monkeypatch, isolated_credentials_path):
    monkeypatch.setattr(auth, "_interactive_login", lambda: _token(expire_at=9999999999))

    token = auth.get_access_token()

    assert token == "access-jwt"
    assert json.loads(isolated_credentials_path.read_text())["access_token"] == "access-jwt"


def test_get_access_token_invalidate_cache_silently_refreshes_when_possible(monkeypatch):
    monkeypatch.setattr(auth.time, "time", lambda: 1000)
    auth._save_token(_token(expire_at=1000 + auth.EXPIRY_SAFETY_MARGIN_SECONDS + 100))

    refreshed = {"access_token": "refreshed-jwt", "expire_at": 9999999999, "expire_after": 31536000}
    monkeypatch.setattr(auth, "_refresh_access_token", lambda rt: refreshed)

    def fail_if_called():
        raise AssertionError("_interactive_login should not be called when refresh succeeds")

    monkeypatch.setattr(auth, "_interactive_login", fail_if_called)

    assert auth.get_access_token(invalidate_cache=True) == "refreshed-jwt"


def test_get_access_token_invalidate_cache_falls_back_to_interactive_when_refresh_fails(
    monkeypatch,
):
    monkeypatch.setattr(auth.time, "time", lambda: 1000)
    auth._save_token(_token(expire_at=1000 + auth.EXPIRY_SAFETY_MARGIN_SECONDS + 100))

    def refresh_fails(refresh_token):
        raise httpx.HTTPStatusError("401", request=None, response=None)

    monkeypatch.setattr(auth, "_refresh_access_token", refresh_fails)

    fresh = {**_token(expire_at=9999999999), "access_token": "fresh-jwt"}
    monkeypatch.setattr(auth, "_interactive_login", lambda: fresh)

    assert auth.get_access_token(invalidate_cache=True) == "fresh-jwt"


def test_refresh_access_token_posts_refresh_token_and_returns_json(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"access_token": "new-jwt", "expire_at": 1, "expire_after": 2}

    def fake_post(url, json=None):
        captured["url"] = url
        captured["json"] = json
        return FakeResponse()

    monkeypatch.setattr(auth.httpx, "post", fake_post)

    result = auth._refresh_access_token("my-refresh-token")

    assert result == {"access_token": "new-jwt", "expire_at": 1, "expire_after": 2}
    assert captured["url"] == auth.REFRESH_URL
    assert captured["json"] == {"refresh_token": "my-refresh-token"}


def test_has_display_true_on_macos_and_windows(monkeypatch):
    monkeypatch.setattr(auth.sys, "platform", "darwin")
    monkeypatch.delenv("DISPLAY", raising=False)
    assert auth._has_display() is True

    monkeypatch.setattr(auth.sys, "platform", "win32")
    assert auth._has_display() is True


def test_has_display_forced_off_by_env_var(monkeypatch):
    monkeypatch.setattr(auth.sys, "platform", "darwin")
    monkeypatch.setenv("MEMOBOT_MCP_HEADLESS", "1")
    assert auth._has_display() is False


def test_has_display_on_linux_depends_on_env_vars(monkeypatch):
    monkeypatch.setattr(auth.sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert auth._has_display() is False

    monkeypatch.setenv("DISPLAY", ":0")
    assert auth._has_display() is True

    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    assert auth._has_display() is True


def test_interactive_login_uses_browser_when_display_available(monkeypatch):
    monkeypatch.setattr(auth, "_has_display", lambda: True)
    monkeypatch.setattr(auth, "_browser_login", lambda timeout_seconds=300: {"access_token": "b"})

    def fail_if_called(*args, **kwargs):
        raise AssertionError(
            "_local_callback_login should not be called when browser login succeeds"
        )

    monkeypatch.setattr(auth, "_local_callback_login", fail_if_called)

    assert auth._interactive_login() == {"access_token": "b"}


def test_interactive_login_skips_browser_when_no_display(monkeypatch):
    monkeypatch.setattr(auth, "_has_display", lambda: False)

    def fail_if_called(timeout_seconds=300):
        raise AssertionError("_browser_login should not be called when there's no display")

    monkeypatch.setattr(auth, "_browser_login", fail_if_called)
    monkeypatch.setattr(auth, "_local_callback_login", lambda: {"access_token": "c"})

    assert auth._interactive_login() == {"access_token": "c"}


def test_interactive_login_falls_back_when_browser_login_raises(monkeypatch):
    monkeypatch.setattr(auth, "_has_display", lambda: True)

    def browser_fails(timeout_seconds=300):
        raise RuntimeError("no working X server")

    monkeypatch.setattr(auth, "_browser_login", browser_fails)
    monkeypatch.setattr(auth, "_local_callback_login", lambda: {"access_token": "d"})

    assert auth._interactive_login() == {"access_token": "d"}


def test_local_callback_login_serves_page_and_captures_posted_token():
    ready = threading.Event()
    ready_url = {}

    def on_ready(url):
        ready_url["url"] = url
        ready.set()

    result = {}

    def run():
        result["data"] = auth._local_callback_login(timeout_seconds=10, _on_ready=on_ready)

    thread = threading.Thread(target=run)
    thread.start()
    assert ready.wait(timeout=5), "server never became ready"
    url = ready_url["url"]

    page = httpx.get(url)
    assert page.status_code == 200
    assert "Log in to Memobot" in page.text

    missing = httpx.get(url + "nonexistent")
    assert missing.status_code == 404

    fake_login_response = {
        "access_token": "captured-jwt",
        "refresh_token": "captured-refresh",
        "expire_at": 9999999999,
        "expire_after": 31536000,
        "user_id": "u1",
    }
    callback_resp = httpx.post(url + "callback", json=fake_login_response)
    assert callback_resp.status_code == 200

    thread.join(timeout=5)
    assert result["data"] == fake_login_response
