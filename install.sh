#!/bin/sh

set -eu

PROGRAM=${0##*/}
SOURCE=${MANTLE_SOURCE:-}
REF=${MANTLE_REF:-}
TEMP_DIR=

usage() {
    cat <<EOF
Install the Mantle CLI for the current user.

Usage:
  $PROGRAM [--source PATH_OR_GIT_URL] [--ref BRANCH_OR_TAG]

Options:
  --source  Repository checkout, package directory, or Git URL.
            Defaults to the checkout containing this script, or MANTLE_SOURCE.
  --ref     Branch or tag to clone when --source is a Git URL.
            MANTLE_REF provides the same setting.
  -h, --help

Examples:
  ./install.sh
  ./install.sh --source https://github.com/farismohammad/mantle.git --ref main
EOF
}

fail() {
    printf 'mantle installer: %s\n' "$*" >&2
    exit 1
}

cleanup() {
    if [ -n "$TEMP_DIR" ] && [ -d "$TEMP_DIR" ]; then
        rm -rf -- "$TEMP_DIR"
    fi
}

trap cleanup EXIT HUP INT TERM

while [ "$#" -gt 0 ]; do
    case "$1" in
        --source)
            [ "$#" -ge 2 ] || fail "--source requires a value"
            SOURCE=$2
            shift 2
            ;;
        --ref)
            [ "$#" -ge 2 ] || fail "--ref requires a value"
            REF=$2
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            fail "unknown argument: $1 (use --help for usage)"
            ;;
    esac
done

case "$(uname -s)" in
    Linux|Darwin) ;;
    *) fail "only Linux and macOS are supported" ;;
esac

command -v git >/dev/null 2>&1 || fail "Git is required; install it and run this script again"
command -v node >/dev/null 2>&1 || fail "Node.js is required; install it and run this script again"
command -v npm >/dev/null 2>&1 || fail "npm is required; install it and run this script again"

if [ -z "$SOURCE" ]; then
    SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" 2>/dev/null && pwd || true)
    if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/pyproject.toml" ]; then
        SOURCE=$SCRIPT_DIR
    else
        fail "no source was found; pass --source PATH_OR_GIT_URL or set MANTLE_SOURCE"
    fi
fi

TEMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/mantle-install.XXXXXX")

case "$SOURCE" in
    git@*|ssh://*|http://*|https://*|git://*|*.git)
        CHECKOUT=$TEMP_DIR/source
        printf 'Cloning Mantle from %s...\n' "$SOURCE"
        if [ -n "$REF" ]; then
            git clone --depth 1 --branch "$REF" -- "$SOURCE" "$CHECKOUT"
        else
            git clone --depth 1 -- "$SOURCE" "$CHECKOUT"
        fi
        ;;
    *)
        [ -z "$REF" ] || fail "--ref can only be used with a Git URL"
        [ -d "$SOURCE" ] || fail "source directory does not exist: $SOURCE"
        CHECKOUT=$(CDPATH= cd -- "$SOURCE" && pwd)
        ;;
esac

[ -f "$CHECKOUT/pyproject.toml" ] || fail "source does not contain pyproject.toml: $SOURCE"

PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
export PATH

if ! command -v uv >/dev/null 2>&1; then
    printf 'Installing uv for the current user...\n'
    UV_INSTALLER=$TEMP_DIR/uv-install.sh
    if command -v curl >/dev/null 2>&1; then
        curl -LsSf https://astral.sh/uv/install.sh -o "$UV_INSTALLER"
    elif command -v wget >/dev/null 2>&1; then
        wget -q https://astral.sh/uv/install.sh -O "$UV_INSTALLER"
    else
        fail "curl or wget is required to install uv"
    fi
    sh "$UV_INSTALLER"
fi

command -v uv >/dev/null 2>&1 || fail "uv was installed but is not available on PATH"

printf 'Installing Mantle CLI...\n'
uv tool install --force "$CHECKOUT"

if [ -n "${UV_TOOL_BIN_DIR:-}" ]; then
    TOOL_BIN=$UV_TOOL_BIN_DIR
else
    TOOL_BIN=$(uv tool dir --bin)
fi

MANTLE_BIN=$TOOL_BIN/mantle
[ -x "$MANTLE_BIN" ] || fail "installation completed but the mantle executable was not found at $MANTLE_BIN"
"$MANTLE_BIN" --help >/dev/null

printf '\nMantle was installed successfully: %s\n' "$MANTLE_BIN"
case ":$PATH:" in
    *:"$TOOL_BIN":*) ;;
    *) printf 'Add %s to PATH before running mantle.\n' "$TOOL_BIN" ;;
esac
printf 'Run "mantle --help" to get started.\n'
