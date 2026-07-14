import json
import stat

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

    monkeypatch.setattr(auth, "_browser_login", fail_if_called)

    assert auth.get_access_token() == "access-jwt"


def test_get_access_token_logs_in_and_caches_when_no_cache(monkeypatch, isolated_credentials_path):
    monkeypatch.setattr(auth, "_browser_login", lambda: _token(expire_at=9999999999))

    token = auth.get_access_token()

    assert token == "access-jwt"
    assert json.loads(isolated_credentials_path.read_text())["access_token"] == "access-jwt"


def test_get_access_token_invalidate_cache_silently_refreshes_when_possible(monkeypatch):
    monkeypatch.setattr(auth.time, "time", lambda: 1000)
    auth._save_token(_token(expire_at=1000 + auth.EXPIRY_SAFETY_MARGIN_SECONDS + 100))

    refreshed = {"access_token": "refreshed-jwt", "expire_at": 9999999999, "expire_after": 31536000}
    monkeypatch.setattr(auth, "_refresh_access_token", lambda rt: refreshed)

    def fail_if_called():
        raise AssertionError("_browser_login should not be called when refresh succeeds")

    monkeypatch.setattr(auth, "_browser_login", fail_if_called)

    assert auth.get_access_token(invalidate_cache=True) == "refreshed-jwt"


def test_get_access_token_invalidate_cache_falls_back_to_browser_when_refresh_fails(monkeypatch):
    monkeypatch.setattr(auth.time, "time", lambda: 1000)
    auth._save_token(_token(expire_at=1000 + auth.EXPIRY_SAFETY_MARGIN_SECONDS + 100))

    def refresh_fails(refresh_token):
        raise httpx.HTTPStatusError("401", request=None, response=None)

    monkeypatch.setattr(auth, "_refresh_access_token", refresh_fails)

    fresh = {**_token(expire_at=9999999999), "access_token": "fresh-jwt"}
    monkeypatch.setattr(auth, "_browser_login", lambda: fresh)

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
