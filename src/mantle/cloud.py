from __future__ import annotations

import os
from typing import Any

import httpx

from mantle.errors import MantleError, NotFoundError
from mantle.agents import validate_agent_name
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
        try:
            url = httpx.URL(selected.rstrip("/"))
        except httpx.InvalidURL as error:
            raise MantleError("Invalid Mantle Cloud URL.") from error
        if url.scheme not in {"http", "https"} or not url.host or url.query or url.fragment:
            raise MantleError("Mantle Cloud URL must be an http:// or https:// URL without a query or fragment.")
        if url.username or url.password:
            raise MantleError("Mantle Cloud URL must not include a username or password.")
        self.base_url = selected.rstrip("/")
        self.client = client or httpx.Client(
            timeout=30,
            follow_redirects=True,
            verify=not _env_flag("MANTLE_CLOUD_INSECURE", default=stored.cloud_insecure),
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self.client.request(method, f"{self.base_url}{path}", **kwargs)
        except (httpx.HTTPError, httpx.InvalidURL) as error:
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
            except (ValueError, AttributeError, TypeError):
                detail = response.text or response.reason_phrase
            raise MantleError(f"Mantle Cloud rejected the request ({response.status_code}): {detail}")
        return response

    def list(self) -> list[dict[str, Any]]:
        response = self._request("GET", "/api/agents")
        try:
            data = response.json()["data"]
            if not isinstance(data, list):
                raise ValueError("data must be a list")
            for item in data:
                if not isinstance(item, dict):
                    raise ValueError("agent summary must be an object")
                validate_agent_name(item.get("name"))
                if (
                    not isinstance(item.get("description"), str)
                    or type(item.get("revision")) is not int
                    or item["revision"] < 1
                    or not isinstance(item.get("updatedAt"), str)
                    or not isinstance(item.get("runtime"), dict)
                    or not isinstance(item["runtime"].get("name"), str)
                    or not isinstance(item["runtime"].get("version"), str)
                    or type(item.get("skillCount")) is not int
                    or item["skillCount"] < 0
                    or type(item.get("packageCount")) is not int
                    or item["packageCount"] < 0
                ):
                    raise ValueError("agent summary fields are invalid")
            return data
        except (ValueError, KeyError, TypeError, AttributeError, MantleError) as error:
            raise MantleError(
                f"Mantle Cloud at {self.base_url} returned an invalid agent list response. Check the server URL and API version."
            ) from error

    def exists(self, name: str) -> bool:
        try:
            self.latest(name)
            return True
        except NotFoundError:
            return False

    def latest(self, name: str) -> dict[str, Any]:
        validate_agent_name(name)
        return self._agent_response(self._request("GET", f"/api/agents/{name}"), upload=False)

    def put(self, name: str, yaml_text: str) -> dict[str, Any]:
        validate_agent_name(name)
        return self._agent_response(self._request(
            "PUT",
            f"/api/agents/{name}",
            content=yaml_text.encode("utf-8"),
            headers={"content-type": "application/yaml; charset=utf-8"},
        ), upload=True)

    def _agent_response(self, response: httpx.Response, *, upload: bool) -> dict[str, Any]:
        try:
            data = response.json()["data"]
            number = data["revision"]["number"]
            if type(number) is not int or number < 1:
                raise ValueError("revision must be a positive integer")
            if upload:
                if type(data["created"]) is not bool:
                    raise ValueError("created must be a boolean")
            elif not isinstance(data["yaml"], str):
                raise ValueError("yaml must be text")
            return data
        except (ValueError, KeyError, TypeError) as error:
            raise MantleError(f"Mantle Cloud at {self.base_url} returned an invalid agent response. Check the server URL and API version.") from error

    def delete(self, name: str) -> None:
        validate_agent_name(name)
        self._request("DELETE", f"/api/agents/{name}")
