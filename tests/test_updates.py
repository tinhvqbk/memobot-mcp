from memobot_mcp import updates


def test_get_running_version_reads_installed_package_metadata(monkeypatch):
    monkeypatch.setattr(updates.metadata, "version", lambda name: "1.2.3")
    assert updates.get_running_version() == "1.2.3"


def test_get_latest_version_parses_version_from_remote_pyproject(monkeypatch):
    captured = {}

    class FakeResponse:
        text = '[project]\nname = "memobot-mcp"\nversion = "9.9.9"\n'

        def raise_for_status(self):
            pass

    def fake_get(url, timeout=None):
        captured["url"] = url
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(updates.httpx, "get", fake_get)

    assert updates.get_latest_version() == "9.9.9"
    assert captured["url"] == updates.PYPROJECT_URL


def test_check_for_updates_reports_update_available(monkeypatch):
    monkeypatch.setattr(updates, "get_running_version", lambda: "0.1.0")
    monkeypatch.setattr(updates, "get_latest_version", lambda: "0.2.0")

    result = updates.check_for_updates()

    assert result == {
        "running_version": "0.1.0",
        "latest_version": "0.2.0",
        "update_available": True,
    }


def test_check_for_updates_reports_up_to_date(monkeypatch):
    monkeypatch.setattr(updates, "get_running_version", lambda: "0.2.0")
    monkeypatch.setattr(updates, "get_latest_version", lambda: "0.2.0")

    result = updates.check_for_updates()

    assert result == {
        "running_version": "0.2.0",
        "latest_version": "0.2.0",
        "update_available": False,
    }


def test_check_for_updates_not_available_when_running_is_ahead(monkeypatch):
    # e.g. a local dev build ahead of what's currently published on main.
    monkeypatch.setattr(updates, "get_running_version", lambda: "0.2.0")
    monkeypatch.setattr(updates, "get_latest_version", lambda: "0.1.0")

    result = updates.check_for_updates()

    assert result == {
        "running_version": "0.2.0",
        "latest_version": "0.1.0",
        "update_available": False,
    }


def test_check_for_updates_reports_error_without_raising(monkeypatch):
    monkeypatch.setattr(updates, "get_running_version", lambda: "0.1.0")

    def fails():
        raise RuntimeError("network down")

    monkeypatch.setattr(updates, "get_latest_version", fails)

    result = updates.check_for_updates()

    assert result["running_version"] == "0.1.0"
    assert result["latest_version"] is None
    assert result["update_available"] is False
    assert "network down" in result["error"]
