from __future__ import annotations

import httpx
import pytest

from mantle.cloud import CloudClient
from mantle.errors import MantleError, NotFoundError
from mantle.paths import MantlePaths
from mantle.storage import write_json


def test_cloud_client_uploads_only_yaml_and_reads_latest(mantle_home):
    seen = {}

    def handler(request: httpx.Request):
        if request.method == "PUT":
            seen["body"] = request.content.decode()
            seen["content_type"] = request.headers["content-type"]
            return httpx.Response(200, json={"data": {"created": True, "revision": {"number": 1}}})
        return httpx.Response(200, json={"data": {"name": "demo", "yaml": "mantle: '0.1'", "revision": {"number": 1}}})

    client = CloudClient(
        MantlePaths.discover(),
        base_url="http://cloud.test",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    client.put("demo", "mantle: '0.1'\n")
    assert seen == {"body": "mantle: '0.1'\n", "content_type": "application/yaml; charset=utf-8"}
    assert client.latest("demo")["revision"]["number"] == 1


def test_cloud_errors_are_actionable(mantle_home):
    def missing(_request):
        return httpx.Response(404, json={"error": {"message": "missing"}})

    client = CloudClient(MantlePaths.discover(), base_url="http://cloud.test", client=httpx.Client(transport=httpx.MockTransport(missing)))
    with pytest.raises(NotFoundError):
        client.latest("demo")

    def invalid(_request):
        return httpx.Response(422, json={"error": {"message": "invalid", "details": {"issues": [{"path": "runtime.name", "message": "must be pi"}]}}})

    client = CloudClient(MantlePaths.discover(), base_url="http://cloud.test", client=httpx.Client(transport=httpx.MockTransport(invalid)))
    with pytest.raises(MantleError, match="runtime.name: must be pi"):
        client.put("demo", "invalid")


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_cloud_client_can_disable_tls_verification(mantle_home, monkeypatch, value):
    seen = {}

    class FakeClient:
        def __init__(self, **kwargs):
            seen.update(kwargs)

    monkeypatch.setenv("MANTLE_CLOUD_INSECURE", value)
    monkeypatch.setattr("mantle.cloud.httpx.Client", FakeClient)

    CloudClient(MantlePaths.discover(), base_url="https://cloud.test")

    assert seen["verify"] is False


def test_cloud_client_verifies_tls_by_default(mantle_home, monkeypatch):
    seen = {}

    class FakeClient:
        def __init__(self, **kwargs):
            seen.update(kwargs)

    monkeypatch.delenv("MANTLE_CLOUD_INSECURE", raising=False)
    monkeypatch.setattr("mantle.cloud.httpx.Client", FakeClient)

    CloudClient(MantlePaths.discover(), base_url="https://cloud.test")

    assert seen["verify"] is True


def test_cloud_client_can_disable_tls_verification_in_config(mantle_home, monkeypatch):
    seen = {}

    class FakeClient:
        def __init__(self, **kwargs):
            seen.update(kwargs)

    write_json(mantle_home / "config.json", {"cloud_insecure": True})
    monkeypatch.delenv("MANTLE_CLOUD_INSECURE", raising=False)
    monkeypatch.setattr("mantle.cloud.httpx.Client", FakeClient)

    CloudClient(MantlePaths.discover(), base_url="https://cloud.test")

    assert seen["verify"] is False


def test_cloud_insecure_environment_variable_overrides_config(mantle_home, monkeypatch):
    seen = {}

    class FakeClient:
        def __init__(self, **kwargs):
            seen.update(kwargs)

    write_json(mantle_home / "config.json", {"cloud_insecure": True})
    monkeypatch.setenv("MANTLE_CLOUD_INSECURE", "false")
    monkeypatch.setattr("mantle.cloud.httpx.Client", FakeClient)

    CloudClient(MantlePaths.discover(), base_url="https://cloud.test")

    assert seen["verify"] is True


def test_cloud_client_rejects_invalid_insecure_flag(mantle_home, monkeypatch):
    monkeypatch.setenv("MANTLE_CLOUD_INSECURE", "sometimes")

    with pytest.raises(MantleError, match="MANTLE_CLOUD_INSECURE must be one of"):
        CloudClient(MantlePaths.discover(), base_url="https://cloud.test")
