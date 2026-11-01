#!/usr/bin/env python3
"""Fetch a pinned official Node.js Apple Silicon archive into a new folder.

Verifies SHA-256 against that release's official HTTPS checksum list. Does not
install Node, alter PATH, run install scripts, or replace an existing folder.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import urllib.request

DESKTOP = Path(__file__).resolve().parents[1]
DEFAULT_VERSION = "v22.23.4"  # Official Node22 security release, 2026-10-20.


def fetch(url: str, destination: Path) -> None:
    print("Downloading " + url, flush=True)
    with urllib.request.urlopen(url, timeout=60) as response, destination.open("xb") as stream:
        while chunk := response.read(1024 * 1024):
            stream.write(chunk)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=DEFAULT_VERSION, help="Pinned Node22 release, including v prefix")
    parser.add_argument("--output", type=Path, help="New destination directory; must not already exist")
    args = parser.parse_args()
    if not re.fullmatch(r"v22\.\d+\.\d+", args.version) or tuple(map(int, args.version[1:].split("."))) < (22,20,0):
        raise SystemExit("Choose a supported Node22 release at least v22.20.0.")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    root = args.output.expanduser().resolve() if args.output else DESKTOP / "builds" / ("node-" + stamp)
    root.mkdir(parents=True, exist_ok=False)
    release_url = "https://nodejs.org/dist/" + args.version
    package_name = "node-" + args.version + "-darwin-arm64"
    archive_name = package_name + ".tar.xz"
    checksums = root / "SHASUMS256.txt"
    archive = root / archive_name
    fetch(release_url + "/SHASUMS256.txt", checksums)
    matches = [line.split()[0] for line in checksums.read_text().splitlines() if len(line.split()) == 2 and line.split()[1] == archive_name]
    if len(matches) != 1 or not re.fullmatch(r"[0-9a-f]{64}", matches[0]):
        raise SystemExit("Official checksum list has no unique SHA-256 for this archive.")
    fetch(release_url + "/" + archive_name, archive)
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != matches[0]:
        raise SystemExit("SHA-256 mismatch. Archive was retained for inspection and was not extracted.")
    with tarfile.open(archive, "r:xz") as tar:
        members = tar.getmembers()
        seen: set[str] = set()
        for member in members:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != package_name:
                raise SystemExit("Archive contains an unexpected path; nothing was extracted.")
            if member.name in seen:
                raise SystemExit("Archive contains duplicate paths; nothing was extracted.")
            seen.add(member.name)
        tar.extractall(root, members=members, filter="data")
    node_home = root / package_name
    node = node_home / "bin/node"
    actual = subprocess.run([str(node), "--version"], check=True, capture_output=True, text=True).stdout.strip()
    if actual != args.version:
        raise SystemExit("Extracted Node version does not match the pinned release.")
    npm_env = dict(os.environ, NPM_CONFIG_USERCONFIG=os.devnull, NPM_CONFIG_GLOBALCONFIG=str(root / "global-npmrc"), NPM_CONFIG_CACHE=str(root / "npm-cache"))
    npm = subprocess.run([str(node), str(node_home / "lib/node_modules/npm/bin/npm-cli.js"), "--version"], check=True, capture_output=True, text=True, env=npm_env).stdout.strip()
    provenance = {
        "version": actual, "npm": npm, "platform": "darwin-arm64",
        "url": release_url + "/" + archive_name,
        "checksumURL": release_url + "/SHASUMS256.txt",
        "sha256": digest,
        "verifiedAt": datetime.now(timezone.utc).isoformat(),
        "verification": "SHA-256 matched official release checksum list over HTTPS; not a separate signature verification",
    }
    with (node_home / "mantle-node-provenance.json").open("x") as stream:
        json.dump(provenance, stream, indent=2); stream.write("\n")
    print(json.dumps({"node_home": str(node_home), **provenance}, indent=2))


if __name__ == "__main__":
    main()
