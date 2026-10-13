"""Guided Cloud setup: URL normalization, identification and sync state."""
from __future__ import annotations

import json

import httpx
import pytest

from mantle.cloud import CloudClient, ProbeResult, normalize_cloud_url, probe, record_sync, sync_status
from mantle.errors import MantleError, NotFoundError
from mantle.paths import MantlePaths


@pytest.mark.parametrize("raw,expected", [
    ("localhost:3100", "http://localhost:3100"),
    ("  http://127.0.0.1:3100/  ", "http://127.0.0.1:3100"),
    ("127.0.0.1:3100/api", "http://127.0.0.1:3100"),
    ("http://cloud.test/api/agents", "http://cloud.test"),
    ("mantle.example.com", "https://mantle.example.com"),
    ("192.168.1.20:3100", "http://192.168.1.20:3100"),
    ("https://example.com/mantle/", "https://example.com/mantle"),
])
def test_normalize_cloud_url(raw, expected):
    assert normalize_cloud_url(raw)[0] == expected


@pytest.mark.parametrize("raw,message", [
    ("ftp://cloud.test", "http:// or https://"),
    ("http://user:secret@cloud.test", "username or password"),
    ("http://cloud.test/?x=1", "query"),
    ("", "Enter the Mantle Cloud address"),
])
def test_normalize_cloud_url_rejects(raw, message):
    with pytest.raises(MantleError, match=message) as error:
        normalize_cloud_url(raw)
    assert "secret" not in str(error.value)


