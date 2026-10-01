"""Validate the upstream package references supported by Pi and Mantle."""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from mantle.errors import MantleError

NPM_SOURCE = re.compile(r"^npm:(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*(?:@[^\s/@\\]+)?$")


def validate_package_source(source: str) -> None:
    if source.startswith("npm:"):
        if not NPM_SOURCE.fullmatch(source):
            raise MantleError("Invalid npm package source. Use npm:package@version or npm:@scope/package@version.")
        return
    git_package_path(source)


def git_package_path(source: str) -> Path:
    """Pi caches Git packages by host/repository, independent of their alias."""
    value = source.removeprefix("git:")
    for prefix, host in (("github:", "github.com"), ("gitlab:", "gitlab.com"), ("bitbucket:", "bitbucket.org")):
        if value.startswith(prefix):
            value = f"https://{host}/{value[len(prefix):]}"
            break
    if value.startswith("git@"):
        value = "ssh://" + value.replace(":", "/", 1)
    if source.startswith("git:") and "." in value.split("/", 1)[0] and not value.startswith((".", "/", "~")) and "/" in value:
        value = "https://" + value
    if not value.startswith(("https://", "http://", "ssh://", "git://")):
        raise MantleError("Package source must be an npm: package or a Git URL. Local package references are not supported because Mantle keeps installed resources inside the agent.")
    try:
        parsed = urlparse(value)
        host = parsed.hostname
        parsed.port  # Validate invalid ports while reporting a normal configuration error.
    except ValueError as error:
        raise MantleError(f"Invalid Git package URL: {error}") from error
    if re.search(r"%(?![0-9a-fA-F]{2})", parsed.path):
        raise MantleError("Git package source contains an invalid percent escape.")
    try:
        path = unquote(parsed.path, errors="strict").lstrip("/").split("@", 1)[0].removesuffix(".git")
    except UnicodeError as error:
        raise MantleError("Git package source contains an invalid encoded path.") from error
    if (not host or len(Path(path).parts) < 2 or ".." in Path(path).parts
            or "\\" in path or "\x00" in path or parsed.query or parsed.fragment or any(c.isspace() for c in value)):
        raise MantleError("Git package source must include a host and repository path, such as https://github.com/owner/repo.")
    if parsed.password is not None or (parsed.username is not None and parsed.scheme in {"http", "https"}):
        raise MantleError("Git package URLs must not contain credentials. Use your local Git credential helper.")
    return Path(host) / path
