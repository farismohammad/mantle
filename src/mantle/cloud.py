from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from mantle.errors import MantleError, NotFoundError
from mantle.agents import validate_agent_name
from mantle.paths import MantlePaths
from mantle.settings import load_settings

DEFAULT_CLOUD_URL = "http://localhost:3100"
# Probed by `mantle cloud connect` when no URL is given: the documented Docker
# port, then the Next.js development server port.
DISCOVERY_CANDIDATES = ("http://localhost:3100", "http://localhost:3000")
STATE_FILE = "mantle-state.json"


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


def _is_local_host(host: str) -> bool:
    host = host.strip("[]").lower()
    if host in {"localhost", "host.docker.internal"} or host.endswith((".local", ".localhost", ".internal", ".lan")):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return "." not in host  # A bare intranet host name such as "nas".
    return address.is_loopback or address.is_private or address.is_link_local


def is_loopback(url: str) -> bool:
    host = (httpx.URL(url).host or "").strip("[]").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def redact(url: str) -> str:
    """Show a URL without credentials (used only after it has parsed)."""
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL:
        return "the configured URL"
    return str(parsed.copy_with(username=None, password=None)) if parsed.userinfo else url


def normalize_cloud_url(value: str) -> tuple[str, list[str]]:
    """Turn what people type into a registry base URL, explaining each change."""
    notes: list[str] = []
    text = value.strip()
    if not text:
        raise MantleError("Enter the Mantle Cloud address, for example http://localhost:3100.")
    if "://" not in text:
        host = text[1:text.find("]")] if text.startswith("[") and "]" in text else re.split(r"[:/]", text, maxsplit=1)[0]
        scheme = "http" if _is_local_host(host) else "https"
        text = f"{scheme}://{text}"
        notes.append(f"Added {scheme}:// (no scheme was given).")
    try:
        url = httpx.URL(text)
    except httpx.InvalidURL as error:
        raise MantleError("That is not a valid URL. Use a form like http://localhost:3100 or https://mantle.example.com.") from error
    if url.scheme not in {"http", "https"}:
        raise MantleError(f"Mantle Cloud URLs must start with http:// or https:// (got {url.scheme}://).")
    if not url.host:
        raise MantleError("The Mantle Cloud URL is missing a host name.")
    if url.userinfo:
        raise MantleError("Mantle Cloud URLs must not include a username or password.")
    if url.query or url.fragment:
        raise MantleError("Mantle Cloud URLs must not include a query (?…) or fragment (#…).")
    path = url.path.rstrip("/")
    for suffix in ("/api/agents", "/api", "/v1/health", "/agents"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            notes.append(f"Removed {suffix} from the end; Mantle adds API paths itself.")
            break
    base = str(url.copy_with(path=path or "/")).rstrip("/")
    return base, notes


def describe_transport_error(error: Exception, base_url: str) -> str:
    text = str(error)
    lowered = text.lower()
    host = httpx.URL(base_url).netloc.decode()
    if isinstance(error, httpx.TimeoutException):
        return f"timed out waiting for {host}. Check that the address is right and the server is running."
    if "wrong version number" in lowered or "record layer failure" in lowered or "packet length too long" in lowered:
        plain = base_url.replace("https://", "http://", 1)
        return f"{host} does not speak HTTPS. If it is a plain HTTP server, use {plain}"
    if "certificate verify failed" in lowered or "self-signed" in lowered or "self signed" in lowered:
        return (f"the TLS certificate of {host} is not trusted. If this is your own instance with a self-signed "
                f"certificate, reconnect with: mantle cloud connect {base_url} --insecure")
    if "name or service not known" in lowered or "nodename nor servname" in lowered or "getaddrinfo" in lowered or "name resolution" in lowered:
        return f"the host name in {base_url} could not be resolved. Check for a typo."
    if "connection refused" in lowered or "connect call failed" in lowered:
        return f"nothing is accepting connections at {host}. Is Mantle Cloud running, and is its port published?"
    return f"{text or type(error).__name__}"


def _is_html(response: httpx.Response) -> bool:
    return "html" in response.headers.get("content-type", "") or response.text.lstrip()[:1] == "<"


def _mantle_error_body(response: httpx.Response) -> dict[str, Any] | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    error = payload.get("error") if isinstance(payload, dict) else None
    return error if isinstance(error, dict) and isinstance(error.get("message"), str) else None


@dataclass
class ProbeResult:
    url: str
    ok: bool
    summary: str
    hint: str = ""
    agents: int | None = None
    notes: list[str] = field(default_factory=list)


class CloudClient:
    def __init__(
        self,
        paths: MantlePaths,
        *,
        base_url: str | None = None,
        client: httpx.Client | None = None,
        insecure: bool | None = None,
        timeout: float = 30,
    ):
        # Explicit URLs (cloud connect) must work even when config.json needs repair.
        stored = load_settings(paths) if base_url is None or insecure is None else None
        if base_url:
            selected, self.source = base_url, "argument"
        elif os.environ.get("MANTLE_CLOUD_URL"):
            selected, self.source = os.environ["MANTLE_CLOUD_URL"], "MANTLE_CLOUD_URL"
        elif stored and stored.cloud_url:
            selected, self.source = stored.cloud_url, str(paths.config)
        else:
            raise MantleError("Mantle Cloud is not configured. Connect to your registry with: mantle cloud connect")
        try:
            self.base_url, _ = normalize_cloud_url(selected)
        except MantleError as error:
            origin = "MANTLE_CLOUD_URL" if self.source == "MANTLE_CLOUD_URL" else f"cloud_url in {paths.config}" if self.source != "argument" else "the URL"
            raise MantleError(f"Invalid Mantle Cloud URL in {origin}: {error}") from error
        verify = not (insecure if insecure is not None else _env_flag("MANTLE_CLOUD_INSECURE", default=bool(stored and stored.cloud_insecure)))
        self.client = client or httpx.Client(timeout=timeout, follow_redirects=True, verify=verify)

    def set_timeout(self, seconds: float) -> None:
        self.client.timeout = httpx.Timeout(seconds)

    def _request(self, method: str, path: str, *, agent: str | None = None, **kwargs: Any) -> httpx.Response:
        try:
            response = self.client.request(method, f"{self.base_url}{path}", **kwargs)
        except (httpx.HTTPError, httpx.InvalidURL) as error:
            raise MantleError(f"Could not reach Mantle Cloud at {self.base_url}: {describe_transport_error(error, self.base_url)}") from error
        if response.status_code == 404:
            if _mantle_error_body(response) is not None:
                subject = f'Agent "{agent}"' if agent else "The requested agent"
                raise NotFoundError(f"{subject} does not exist in Mantle Cloud at {self.base_url}.")
            raise MantleError(
                f"{self.base_url} answered {path} with 404 Not Found, so it does not look like a Mantle Cloud API. "
                "Check the address with: mantle cloud status"
            )
        if not response.is_success:
            error = _mantle_error_body(response)
            if error is not None:
                detail = error["message"]
                issues = error.get("details", {}).get("issues", []) if isinstance(error.get("details"), dict) else []
                if isinstance(issues, list) and issues:
                    detail += "\n" + "\n".join(
                        f"  {issue.get('path', 'configuration')}: {issue.get('message', 'invalid')}"
                        for issue in issues if isinstance(issue, dict)
                    )
            elif _is_html(response):
                detail = f"{response.reason_phrase} (the server returned a web page, not the Mantle Cloud API)"
            else:
                detail = response.text.strip()[:300] or response.reason_phrase
            raise MantleError(f"Mantle Cloud rejected the request ({response.status_code}): {detail}")
        return response

    def _invalid(self, response: httpx.Response, what: str) -> MantleError:
        if _is_html(response):
            return MantleError(
                f"Mantle Cloud at {self.base_url} returned a web page instead of a Mantle Cloud {what} response. "
                "The address probably points at a different site; check it with: mantle cloud status"
            )
        return MantleError(f"Mantle Cloud at {self.base_url} returned an invalid {what} response. Check the server URL and API version.")

    def health(self) -> dict[str, Any]:
        response = self._request("GET", "/v1/health")
        try:
            data = response.json()["data"]
            if not isinstance(data, dict):
                raise ValueError
            return data
        except (ValueError, KeyError, TypeError) as error:
            raise self._invalid(response, "health") from error

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
                    not isinstance(item.get("description", ""), str)
                    or type(item.get("revision")) is not int
                    or item["revision"] < 1
                    or not isinstance(item.get("updatedAt", ""), str)
                ):
                    raise ValueError("agent summary fields are invalid")
            return data
        except (ValueError, KeyError, TypeError, AttributeError, MantleError) as error:
            raise self._invalid(response, "agent list") from error

    def exists(self, name: str) -> bool:
        try:
            self.latest(name)
            return True
        except NotFoundError:
            return False

    def latest(self, name: str) -> dict[str, Any]:
        validate_agent_name(name)
        return self._agent_response(self._request("GET", f"/api/agents/{name}", agent=name), upload=False)

    def put(self, name: str, yaml_text: str) -> dict[str, Any]:
        validate_agent_name(name)
        return self._agent_response(self._request(
            "PUT",
            f"/api/agents/{name}",
            agent=name,
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
            raise self._invalid(response, "agent") from error

    def delete(self, name: str) -> None:
        validate_agent_name(name)
        self._request("DELETE", f"/api/agents/{name}", agent=name)


def probe(paths: MantlePaths, url: str, *, insecure: bool = False, timeout: float = 5,
          client: httpx.Client | None = None) -> ProbeResult:
    """Check that `url` is reachable and is a Mantle Cloud, explaining any failure."""
    try:
        base, notes = normalize_cloud_url(url)
    except MantleError as error:
        return ProbeResult(url, False, str(error))
    cloud = CloudClient(paths, base_url=base, insecure=insecure, timeout=timeout, client=client)
    result = ProbeResult(base, False, "", notes=notes)
    try:
        response = cloud.client.get(f"{base}/v1/health")
    except (httpx.HTTPError, httpx.InvalidURL) as error:
        result.summary = f"Could not connect: {describe_transport_error(error, base)}"
        return result
    identified = False
    if response.is_success and not _is_html(response):
        try:
            data = response.json().get("data", {})
            identified = isinstance(data, dict) and data.get("service") == "mantle-cloud"
        except (ValueError, AttributeError):
            identified = False
    if not identified:
        if _is_html(response) and response.is_success:
            result.summary = f"{base} is a website, not a Mantle Cloud API."
        else:
            result.summary = f"{base} answered, but it is not a Mantle Cloud (GET /v1/health returned HTTP {response.status_code})."
        result.hint = "Check the host and port. The Docker setup publishes Mantle Cloud on http://localhost:3100 by default."
        return result
    try:
        result.agents = len(cloud.list())
    except MantleError:
        result.agents = None
    result.ok = True
    result.summary = f"Connected to Mantle Cloud at {base}"
    return result


# Local sync bookkeeping ------------------------------------------------------

def yaml_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def record_sync(agent_root: Path, base_url: str, revision: int, yaml_text: str) -> None:
    from mantle.storage import write_json
    write_json(agent_root / STATE_FILE, {
        "cloud": {
            "url": base_url,
            "revision": revision,
            "sha256": yaml_digest(yaml_text),
            "synced_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    })


def read_sync(agent_root: Path) -> dict[str, Any] | None:
    path = agent_root / STATE_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        cloud = data.get("cloud") if isinstance(data, dict) else None
        return cloud if isinstance(cloud, dict) else None
    except (OSError, ValueError):
        return None


def local_changes(agent_root: Path) -> bool | None:
    """True when agent.yaml changed since the last push/pull/update; None if unknown."""
    state = read_sync(agent_root)
    if state is None:
        return None
    try:
        return yaml_digest((agent_root / "agent.yaml").read_text(encoding="utf-8")) != state.get("sha256")
    except OSError:
        return None


def sync_status(agent_root: Path | None, base_url: str, remote_revision: int | None) -> str:
    if agent_root is None:
        return "only in Cloud (mantle cloud pull NAME)" if remote_revision else "missing"
    if not (agent_root / "agent.yaml").is_file():
        return "damaged locally (no agent.yaml)"
    if remote_revision is None:
        return "only local (not pushed)"
    state = read_sync(agent_root)
    if state is None or state.get("url") != base_url:
        return "unknown (not synced from this machine yet)"
    changed = local_changes(agent_root)
    newer = remote_revision > int(state.get("revision", 0))
    if changed and newer:
        return "both changed"
    if changed:
        return "local changes not pushed"
    if newer:
        return f"Cloud has a newer revision ({remote_revision})"
    return "in sync"