def client_for(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def mantle_cloud(request):
    if request.url.path == "/v1/health":
        return httpx.Response(200, json={"data": {"status": "ok", "service": "mantle-cloud"}})
    if request.url.path == "/api/agents":
        return httpx.Response(200, json={"data": [{"name": "demo", "description": "", "revision": 2, "updatedAt": "2026-10-07T00:00:00Z"}]})
    return httpx.Response(404, json={"error": {"code": "NOT_FOUND", "message": "missing"}})


def test_probe_identifies_mantle_cloud(mantle_home):
    result = probe(MantlePaths.discover(), "localhost:3100/api", client=client_for(mantle_cloud))
    assert result.ok and result.agents == 1 and result.url == "http://localhost:3100"
    assert any("Removed /api" in note for note in result.notes)


@pytest.mark.parametrize("handler,text", [
    (lambda r: httpx.Response(200, text="<!doctype html><p>dashboard</p>", headers={"content-type": "text/html"}), "is a website"),
    (lambda r: httpx.Response(404, text="<html>missing</html>", headers={"content-type": "text/html"}), "not a Mantle Cloud"),
    (lambda r: httpx.Response(200, json={"data": {"service": "something-else"}}), "not a Mantle Cloud"),
])
def test_probe_rejects_other_services(mantle_home, handler, text):
    result = probe(MantlePaths.discover(), "http://cloud.test", client=client_for(handler))
    assert not result.ok and text in result.summary


def test_probe_explains_transport_failures(mantle_home):
    def refused(request):
        raise httpx.ConnectError("[Errno 111] Connection refused", request=request)
    def tls(request):
        raise httpx.ConnectError("[SSL: WRONG_VERSION_NUMBER] wrong version number", request=request)
    assert "nothing is accepting connections" in probe(MantlePaths.discover(), "http://cloud.test", client=client_for(refused)).summary
    assert "use http://cloud.test" in probe(MantlePaths.discover(), "https://cloud.test", client=client_for(tls)).summary


def test_non_mantle_404_is_not_reported_as_missing_agent(mantle_home):
    client = CloudClient(MantlePaths.discover(), base_url="http://cloud.test",
                         client=client_for(lambda r: httpx.Response(404, text="<html>nope</html>", headers={"content-type": "text/html"})))
    with pytest.raises(MantleError) as error:
        client.latest("demo")
    assert not isinstance(error.value, NotFoundError)
    assert "does not look like a Mantle Cloud" in str(error.value)
    mantle = CloudClient(MantlePaths.discover(), base_url="http://cloud.test", client=client_for(mantle_cloud))
    with pytest.raises(NotFoundError, match='Agent "ghost" does not exist in Mantle Cloud at http://cloud.test'):
        mantle.latest("ghost")


def test_html_error_bodies_are_summarized(mantle_home):
    client = CloudClient(MantlePaths.discover(), base_url="http://cloud.test",
                         client=client_for(lambda r: httpx.Response(405, text="<html><h1>Method Not Allowed</h1></html>", headers={"content-type": "text/html"})))
    with pytest.raises(MantleError) as error:
        client.put("demo", "x")
    assert "<html>" not in str(error.value)
    assert "web page" in str(error.value)


def test_invalid_settings_name_the_file(cli, mantle_home):
    mantle_home.mkdir(parents=True, exist_ok=True)
    (mantle_home / "config.json").write_text('{"cloudurl": "http://x"}')
    result = cli("cloud", "pull", "demo")
    assert result.exit_code == 1
    assert "config.json" in result.stderr and "cloudurl" in result.stderr


def test_unconfigured_message_points_to_connect(mantle_home):
    with pytest.raises(MantleError, match="mantle cloud connect"):
        CloudClient(MantlePaths.discover())


def test_cloud_connect_saves_only_verified_addresses(cli, mantle_home, monkeypatch):
    results = {"http://bad.test": ProbeResult("http://bad.test", False, "not a Mantle Cloud", "hint"),
               "http://localhost:3100": ProbeResult("http://localhost:3100", True, "Connected", agents=0)}
    monkeypatch.setattr("mantle.cli_cloud.probe", lambda paths, url, **kwargs: results[url])
    failed = cli("cloud", "connect", "http://bad.test")
    assert failed.exit_code == 1 and "Nothing was saved" in failed.stderr
    assert not (mantle_home / "config.json").exists()
    assert cli("cloud", "connect", "http://bad.test", "--force").exit_code == 0
    assert cli("cloud", "connect", "http://localhost:3100").exit_code == 0
    assert json.loads((mantle_home / "config.json").read_text())["cloud_url"] == "http://localhost:3100"
    assert cli("cloud", "disconnect").exit_code == 0
    assert json.loads((mantle_home / "config.json").read_text())["cloud_url"] is None


def test_cloud_connect_discovers_local_registry(cli, mantle_home, monkeypatch):
    monkeypatch.setattr("mantle.cli_cloud.probe", lambda paths, url, **kwargs: ProbeResult(url, url.endswith(":3000"), "Connected", agents=2))
    result = cli("cloud", "connect", "--yes")
    assert result.exit_code == 0, result.output
    assert "Found Mantle Cloud at http://localhost:3000" in result.stdout


def test_cloud_connect_repairs_invalid_settings(cli, mantle_home, monkeypatch):
    mantle_home.mkdir(parents=True, exist_ok=True)
    (mantle_home / "config.json").write_text('{"cloudurl": "typo", "default_runtime": "1.0.0"}')
    monkeypatch.setattr("mantle.cli_cloud.probe", lambda paths, url, **kwargs: ProbeResult(url, True, "Connected"))
    assert cli("cloud", "connect", "http://localhost:3100").exit_code == 0
    data = json.loads((mantle_home / "config.json").read_text())
    assert data["cloud_url"] == "http://localhost:3100" and data["default_runtime"] == "1.0.0"


def test_sync_status(tmp_path):
    root = tmp_path / "demo"
    root.mkdir()
    (root / "agent.yaml").write_text("a")
    assert sync_status(root, "http://c", None) == "only local (not pushed)"
    assert sync_status(None, "http://c", 3).startswith("only in Cloud")
    assert sync_status(root, "http://c", 1).startswith("unknown")
    record_sync(root, "http://c", 1, "a")
    assert sync_status(root, "http://c", 1) == "in sync"
    assert sync_status(root, "http://c", 2).startswith("Cloud has a newer")
    (root / "agent.yaml").write_text("b")
    assert sync_status(root, "http://c", 1) == "local changes not pushed"
    assert sync_status(root, "http://c", 2) == "both changed"
