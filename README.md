# Mantle

Mantle is a local-first CLI for creating and running isolated AI agents around
[Pi](https://github.com/earendil-works/pi). Each agent has an `agent.yaml`, its own
skills, packages, authentication and sessions. Pi runtimes are shared by version.

An unrelated global Pi skill or configuration should not change how your agent
behaves. Mantle disables implicit discovery and explicitly loads the selected
agent's resources plus the current project's `AGENTS.md` and `CLAUDE.md`.
This is **configuration isolation, not an operating-system sandbox**.

## Install

Supported: Linux and macOS, Python 3.11+ (obtained by the installer if needed),
Node.js **22.20.0 or later**, npm, Git, and curl or wget. Windows is not supported.
See [fresh-machine installation](docs/installation.md) for base dependencies.

From a downloaded source archive or checkout:

```sh
./install.sh
export PATH="$HOME/.local/bin:$PATH"
mantle --help
```

The installer installs `uv` if needed, then installs the CLI for your user.
It does not need `sudo`. Pi is installed automatically the first time it is needed.
An internet connection is required for initial dependency/resource downloads.
No Mantle Cloud account or registry is needed for local agents.

## Your first agent

```sh
mantle create reviewer --runtime-version 1.0.0
cp examples/reviewer/agent.yaml ~/.mantle/agents/reviewer/agent.yaml
```

The example uses OpenAI. Choose an available provider/model in `agent.yaml` if
yours differs. Put your own provider credential in `~/.mantle/.env`, for example:

```dotenv
OPENAI_API_KEY=your-key-here
```

Keep that file private (`chmod 600 ~/.mantle/.env`). The example declares
`OPENAI_API_KEY` under `secrets`; only declared variables are passed to the agent.
Alternatively use Pi's agent-local `/login` for a supported OAuth provider and
remove any API-key requirement from `secrets` in the YAML.

Using DeepSeek? A complete [DeepSeek example](examples/deepseek-reviewer/agent.yaml)
and [provider setup commands](docs/agent-format.md#deepseek-example) avoid having
to build the provider configuration yourself.

```sh
cd /path/to/your/project
mantle run reviewer
# Non-interactive:
mantle run reviewer -- --print "Summarize this project."
```

`mantle create` works offline. The first `run` installs the selected Pi version
into Mantle's cache. It uses the current directory as the project root; it does
not search parent directories for instructions. `--no-project-context` omits
project instructions. Provider requests need your own provider access.

## Skills and packages

```sh
mantle skills add https://github.com/vercel-labs/agent-skills \
  --skill web-design-guidelines --agent reviewer
mantle add-package npm:pi-lens@4.2.1 reviewer
```

These commands install resources for this agent and update its **local** YAML.
Use `--force` to replace an existing named resource. Remove resources with
`mantle remove-skill NAME reviewer` or `mantle remove-package NAME reviewer`.
Multiple skills are added by repeating the command. Pi packages can come from
npm or Git; local package references are unsupported. Local skill directories
work locally, but must be available wherever an agent is reconstructed.

Pin runtime versions, npm versions and Git commits for repeatable reconstruction.
`latest`, branches and unversioned sources are intentionally mutable. The
separate resource `version` field was never used and is now rejected; pin in
`source` instead. See [agent.yaml](docs/agent-format.md) for examples and migration.

## Optional registry

[Mantle Cloud](https://github.com/farismohammad/mantleagent.cloud) is a simple
self-hostable configuration registry. From its checkout, run:

```sh
docker compose up --build -d
export MANTLE_CLOUD_URL=http://localhost:3100
mantle push reviewer
# In another installation:
mantle install reviewer
# After explicitly pushing a newer configuration:
mantle update reviewer
```

`install` is the pull command for a new agent; `update` replaces an existing
agent's YAML/resources while preserving local context, authentication and
sessions. Back up local YAML before updating if you want to keep unpublished edits.
Only `push` publishes. Changed YAML creates a registry revision; an identical
push reuses the latest one. No revision bookkeeping is added to `agent.yaml`.
An agent that exists only remotely produces an installation hint when run.

Set `MANTLE_CLOUD_URL` to your chosen registry, or `cloud_url` in
`~/.mantle/config.json`. There is no required public service or hardcoded hosted
endpoint. See the registry's README for Docker, persistence and API details.

**The v0.1 registry has no authentication. Anyone who can reach it can read,
change or delete every agent.** Docker binds to loopback by default. Never store
secrets or sensitive configuration in a publicly exposed registry. Credential
values, including literal MCP headers/env values, must not be placed in shared YAML.

## Files, commands and limits

Everything Mantle manages lives under `~/.mantle` (override with `MANTLE_HOME`):
`agents/NAME/agent.yaml`, agent resource/state directories, shared `runtimes/pi`,
`config.json`, and a local `.env`. The registry receives only the YAML.

```sh
mantle list
mantle validate ~/.mantle/agents/reviewer/agent.yaml
mantle run reviewer --dry-run
mantle runtime list
mantle delete reviewer
```

Every command has `--help`. `--dry-run` prepares a launch and may install missing
runtime/MCP dependencies. Normal user errors use exit code 1; argument errors use
2; `run` forwards Pi's exit code. See [CLI reference](docs/cli-reference.md),
[agent format and isolation](docs/agent-format.md), and [development](CONTRIBUTING.md).

v0.1 intentionally has no accounts, automatic sync, hosted agent execution,
Windows support, or OS sandbox. Tool restrictions cannot constrain extension
code or malicious instructions. Trust packages before installing them. Avoid
concurrent commands that modify the same local agent. A registry volume supports
one application process. See [security](SECURITY.md) and [release notes](CHANGELOG.md).

Licensed under [Apache-2.0](LICENSE). Third-party components retain their own
licenses; see [dependency notes](docs/dependencies.md).
