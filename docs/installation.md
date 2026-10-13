# Install Mantle

## Requirements

- Linux or macOS (Windows through WSL)
- Node.js 22.20+ with npm (the installer can install it for you)
- Git
- curl or wget
- uv and Python 3.11+ (installed automatically if missing)

## Install

From a source checkout:

```bash
./install.sh
```

To install a tagged release:

```bash
git clone --branch v0.1.0 https://github.com/farismohammad/mantle.git
cd mantle && ./install.sh
```

The installer checks every prerequisite and prints the fix for anything missing.
It needs no `sudo`. It installs everything for your user only, offers to add
`mantle` to your PATH, and then offers to run `mantle setup`.

| Option | Effect |
| --- | --- |
| `--with-node` / `--no-node` | Install Node.js without asking / never offer it |
| `--no-modify-path` | Print the PATH line instead of editing your shell profile |
| `--no-setup` | Don't start `mantle setup` |
| `-y`, `--yes` | Accept every default (for scripts and CI) |
| `--source PATH_OR_GIT_URL`, `--ref TAG` | Install from another checkout or Git tag |

## Check, upgrade and uninstall

```bash
mantle doctor                       # checks your setup and prints a fix for each problem
./install.sh                        # from a newer checkout: upgrades, keeps agents and logins
./install.sh --uninstall            # removes Mantle, keeps ~/.mantle
./install.sh --uninstall --purge    # also deletes ~/.mantle (agents, logins, sessions)
```

## Installing Node.js yourself

Any method that puts Node.js 22.20+ and npm on your PATH works, for example
`fnm install 22`, `nvm install 22` or `brew install node@22`. Add it to your shell
profile, not only the current shell, then run `mantle doctor` to confirm.
