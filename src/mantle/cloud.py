from __future__ import annotations

import os
from typing import Any

import httpx

from mantle.errors import MantleError, NotFoundError
from mantle.models import MantleConfig
from mantle.paths import MantlePaths
from mantle.storage import load_json


def _env_flag(name: str, *, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise MantleError(f"{name} must be one of: 1, true, yes, on, 0, false, no, off.")


class CloudClient:
    def __init__(
        self,
        paths: MantlePaths,
        *,
        base_url: str | None = None,
        client: httpx.Client | None = None,
    ):
        stored = MantleConfig.model_validate(load_json(paths.config, {}))
        selected = base_url or os.environ.get("MANTLE_CLOUD_URL") or stored.cloud_url
        if not selected:
            raise MantleError(
                "Mantle Cloud is not configured. Set MANTLE_CLOUD_URL or cloud_url in ~/.mantle/config.json."
            )
        self.base_url = selected.rstrip("/")
        self.client = client or httpx.Client(
            timeout=30,
            follow_redirects=True,
            verify=not _env_flag("MANTLE_CLOUD_INSECURE", default=stored.cloud_insecure),
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self.client.request(method, f"{self.base_url}{path}", **kwargs)
        except httpx.HTTPError as error:
            raise MantleError(f"Could not reach Mantle Cloud at {self.base_url}: {error}") from error
        if response.status_code == 404:
            raise NotFoundError("The requested agent does not exist in Mantle Cloud.")
        if not response.is_success:
            try:
                payload = response.json()
                detail = payload.get("error", {}).get("message") or response.text
                issues = payload.get("error", {}).get("details", {}).get("issues", [])
                if issues:
                    detail += "\n" + "\n".join(
                        f"  {issue.get('path', 'configuration')}: {issue.get('message', 'invalid')}"
                        for issue in issues
                    )
            except (ValueError, AttributeError):
                detail = response.text or response.reason_phrase
            raise MantleError(f"Mantle Cloud rejected the request ({response.status_code}): {detail}")
        return response

    def list(self) -> list[dict[str, Any]]:
        return self._request("GET", "/api/agents").json()["data"]

    def exists(self, name: str) -> bool:
        try:
            self.latest(name)
            return True
        except NotFoundError:
            return False

    def latest(self, name: str) -> dict[str, Any]:
        return self._request("GET", f"/api/agents/{name}").json()["data"]

    def put(self, name: str, yaml_text: str) -> dict[str, Any]:
        return self._request(
            "PUT",
            f"/api/agents/{name}",
            content=yaml_text.encode("utf-8"),
            headers={"content-type": "application/yaml; charset=utf-8"},
        ).json()["data"]

    def delete(self, name: str) -> None:
        self._request("DELETE", f"/api/agents/{name}")
