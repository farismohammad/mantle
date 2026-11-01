# Mantle for macOS

This desktop app uses native SwiftUI and the existing Mantle Python engine. The Python interpreter and its libraries are packaged inside the app, so opening the app does not require a separate Python installation. The complete app build also includes portable Node.js and npm when built with `--node-home`.

## Build locally

Requirements: an Apple Silicon Mac running macOS 14 or later, Swift command line tools, `uv`, and an existing relocatable Python 3.11 or 3.12 distribution (such as a uv-managed Python).

From the repository root:

```sh
python3 desktop/scripts/prepare_node.py
python3 desktop/scripts/build_app.py --node-home /absolute/node_home/path/printed/above
```

`prepare_node.py` downloads the pinned Node.js 22.23.4 Apple Silicon release from nodejs.org into a new build directory, checks its SHA-256 against the official release checksum list, safely extracts it, and verifies Node/npm versions. It retains the original archive, checksum list, licenses, and a provenance record. It does not install Node system-wide. Pass `--version` to choose another supported Node22 release deliberately. A basic developer build may omit `--node-home` and use an existing Node installation instead; the final self-contained desktop bundle should include it.

The script creates a new dated folder under `desktop/builds/` and prints the absolute path to `Mantle.app`. It refuses to replace an existing output folder, never removes older builds, and does not install anything system-wide. Python dependencies are exported from the existing lockfile and downloaded into that build's directory. Pass `--python-home` to select a different existing standalone Python distribution.

To reuse already prepared dependencies or a compiled executable:

```sh
python3 desktop/scripts/build_app.py \
  --dependencies /absolute/path/to/dependencies \
  --node-home /absolute/path/to/node-v22.23.4-darwin-arm64 \
  --binary /absolute/path/to/Mantle
```

`--prepare-dependencies` prepares and verifies only the Python libraries. `--output` chooses a new output directory. `--skip-sign` skips local ad-hoc signing. `--sdk /absolute/path/to/MacOSX.sdk` selects an existing SDK for this build without changing system settings. If the default macOS 27 SDK is installed without its required SwiftUI macro plugin, the script selects an existing macOS 26.5 SDK automatically. This addresses that Command Line Tools packaging mismatch; full Xcode remains another supported build environment.

The produced app contains:

- A native Apple Silicon executable, window interface, menu commands, and brand icon.
- The Mantle engine source, unchanged from the repository snapshot.
- A relocatable Python interpreter and pinned engine dependencies.
- Portable Node.js and npm, when `--node-home` is supplied.
- The desktop JSON bridge and a build manifest.
- License and build documentation.

For visual testing against an existing fixture directory, pass `--qa-home /absolute/path/to/isolated-mantle-home`. That produces a separate **Mantle QA** identity (`cloud.mantle.desktop.qa`) and embeds its fixture `MANTLE_HOME` in the QA bundle's launch environment. The app directory remains named `Mantle.app`, so use its full dated build path. This option is only for QA; omit it for the final user build. Standard builds contain no fixture-home override and retain the normal Mantle data location. QA builds must not be distributed as the final app.

## Runtime requirements

Agent execution requires a Pi runtime and a provider login. Mantle can download Pi into its own data directory on the first run, or it can be installed from Runtimes. The complete bundle supplies Node.js and npm privately, without changing the Mac's Node installation. Developer builds without bundled Node still require Node.js 22.20 or later and npm. Git is required for Git-based resources. Docker Compose is only needed when running your own local registry. The app does not silently install these system tools. The Health screen identifies missing prerequisites.

Mantle data stays under `~/.mantle`, or the location selected through the `MANTLE_HOME` environment variable. The app can reuse an existing Mantle installation's agents and provider logins. Agent conversations open in a native Mantle session window; Terminal remains an alternate launch option. Provider browser-login flows use the existing interactive Terminal flow with the bundled engine. Keys are not placed on command lines.

A registry is optional. Connect to your self-hosted Mantle registry in the Registry screen. The existing v0.1 server has no built-in authentication; use its existing trusted-network or authenticating-proxy deployment model. An optional proxy token in the desktop app is kept only in memory for that app session.

## Distribution status

This local build is ad-hoc signed, not Developer ID signed or notarized. It is suitable for local development and review. Public distribution still requires the maintainer's Apple Developer signing identity, hardened-runtime/entitlement review, notarization, testing on the minimum supported macOS version, and a release installer. No signing certificate or account is configured by this script.

The build script verifies the packaged Python bridge with an empty, isolated temporary Mantle home. It does not run an agent, read provider credentials, publish to a registry, or alter the user's Mantle data during packaging.

The minimum operating-system declaration is a build target, not a claim of testing on every supported macOS version. Provider authentication, paid model requests, downloading skills/packages, and deployment against the user's external registry require separate end-to-end verification with the user's environment. A successful local compile or isolated bridge test does not establish those live integrations.

## Source and credits

The desktop port starts from the published [Mantle CLI](https://github.com/farismohammad/mantle) `main` snapshot `50ae59ccfd69af8fd58cd15b4eb49fc7d4d512f6`. The original engine remains in `src/mantle`; this port's additions are under `desktop/`. The build manifest records the current checkout commit for each app, and `sourceModified` is true when the build includes uncommitted changes. Unpublished changes in another checkout are not included in this source snapshot.

The brand palette and cloak/M mark come from [Mantle Web](https://github.com/farismohammad/mantle-web), snapshot `60a1fe1beaa551a2d609de132567cd1085dbbac7`. The icon adapts that existing vector to a macOS enclosure; the app does not imply Apple authorship or endorsement. SwiftUI, AppKit, system fonts, and SF Symbols are supplied by macOS.

Mantle is licensed under Apache-2.0; its license is included as `Contents/Resources/Mantle-LICENSE.txt`. CPython and its bundled components retain their own licenses, including `Contents/Resources/python/lib/python3.12/LICENSE.txt` for the packaged Python 3.12 build (the directory follows the selected Python version). Python dependency license and attribution files are retained in `Contents/Resources/dependencies/*.dist-info/`. Pi and downloaded third-party skills/packages retain their own licenses and are managed separately from the app bundle. Review all component notices when preparing a public release.

Bundled Node.js, npm, and their dependencies retain their own licenses, including `Contents/Resources/node/LICENSE` and the included npm notices. `Contents/Resources/node/mantle-node-provenance.json` records the official release URL, checksum source, verified digest, and versions. The checksum check establishes correspondence with the official HTTPS download; it is not a separate release-signature verification.
