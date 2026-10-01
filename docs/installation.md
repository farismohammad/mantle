# Install Mantle CLI

The CLI repository is [`farismohammad/mantle`](https://github.com/farismohammad/mantle).
The optional registry is a separate project:
[`farismohammad/mantleagent.cloud`](https://github.com/farismohammad/mantleagent.cloud).
Installing the CLI does not install or expose a Cloud server.

## Requirements

- Linux or macOS.
- Node.js 22.20.0 or newer, with npm. Mantle uses Node/npm to install and run
  Pi and its packages.
- Git for remote CLI installation and Git-based skills or packages.
- `uv` and Python 3.11 or newer. If `uv` is missing, the installer downloads
  Astral's current installer over HTTPS and runs it for the current user; `uv`
  obtains a compatible Python interpreter when needed.
- `curl` and working CA certificates if `uv` must be downloaded. The installer
  does not pin the `uv` installer to a version or checksum.

The installer does not use `sudo`. Review its source before running it. For a
tagged release, use the version tag instead of the moving default branch:

```bash
git clone --branch v0.1.0 https://github.com/farismohammad/mantle.git
cd mantle
./install.sh
export PATH="$HOME/.local/bin:$PATH"
mantle --help
```

If the `v0.1.0` tag has not been published yet, select a reviewed commit and
download its source archive from the CLI repository's commit page. Extract the
archive and run `./install.sh` from the extracted CLI repository directory.
Do not treat a moving branch archive as a pinned release.

When using a copied installer, copy it from the CLI repository and pass the
release tag explicitly:

```bash
./install.sh --source https://github.com/farismohammad/mantle.git --ref v0.1.0
```

The PATH export above is needed when `uv` uses its default tool directory. The
installer prints the exact PATH profile line if its selected tool directory
differs. Re-running the installer upgrades or repairs the CLI.
`MANTLE_SOURCE` and `MANTLE_REF` are equivalent to the corresponding options.

## Ubuntu and Debian prerequisites

On a clean Ubuntu or Debian host, install the base tools:

```bash
sudo apt-get update
sudo apt-get install ca-certificates curl git xz-utils
```

Install Node from the official release archive. This example uses the minimum
supported version and the x64 Linux archive; use `arm64` for a 64-bit ARM host.
The checksum file and archive come from the same HTTPS release directory.

```bash
NODE_VERSION=22.20.0
NODE_ARCH=x64
NODE_FILE="node-v${NODE_VERSION}-linux-${NODE_ARCH}.tar.xz"
NODE_DIR="node-v${NODE_VERSION}-linux-${NODE_ARCH}"
NODE_URL="https://nodejs.org/dist/v${NODE_VERSION}"
mkdir -p "$HOME/.local/opt"
cd "$HOME/.local/opt"
curl -fSLO "$NODE_URL/$NODE_FILE"
curl -fSLO "$NODE_URL/SHASUMS256.txt"
grep " $NODE_FILE$" SHASUMS256.txt | sha256sum --check -
tar -xJf "$NODE_FILE"
export PATH="$HOME/.local/opt/$NODE_DIR/bin:$PATH"
node --version
npm --version
```

The checksum file is served over HTTPS from the same Node.js release directory
as the archive; this example does not verify a detached signature. The official
archive lists available platforms and checksum files at
[Node.js v22.20.0](https://nodejs.org/en/download/archive/v22.20.0). On macOS,
use the official macOS installer from the Node.js archive and confirm its
published checksum before installation.

## Verify the installation

```bash
node --version
npm --version
git --version
uv --version
mantle --help
```

If `mantle` is not found after installation, add the PATH line printed by the
installer to your shell profile and open a new shell.
