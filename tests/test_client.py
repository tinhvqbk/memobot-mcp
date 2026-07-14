import pytest

from memobot_mcp import client as client_module
from memobot_mcp.client import MemobotClient


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


@pytest.fixture(autouse=True)
def no_real_login(monkeypatch):
    monkeypatch.setattr(client_module, "get_access_token", lambda invalidate_cache=False: "tok-1")


def test_request_sends_bearer_style_auth_header_and_returns_json(monkeypatch):
    calls = []

    def fake_request(method, url, params=None, headers=None):
        calls.append((method, url, params, headers))
        return FakeResponse(200, {"ok": True})

    monkeypatch.setattr(client_module.httpx, "request", fake_request)

    result = MemobotClient()._request("GET", "/analytic-v2/api/audio", params={"page": 1})

    assert result == {"ok": True}
    method, url, params, headers = calls[0]
    assert method == "GET"
    assert str(url) == "https://sohoa.memobot.io/analytic-v2/api/audio"
    assert params == {"page": 1}
    assert headers["Authorization"] == "Basic tok-1"


def test_request_retries_once_after_401_with_forced_relogin(monkeypatch):
    responses = [FakeResponse(401), FakeResponse(200, {"ok": True})]
    tokens_used = []

    def fake_request(method, url, params=None, headers=None):
        tokens_used.append(headers["Authorization"])
        return responses.pop(0)

    def fake_get_access_token(invalidate_cache=False):
        return "tok-2" if invalidate_cache else "tok-1"

    monkeypatch.setattr(client_module.httpx, "request", fake_request)
    monkeypatch.setattr(client_module, "get_access_token", fake_get_access_token)

    result = MemobotClient()._request("GET", "/authen/api/v1/auth/user")

    assert result == {"ok": True}
    assert tokens_used == ["Basic tok-1", "Basic tok-2"]


def test_list_recordings_passes_pagination_params(monkeypatch):
    captured = {}

    def fake_request(method, url, params=None, headers=None):
        captured.update(params)
        return FakeResponse(200, {"data": {"items": []}})

    monkeypatch.setattr(client_module.httpx, "request", fake_request)

    MemobotClient().list_recordings(page=2, limit=5)

    assert captured == {"page": 2, "limit": 5, "sort[create_time]": -1}


def test_get_audio_detail_requests_correct_path_and_params(monkeypatch):
    captured = {}

    def fake_request(method, url, params=None, headers=None):
        captured["url"] = url
        captured["params"] = params
        return FakeResponse(200, {"content": {"document": {"children": []}}})

    monkeypatch.setattr(client_module.httpx, "request", fake_request)

    result = MemobotClient().get_audio_detail("abc123")

    assert captured["url"] == "https://sohoa.memobot.io/analytic-v2/api/audio/abc123"
    assert captured["params"] == {"add_audio_url": "true"}
    assert result == {"content": {"document": {"children": []}}}


def test_get_recording_summary_requests_correct_path_and_params(monkeypatch):
    captured = {}

    def fake_request(method, url, params=None, headers=None):
        captured["url"] = url
        captured["params"] = params
        return FakeResponse(200, {"data": {"content": "a summary"}})

    monkeypatch.setattr(client_module.httpx, "request", fake_request)

    result = MemobotClient().get_recording_summary("abc123")

    assert captured["url"] == "https://sohoa.memobot.io/analytic-v2/api/feeds/get-one"
    assert captured["params"] == {"filter[related.audioId]": "abc123"}
    assert result == {"data": {"content": "a summary"}}
