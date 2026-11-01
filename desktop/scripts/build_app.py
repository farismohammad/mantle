#!/usr/bin/env python3
"""Build a self-contained local Mantle.app without replacing earlier builds.

Requires macOS, Swift command line tools, uv, and an existing relocatable Python.
Nothing is installed system-wide. Every invocation writes to a fresh directory.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import sys

DESKTOP = Path(__file__).resolve().parents[1]
REPOSITORY = DESKTOP.parent


def run(arguments: list[str], **kwargs) -> subprocess.CompletedProcess:
    print("→ " + " ".join(str(arg) for arg in arguments), flush=True)
    return subprocess.run([str(arg) for arg in arguments], check=True, **kwargs)


def fresh_directory(path: Path) -> Path:
    path = path.expanduser().resolve()
    path.mkdir(parents=True, exist_ok=False)
    return path


def standalone_python(explicit: str | None) -> Path:
    if explicit:
        candidates = [Path(explicit).expanduser()]
    else:
        base = Path.home() / ".local/share/uv/python"
        candidates = sorted(base.glob("cpython-3.12.*-macos-aarch64-none"), reverse=True)
        candidates += sorted(base.glob("cpython-3.11.*-macos-aarch64-none"), reverse=True)
    for candidate in candidates:
        if (candidate / "bin/python3").is_file() and (candidate / "lib").is_dir():
            return candidate.resolve()
    raise SystemExit("An existing relocatable Python is required. Pass --python-home /path/to/python-build-standalone. No Python installation was changed.")


def select_sdk(explicit: Path | None) -> Path:
    if explicit:
        sdk = explicit.expanduser().resolve()
        if not sdk.is_dir():
            raise SystemExit(f"SDK directory not found: {sdk}")
        return sdk
    sdk = Path(subprocess.run(["xcrun", "--sdk", "macosx", "--show-sdk-path"], check=True, capture_output=True, text=True).stdout.strip()).resolve()
    swiftc = Path(subprocess.run(["xcrun", "--find", "swiftc"], check=True, capture_output=True, text=True).stdout.strip()).resolve()
    plugins = swiftc.parent.parent / "lib/swift/host/plugins"
    fallback = sdk.parent / "MacOSX26.5.sdk"
    # Some Command Line Tools installations ship the macOS 27 SwiftUI
    # interfaces without the SwiftUIMacros plugin that those interfaces need.
    # Select an existing SDK for this build only; never change xcode-select.
    if sdk.name.startswith("MacOSX27") and fallback.is_dir() and not any(plugins.glob("*SwiftUIMacros*")):
        print("Using installed macOS 26.5 SDK: the selected toolchain has no SwiftUIMacros plugin for macOS 27.", flush=True)
        return fallback
    return sdk


def prepare_dependencies(build_root: Path, python_home: Path) -> Path:
    uv = shutil.which("uv") or str(Path.home() / ".local/bin/uv")
    if not Path(uv).is_file():
        raise SystemExit("uv was not found. Provide a prepared dependency folder with --dependencies.")
    requirements = build_root / "requirements.txt"
    cache = build_root / "uv-cache"
    with requirements.open("x") as stream:
        run([uv, "export", "--project", str(REPOSITORY), "--cache-dir", str(cache), "--frozen", "--no-dev", "--no-emit-project", "--format", "requirements-txt"], stdout=stream)
    dependencies = build_root / "dependencies"
    run([uv, "pip", "install", "--python", str(python_home / "bin/python3"), "--target", str(dependencies), "--cache-dir", str(cache), "--requirements", str(requirements)])
    return dependencies


def copytree(source: Path, destination: Path) -> None:
    shutil.copytree(source, destination, symlinks=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store", ".pytest_cache"))


def is_macho(path: Path) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    with path.open("rb") as stream:
        return stream.read(4) in {b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xce\xfa\xed\xfe"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", help="New build directory; it must not already exist")
    parser.add_argument("--python-home", help="Existing relocatable Python distribution directory")
    parser.add_argument("--node-home", type=Path, help="Verified portable Node distribution to bundle with npm")
    parser.add_argument("--dependencies", type=Path, help="Existing folder of Python dependencies to copy")
    parser.add_argument("--prepare-dependencies", action="store_true", help="Prepare dependencies only, without compiling the app")
    parser.add_argument("--binary", type=Path, help="Use this already-built Mantle executable")
    parser.add_argument("--sdk", type=Path, help="Use an existing macOS SDK for this build only")
    parser.add_argument("--qa-home", type=Path, help="Create a separate QA app using this existing isolated Mantle home")
    parser.add_argument("--configuration", choices=["debug", "release"], default="release")
    parser.add_argument("--skip-sign", action="store_true", help="Skip local ad-hoc signing")
    args = parser.parse_args()
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise SystemExit("This build configuration targets Apple Silicon Macs.")
    qa_home = args.qa_home.expanduser().resolve() if args.qa_home else None
    if qa_home is not None and not qa_home.is_dir():
        raise SystemExit(f"QA home must be an existing isolated directory: {qa_home}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    build_root = fresh_directory(Path(args.output) if args.output else DESKTOP / "builds" / stamp)
    python_home = standalone_python(args.python_home)
    node_home = args.node_home.expanduser().resolve() if args.node_home else None
    if node_home is not None and not (node_home / "bin/node").is_file():
        raise SystemExit(f"Portable Node executable not found: {node_home}")
    dependencies = args.dependencies.expanduser().resolve() if args.dependencies else prepare_dependencies(build_root, python_home)
    if not dependencies.is_dir():
        raise SystemExit(f"Dependency directory not found: {dependencies}")
    probe_env = dict(os.environ, PYTHONPATH=str(dependencies), PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1")
    run([str(python_home / "bin/python3"), "-c", "import httpx,pydantic,yaml,rich,typer; print('Engine dependencies verified')"], env=probe_env)
    if args.prepare_dependencies:
        print(json.dumps({"dependencies": str(dependencies), "python_home": str(python_home)}))
        return
    sdk = select_sdk(args.sdk)
    binary = args.binary.expanduser().resolve() if args.binary else None
    if binary is None:
        scratch = build_root / "swift"
        swift_env = dict(os.environ, CLANG_MODULE_CACHE_PATH=str(build_root / "clang-module-cache"), SWIFTPM_MODULECACHE_OVERRIDE=str(build_root / "swift-module-cache"))
        swift_command = ["swift", "build", "--package-path", str(DESKTOP), "--sdk", str(sdk), "--scratch-path", str(scratch), "--cache-path", str(build_root / "swift-cache"), "--config-path", str(build_root / "swift-config"), "--security-path", str(build_root / "swift-security"), "--manifest-cache", "local", "--configuration", args.configuration]
        run(swift_command + ["--product", "Mantle"], env=swift_env)
        result = run(swift_command + ["--show-bin-path"], capture_output=True, text=True, env=swift_env)
        binary = Path(result.stdout.strip()) / "Mantle"
    if not binary.is_file():
        raise SystemExit(f"Executable not found: {binary}")
    app = build_root / "Mantle.app"
    contents = fresh_directory(app / "Contents")
    executables = fresh_directory(contents / "MacOS")
    resources = fresh_directory(contents / "Resources")
    shutil.copy2(binary, executables / "Mantle")
    with (DESKTOP / "Resources/Info.plist").open("rb") as stream:
        info = plistlib.load(stream)
    if qa_home is not None:
        info["CFBundleIdentifier"] = "cloud.mantle.desktop.qa"
        info["CFBundleName"] = "Mantle QA"
        info["CFBundleDisplayName"] = "Mantle QA"
        info["LSEnvironment"] = {"MANTLE_HOME": str(qa_home)}
    with (contents / "Info.plist").open("xb") as stream:
        plistlib.dump(info, stream)
    with (contents / "PkgInfo").open("xb") as stream:
        stream.write(b"APPL????")
    copytree(python_home, resources / "python")
    if node_home is not None:
        copytree(node_home, resources / "node")
    copytree(dependencies, resources / "dependencies")
    copytree(REPOSITORY / "src/mantle", resources / "engine/mantle")
    copytree(DESKTOP / "bridge", resources / "bridge")
    shutil.copy2(REPOSITORY / "LICENSE", resources / "Mantle-LICENSE.txt")
    shutil.copy2(DESKTOP / "BUILD.md", resources / "Build-and-distribution.md")
    shutil.copy2(DESKTOP / "README.md", resources / "Getting-started.md")
    shutil.copy2(DESKTOP / "FEATURES.md", resources / "Feature-coverage.md")
    iconset = build_root / "Mantle.iconset"
    run(["swift", "-sdk", str(sdk), "-module-cache-path", str(build_root / "swift-module-cache"), str(DESKTOP / "scripts/make_icon.swift"), str(iconset)])
    run(["iconutil", "--convert", "icns", str(iconset), "--output", str(resources / "Mantle.icns")])
    manifest = {
        "builtAt": datetime.now(timezone.utc).isoformat(),
        "architecture": "arm64",
        "minimumMacOS": "14.0",
        "variant": "QA" if qa_home else "standard",
        "sdk": str(sdk),
        "signing": "unsigned" if args.skip_sign else "local ad-hoc; not notarized",
        "python": python_home.name,
        "node": json.loads((node_home / "mantle-node-provenance.json").read_text()) if node_home and (node_home / "mantle-node-provenance.json").is_file() else (node_home.name if node_home else None),
        "sourceExecutableSHA256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "dependencies": sorted(
            ({"name": d.metadata.get("Name", "unknown"), "version": d.version}
             for d in importlib.metadata.distributions(path=[str(dependencies)])),
            key=lambda item: item["name"].lower(),
        ),
        "sourceCommit": subprocess.run(["git", "-C", str(REPOSITORY), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip(),
        # True means the app includes changes that sourceCommit doesn't record.
        "sourceModified": bool(subprocess.run(["git", "-C", str(REPOSITORY), "status", "--porcelain"], capture_output=True, text=True).stdout.strip()),
    }
    (resources / "build-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    # Each copied native component receives a fresh local signature. The source
    # Python distribution and dependency directory are never signed or modified.
    if not args.skip_sign:
        native_components = [path for path in sorted(resources.rglob("*")) if is_macho(path)]
        print(f"→ Ad-hoc signing {len(native_components)} bundled native components", flush=True)
        for path in native_components:
            subprocess.run(["codesign", "--force", "--sign", "-", "--preserve-metadata=entitlements", str(path)], check=True, capture_output=True, text=True)
        run(["codesign", "--force", "--sign", "-", str(app)])
        run(["codesign", "--verify", "--deep", "--strict", str(app)])
    test_home = fresh_directory(build_root / "verification-home")
    if node_home is not None:
        bundled_node = resources / "node/bin/node"
        run([str(bundled_node), "--version"])
        npm_env = dict(os.environ, NPM_CONFIG_USERCONFIG=os.devnull, NPM_CONFIG_GLOBALCONFIG=str(test_home / "global-npmrc"), NPM_CONFIG_CACHE=str(test_home / "npm-cache"))
        run([str(bundled_node), str(resources / "node/lib/node_modules/npm/bin/npm-cli.js"), "--version"], env=npm_env)
    engine_env = dict(os.environ,
                      PYTHONPATH=str(resources / "engine") + ":" + str(resources / "dependencies"),
                      PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1", MANTLE_HOME=str(test_home))
    verification = run([str(resources / "python/bin/python3"), str(resources / "bridge/mantle_bridge.py")],
                       input='{"operation":"snapshot"}', capture_output=True, text=True, env=engine_env)
    response = json.loads(verification.stdout)
    if not response.get("ok"):
        raise SystemExit("Packaged engine verification failed: " + str(response.get("error")))
    print(json.dumps({"app": str(app), "engineVerification": "passed", "signing": manifest["signing"]}, indent=2))


if __name__ == "__main__":
    main()
