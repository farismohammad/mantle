from __future__ import annotations

import httpx
import pytest

from mantle.cloud import CloudClient
from mantle.errors import MantleError, NotFoundError
from mantle.paths import MantlePaths


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
