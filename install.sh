#!/bin/sh
# Install the Mantle CLI for the current user (no sudo), then guide first-run setup.

set -eu

PROGRAM=${0##*/}
DEFAULT_SOURCE=https://github.com/farismohammad/mantle.git
SOURCE=${MANTLE_SOURCE:-}
REF=${MANTLE_REF:-}
NODE_MIN=22.20.0
NODE_FALLBACK=22.20.0
NODE_ROOT=$HOME/.local/opt
ASSUME_YES=0
WITH_NODE=ask
MODIFY_PATH=ask
RUN_SETUP=1
UNINSTALL=0
PURGE=0
TEMP_DIR=
STEP="starting"
DONE=0
ORIGINAL_PATH=${PATH-}
PATH_ADDITIONS=
PROFILE=
PROFILE_UPDATED=0

usage() {
    cat <<EOF
Install the Mantle CLI for the current user. No sudo is needed.

Usage:
  $PROGRAM [options]

Options:
  --source PATH_OR_GIT_URL  Repository checkout, package directory, or Git URL.
                            Defaults to this checkout, or the official repository
                            when the script is piped. MANTLE_SOURCE also works.
  --ref BRANCH_OR_TAG       Branch or tag to clone when --source is a Git URL.
                            MANTLE_REF provides the same setting.
  --with-node               Install Node.js 22 LTS for this user if it is missing
                            or too old (checksum-verified, into ~/.local/opt).
  --no-node                 Never offer to install Node.js.
  --no-modify-path          Do not edit shell profiles; print the PATH line instead.
  --no-setup                Do not start the guided \`mantle setup\` afterwards.
  -y, --yes                 Accept every default without asking.
  --uninstall               Remove the Mantle CLI and its PATH entries.
  --purge                   With --uninstall, also delete ~/.mantle (agents, logins).
  -h, --help                Show this help.

Examples:
  ./install.sh
  ./install.sh --with-node --yes
  ./install.sh --source https://github.com/farismohammad/mantle.git --ref v0.1.0
EOF
}

say() { printf '%s\n' "$*"; }
warn() { printf 'mantle installer: %s\n' "$*" >&2; }
fail() {
    printf 'mantle installer: %s\n' "$*" >&2
    DONE=2
    exit 1
}

cleanup() {
    status=$?
    if [ -n "$TEMP_DIR" ] && [ -d "$TEMP_DIR" ]; then
        rm -rf -- "$TEMP_DIR"
    fi
    if [ "$status" -ne 0 ] && [ "$DONE" -eq 0 ]; then
        printf '\nmantle installer: installation did not complete (while %s).\n' "$STEP" >&2
        printf 'Fix the problem above and run the installer again; it is safe to rerun.\n' >&2
    fi
}

trap cleanup EXIT
trap 'exit 130' HUP INT TERM

interactive() {
    case "${MANTLE_INTERACTIVE:-}" in
        1) return 0 ;;
        0) return 1 ;;
    esac
    [ -t 1 ] && { [ -t 0 ] || { [ -r /dev/tty ] && (: </dev/tty) 2>/dev/null; }; }
}

# ask QUESTION DEFAULT(y|n): succeeds for yes. Reads the terminal even when piped.
ask() {
    question=$1
    default=$2
    if [ "$ASSUME_YES" -eq 1 ]; then
        [ "$default" = y ]
        return
    fi
    if ! interactive; then
        return 1
    fi
    if [ "$default" = y ]; then hint="[Y/n]"; else hint="[y/N]"; fi
    printf '%s %s ' "$question" "$hint"
    if [ -t 0 ]; then read -r answer || answer=; else read -r answer </dev/tty || answer=; fi
    case "$answer" in
        [Yy]*) return 0 ;;
        [Nn]*) return 1 ;;
        *) [ "$default" = y ] ;;
    esac
}

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
        --with-node) WITH_NODE=yes; shift ;;
        --no-node) WITH_NODE=no; shift ;;
        --no-modify-path) MODIFY_PATH=no; shift ;;
        --no-setup) RUN_SETUP=0; shift ;;
        -y|--yes) ASSUME_YES=1; shift ;;
        --uninstall) UNINSTALL=1; shift ;;
        --purge) PURGE=1; shift ;;
        -h|--help)
            usage
            DONE=1
            exit 0
            ;;
        *)
            fail "unknown argument: $1 (use --help for usage)"
            ;;
    esac
done

# Platform ------------------------------------------------------------------

OS=$(uname -s)
case "$OS" in
    Linux) NODE_OS=linux ;;
    Darwin) NODE_OS=darwin ;;
    *) fail "only Linux and macOS are supported (found $OS). On Windows, use WSL." ;;
esac
case "$(uname -m)" in
    x86_64|amd64) NODE_ARCH=x64 ;;
    aarch64|arm64) NODE_ARCH=arm64 ;;
    *) NODE_ARCH= ;;
esac

PKG=
for candidate in brew apt-get dnf yum pacman zypper apk; do
    if command -v "$candidate" >/dev/null 2>&1; then PKG=$candidate; break; fi
done

package_hint() {
    package=$1
    case "$PKG" in
        brew) printf 'brew install %s' "$package" ;;
        apt-get) printf 'sudo apt-get install -y %s' "$package" ;;
        dnf|yum) printf 'sudo %s install -y %s' "$PKG" "$package" ;;
        pacman) printf 'sudo pacman -S %s' "$package" ;;
        zypper) printf 'sudo zypper install %s' "$package" ;;
        apk) printf 'sudo apk add %s' "$package" ;;
        *) if [ "$OS" = Darwin ] && [ "$package" = git ]; then printf 'xcode-select --install'; else printf 'install %s with your package manager' "$package"; fi ;;
    esac
}

node_hint() {
    if command -v fnm >/dev/null 2>&1; then
        printf 'fnm install 22 && fnm use 22'
    elif [ -n "${NVM_DIR:-}" ]; then
        printf 'nvm install 22 && nvm use 22'
    elif [ "$PKG" = brew ]; then
        printf 'brew install node@22 (or rerun with --with-node)'
    else
        printf 'rerun with --with-node to install it for your user, or use fnm/nvm'
    fi
}

# version_at_least VERSION MINIMUM: accepts v-prefixes and prerelease/build suffixes.
version_at_least() {
    printf '%s %s\n' "$1" "$2" | awk '
        function parse(text, parts,    clean, n) {
            sub(/^v/, "", text); clean = text; sub(/[-+].*$/, "", clean)
            n = split(clean, parts, ".")
            return n == 3 && parts[1] ~ /^[0-9]+$/ && parts[2] ~ /^[0-9]+$/ && parts[3] ~ /^[0-9]+$/
        }
        {
            if (!parse($1, v) || !parse($2, m)) exit 1
            for (i = 1; i <= 3; i++) {
                if (v[i] + 0 > m[i] + 0) exit 0
                if (v[i] + 0 < m[i] + 0) exit 1
            }
            exit 0
        }'
}

download() {
    url=$1
    destination=$2
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL --retry 2 "$url" -o "$destination"
    elif command -v wget >/dev/null 2>&1; then
        wget -q "$url" -O "$destination"
    else
        fail "curl or wget is required to download $url; $(package_hint curl)"
    fi
}

sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | awk '{print $1}'
    else
        return 1
    fi
}

add_path() {
    case ":$PATH:" in
        *:"$1":*) ;;
        *) PATH="$1:$PATH"; export PATH ;;
    esac
    # Newline-separated so directories containing spaces survive.
    case ":$ORIGINAL_PATH:" in
        *:"$1":*) ;;
        *)
            case "
$PATH_ADDITIONS
" in
                *"
$1
"*) ;;
                *) PATH_ADDITIONS="${PATH_ADDITIONS:+$PATH_ADDITIONS
}$1" ;;
            esac
            ;;
    esac
}

profile_file() {
    case "$(basename "${SHELL:-sh}")" in
        zsh) printf '%s' "${ZDOTDIR:-$HOME}/.zshrc" ;;
        bash) if [ "$OS" = Darwin ]; then printf '%s' "$HOME/.bash_profile"; else printf '%s' "$HOME/.bashrc"; fi ;;
        fish) printf '%s' "$HOME/.config/fish/conf.d/mantle.fish" ;;
        *) printf '%s' "$HOME/.profile" ;;
    esac
}

remove_profile_block() {
    file=$1
    [ -f "$file" ] || return 0
    if grep -q '^# >>> mantle >>>' "$file" 2>/dev/null; then
        awk '/^# >>> mantle >>>/{skip=1; next} /^# <<< mantle <<</{skip=0; next} !skip' "$file" >"$file.mantle-tmp" &&
            cat "$file.mantle-tmp" >"$file" && rm -f "$file.mantle-tmp"
        say "Removed Mantle's PATH entry from $file"
    fi
}

# Uninstall -----------------------------------------------------------------

if [ "$UNINSTALL" -eq 1 ]; then
    STEP="uninstalling"
    PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    if command -v uv >/dev/null 2>&1 && uv tool list 2>/dev/null | grep -q '^mantle-agent-cli'; then
        uv tool uninstall mantle-agent-cli
        say "Removed the Mantle CLI."
    else
        say "The Mantle CLI is not installed with uv; nothing to remove."
    fi
    for file in "$HOME/.zshrc" "$HOME/.bashrc" "$HOME/.bash_profile" "$HOME/.profile" "$HOME/.config/fish/conf.d/mantle.fish"; do
        remove_profile_block "$file"
    done
    if [ -f "$NODE_ROOT/.mantle-node" ] && ask "Remove the Node.js that Mantle installed in $NODE_ROOT?" n; then
        rm -rf -- "$NODE_ROOT"/node "$NODE_ROOT"/node-v* "$NODE_ROOT/.mantle-node"
        say "Removed Mantle's Node.js."
    fi
    MANTLE_DATA=${MANTLE_HOME:-$HOME/.mantle}
    if [ "$PURGE" -eq 1 ] && [ -d "$MANTLE_DATA" ]; then
        if ask "Delete $MANTLE_DATA (all local agents, sessions and provider logins)?" n; then
            rm -rf -- "$MANTLE_DATA"
            say "Deleted $MANTLE_DATA."
        else
            say "Kept $MANTLE_DATA."
        fi
    elif [ -d "$MANTLE_DATA" ]; then
        say "Your agents and logins remain in $MANTLE_DATA (delete it, or rerun with --uninstall --purge)."
    fi
    DONE=1
    exit 0
fi

# Preflight: report every problem at once -------------------------------------

STEP="checking prerequisites"
PROBLEMS=0
NODE_NEEDED=0

check_line() {
    # check_line STATUS LABEL DETAIL [FIX]
    case "$1" in
        ok) mark="✔" ;;
        warn) mark="!" ;;
        *) mark="✖" ;;
    esac
    printf '  %s %-10s %s\n' "$mark" "$2" "$3"
    if [ -n "${4:-}" ]; then printf '      → %s\n' "$4"; fi
}

say "Checking prerequisites"
check_line ok "System" "$OS $(uname -m)"

if command -v git >/dev/null 2>&1; then
    check_line ok "Git" "$(git --version 2>/dev/null | sed 's/^git version //')"
else
    check_line fail "Git" "Git is required for resource sources and remote installs" "$(package_hint git)"
    PROBLEMS=$((PROBLEMS + 1))
fi

NODE_VERSION=
if command -v node >/dev/null 2>&1; then
    NODE_VERSION=$(node --version 2>/dev/null || true)
fi
if [ -n "$NODE_VERSION" ] && version_at_least "$NODE_VERSION" "$NODE_MIN"; then
    check_line ok "Node.js" "$NODE_VERSION"
    if command -v npm >/dev/null 2>&1; then
        check_line ok "npm" "$(npm --version 2>/dev/null || printf found)"
    else
        check_line fail "npm" "npm is required with Node.js 22.20.0 or newer" "reinstall Node.js 22 including npm, or $(node_hint)"
        PROBLEMS=$((PROBLEMS + 1))
    fi
else
    NODE_NEEDED=1
    if [ -n "$NODE_VERSION" ]; then
        found="found ${NODE_VERSION#v}"
    else
        found="not found"
    fi
    if [ "$WITH_NODE" = yes ]; then
        check_line warn "Node.js" "Node.js $NODE_MIN or newer is required; $found. It will be installed for your user."
    else
        check_line fail "Node.js" "Node.js $NODE_MIN or newer is required; $found" "$(node_hint)"
    fi
fi

if command -v curl >/dev/null 2>&1 || command -v wget >/dev/null 2>&1; then
    check_line ok "Download" "$(command -v curl >/dev/null 2>&1 && printf curl || printf wget)"
elif ! command -v uv >/dev/null 2>&1 || [ "$NODE_NEEDED" -eq 1 ]; then
    check_line fail "Download" "curl or wget is required to download uv and Node.js" "$(package_hint curl)"
    PROBLEMS=$((PROBLEMS + 1))
fi

PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
export PATH
if command -v uv >/dev/null 2>&1; then
    check_line ok "uv" "$(uv --version 2>/dev/null | sed 's/^uv //')"
else
    check_line ok "uv" "not found; it will be installed for your user"
fi

if [ "$NODE_NEEDED" -eq 1 ]; then
    if [ "$WITH_NODE" = ask ] && [ "$PROBLEMS" -eq 0 ] && [ -n "$NODE_ARCH" ] &&
        ask "Install Node.js 22 LTS for your user only (into $NODE_ROOT, no sudo)?" y; then
        WITH_NODE=yes
    fi
    if [ "$WITH_NODE" != yes ]; then
        PROBLEMS=$((PROBLEMS + 1))
    elif [ -z "$NODE_ARCH" ]; then
        fail "automatic Node.js installation supports x64 and arm64 only; install Node.js $NODE_MIN or newer manually"
    fi
fi

if [ "$PROBLEMS" -gt 0 ]; then
    say ""
    if [ "$NODE_NEEDED" -eq 1 ] && [ "$WITH_NODE" != yes ]; then
        fail "Node.js $NODE_MIN or newer is required. Fix the items marked ✖ above (or rerun with --with-node), then run the installer again"
    fi
    fail "fix the $PROBLEMS item(s) marked ✖ above, then run the installer again"
fi

# Source --------------------------------------------------------------------

STEP="locating the Mantle source"
if [ -z "$SOURCE" ]; then
    if [ -f "$0" ]; then
        SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" 2>/dev/null && pwd || true)
        if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/pyproject.toml" ]; then
            SOURCE=$SCRIPT_DIR
        else
            fail "no source was found next to $0; pass --source PATH_OR_GIT_URL or set MANTLE_SOURCE"
        fi
    else
        # Piped (curl ... | sh): never guess from the current directory.
        SOURCE=$DEFAULT_SOURCE
    fi
fi

TEMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/mantle-install.XXXXXX")

case "$SOURCE" in
    git@*|ssh://*|http://*|https://*|git://*|*.git)
        STEP="downloading Mantle"
        CHECKOUT=$TEMP_DIR/source
        say ""
        say "Downloading Mantle from $SOURCE${REF:+ ($REF)}..."
        if [ -n "$REF" ]; then
            set -- --branch "$REF"
        else
            set --
        fi
        if ! GIT_TERMINAL_PROMPT=0 git clone --quiet --depth 1 "$@" -- "$SOURCE" "$CHECKOUT"; then
            fail "could not download $SOURCE${REF:+ at $REF}. Check the address and your network. For a private repository, clone it yourself and run ./install.sh from the checkout"
        fi
        ;;
    *)
        [ -z "$REF" ] || fail "--ref can only be used with a Git URL"
        [ -d "$SOURCE" ] || fail "source directory does not exist: $SOURCE"
        CHECKOUT=$(CDPATH= cd -- "$SOURCE" && pwd)
        ;;
esac

[ -f "$CHECKOUT/pyproject.toml" ] || fail "source does not contain pyproject.toml: $SOURCE"
grep -Eq '^[[:space:]]*name[[:space:]]*=[[:space:]]*"mantle-agent-cli"' "$CHECKOUT/pyproject.toml" ||
    fail "$SOURCE is not the Mantle CLI (its pyproject.toml does not declare mantle-agent-cli)"

# Node.js (optional, user-local) --------------------------------------------

if [ "$NODE_NEEDED" -eq 1 ] && [ "$WITH_NODE" = yes ]; then
    STEP="installing Node.js"
    say ""
    if [ -n "${MANTLE_NODE_VERSION:-}" ]; then
        NODE_INSTALL=${MANTLE_NODE_VERSION#v}
    else
        NODE_INSTALL=
        if download https://nodejs.org/dist/index.json "$TEMP_DIR/node-index.json" 2>/dev/null; then
            NODE_INSTALL=$(grep -o '"version":"v22\.[0-9]*\.[0-9]*"' "$TEMP_DIR/node-index.json" | head -n 1 | sed 's/.*"v//; s/"$//')
        fi
        NODE_INSTALL=${NODE_INSTALL:-$NODE_FALLBACK}
    fi
    version_at_least "$NODE_INSTALL" "$NODE_MIN" || fail "MANTLE_NODE_VERSION must be $NODE_MIN or newer"
    NODE_NAME=node-v$NODE_INSTALL-$NODE_OS-$NODE_ARCH
    NODE_URL=https://nodejs.org/dist/v$NODE_INSTALL
    say "Installing Node.js $NODE_INSTALL into $NODE_ROOT/$NODE_NAME..."
    download "$NODE_URL/$NODE_NAME.tar.gz" "$TEMP_DIR/$NODE_NAME.tar.gz"
    download "$NODE_URL/SHASUMS256.txt" "$TEMP_DIR/SHASUMS256.txt"
    expected=$(awk -v file="$NODE_NAME.tar.gz" '$2 == file {print $1}' "$TEMP_DIR/SHASUMS256.txt")
    actual=$(sha256_of "$TEMP_DIR/$NODE_NAME.tar.gz") || fail "sha256sum or shasum is required to verify the Node.js download"
    [ -n "$expected" ] && [ "$expected" = "$actual" ] || fail "the Node.js download failed checksum verification; nothing was installed"
    mkdir -p "$NODE_ROOT"
    rm -rf -- "${NODE_ROOT:?}/$NODE_NAME"
    tar --no-same-owner -xzf "$TEMP_DIR/$NODE_NAME.tar.gz" -C "$NODE_ROOT"
    ln -sfn "$NODE_NAME" "$NODE_ROOT/node"
    printf 'Installed by the Mantle installer.\n' >"$NODE_ROOT/.mantle-node"
    add_path "$NODE_ROOT/node/bin"
    hash -r 2>/dev/null || true
    version_at_least "$(node --version)" "$NODE_MIN" || fail "the installed Node.js is not usable"
    say "✔ Node.js $(node --version) with npm $(npm --version)"
fi

# uv and the CLI ------------------------------------------------------------

if ! command -v uv >/dev/null 2>&1; then
    STEP="installing uv"
    say ""
    say "Installing uv (Python tool manager) for the current user..."
    UV_INSTALLER=$TEMP_DIR/uv-install.sh
    download https://astral.sh/uv/install.sh "$UV_INSTALLER"
    # Mantle adds PATH entries itself, with consent, in one marked block.
    UV_NO_MODIFY_PATH=1 sh "$UV_INSTALLER" >"$TEMP_DIR/uv-install.log" 2>&1 || {
        cat "$TEMP_DIR/uv-install.log" >&2
        fail "uv could not be installed"
    }
fi

command -v uv >/dev/null 2>&1 || fail "uv was installed but is not available on PATH"

if [ -n "${UV_TOOL_BIN_DIR:-}" ]; then
    TOOL_BIN=$UV_TOOL_BIN_DIR
else
    TOOL_BIN=$(uv tool dir --bin 2>/dev/null) || fail "this uv is too old for tool installs; update it with: uv self update"
fi

STEP="installing the Mantle CLI"
say ""
say "Installing the Mantle CLI..."
uv tool install --force --reinstall-package mantle-agent-cli "$CHECKOUT"

MANTLE_BIN=$TOOL_BIN/mantle
[ -x "$MANTLE_BIN" ] || fail "installation completed but the mantle executable was not found at $MANTLE_BIN"
STEP="verifying the installation"
if ! "$MANTLE_BIN" --version >"$TEMP_DIR/version" 2>&1; then
    cat "$TEMP_DIR/version" >&2
    fail "mantle was installed at $MANTLE_BIN but does not start. Rerun the installer; if it still fails, report the output above"
fi
add_path "$TOOL_BIN"

say ""
say "✔ Mantle was installed successfully: $MANTLE_BIN $(cat "$TEMP_DIR/version")"

# PATH ----------------------------------------------------------------------

STEP="updating PATH"
if [ -n "$PATH_ADDITIONS" ]; then
    PROFILE=$(profile_file)
    case "$PROFILE" in
        *.fish) LINES=$(printf '%s\n' "$PATH_ADDITIONS" | sed 's/.*/fish_add_path "&"/') ;;
        *) LINES=$(printf '%s\n' "$PATH_ADDITIONS" | sed 's/.*/export PATH="&:$PATH"/') ;;
    esac
    LINES="$LINES
"
    if [ "$MODIFY_PATH" != no ] && ask "Add Mantle to PATH in $PROFILE?" y; then
        mkdir -p "$(dirname "$PROFILE")"
        remove_profile_block "$PROFILE" >/dev/null
        printf '# >>> mantle >>>\n%s# <<< mantle <<<\n' "$LINES" >>"$PROFILE"
        PROFILE_UPDATED=1
        say "Added to $PROFILE."
    else
        say "Add Mantle to PATH in your shell profile:"
        printf '%s' "$LINES" | sed 's/^/  /'
    fi
fi
DONE=1

# New shell -------------------------------------------------------------------

# The installer cannot change the PATH of the shell that started it. When it
# edited the profile, replace itself with a fresh interactive shell that reads
# it, so `mantle` works right away instead of after reopening the terminal.
start_new_shell() {
    if [ "$PROFILE_UPDATED" -eq 0 ]; then
        return
    fi
    shell=${SHELL:-}
    if [ "$ASSUME_YES" -eq 1 ] || ! interactive || [ -z "$shell" ] || ! command -v "$shell" >/dev/null 2>&1; then
        say ""
        say "To use mantle in this terminal, open a new terminal or run: . \"$PROFILE\""
        return
    fi
    case "$PROFILE" in
        */.bash_profile|*/.profile) set -- -l ;;  # Read only by login shells.
        *) set -- -i ;;
    esac
    say ""
    say "Starting a new $(basename "$shell") so mantle is on your PATH in this terminal."
    PATH=$ORIGINAL_PATH
    export PATH
    # exec skips the EXIT trap, so clean up first.
    rm -rf -- "$TEMP_DIR"
    TEMP_DIR=
    if [ -t 0 ]; then
        exec "$shell" "$@"
    else
        exec "$shell" "$@" </dev/tty
    fi
}

# Guided setup --------------------------------------------------------------

SETUP_RAN=0
if [ "$RUN_SETUP" -eq 1 ] && [ "$ASSUME_YES" -eq 0 ] && interactive; then
    say ""
    if ask "Run guided setup now (sign in to providers, optional Cloud, first agent)?" y; then
        SETUP_RAN=1
        if [ -t 0 ]; then
            "$MANTLE_BIN" setup || warn "setup did not finish; run it again any time with: mantle setup"
        else
            "$MANTLE_BIN" setup </dev/tty || warn "setup did not finish; run it again any time with: mantle setup"
        fi
    fi
fi
if [ "$SETUP_RAN" -eq 0 ]; then
    say ""
    say "Next: run \"mantle setup\" to sign in to a model provider and create your first agent."
    say "Check your installation at any time with: mantle doctor"
fi
start_new_shell
