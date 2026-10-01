# CLI reference

Mantle creates reproducible, isolated Pi agents on your own machines. Each agent is declared by one editable `agent.yaml`; a self-hosted Mantle Cloud can retain that file and its immutable revision history.

For installation and the first-agent workflow, see the [README](../README.md).
The [agent format](agent-format.md) defines validation, permissions and migration.

## Local structure

```text
~/.mantle/
├── .env
├── config.json
├── runtimes/pi/<version>/
└── agents/
    └── frontend-web/
        ├── agent.yaml
        ├── skills/
        ├── packages/
        ├── context/
        ├── mantle-mcp.json     # generated from agent.yaml when MCP is enabled
        ├── npm/ and git/       # Pi-managed package files
        ├── auth.json           # Pi-managed OAuth/login state
        └── sessions/
```

Set `MANTLE_HOME` to use another local root. Runtimes are cached once by version and shared; agent configuration and resources remain isolated.

## Configure Cloud

Set your chosen registry URL:

```bash
export MANTLE_CLOUD_URL=http://localhost:3100
```

Or write `~/.mantle/config.json`:

```json
{
  "schema_version": 1,
  "cloud_url": "http://localhost:3100",
  "cloud_insecure": false
}
```

Cloud has no Mantle login or authentication layer. Keep it on a trusted private network or protect it at your reverse proxy.

If a development or private Cloud instance uses a self-signed certificate, TLS certificate
verification can be disabled for Mantle Cloud requests:

```bash
export MANTLE_CLOUD_INSECURE=true
```

Or set `"cloud_insecure": true` in `~/.mantle/config.json`. The environment variable,
when set, takes precedence over the config file.

This is insecure and should only be used for instances you trust. Certificate verification
remains enabled by default.

## Core workflow

Create locally; this does not contact Cloud:

```bash
mantle create frontend-web --description "Frontend development agent"
```

Add direct upstream resources; each command installs into only that agent and updates its local YAML:

```bash
mantle skills add https://github.com/anthropics/skills \
  --skill frontend-design --agent frontend-web
mantle add-package npm:pi-lens frontend-web
```

Run and test locally:

```bash
cd ~/projects/example
mantle run frontend-web
```

Publish the exact YAML explicitly:

```bash
mantle push frontend-web
```

An identical push reuses the current revision. Changed YAML creates the next immutable Cloud revision. No other command pushes automatically.

On another machine:

```bash
mantle install frontend-web
mantle run frontend-web
```

After a later push:

```bash
mantle update frontend-web
```

`update` treats Cloud as canonical for declarative state. It builds the desired runtime/resources in a staging directory, preserves local context, Pi authentication, logs, and sessions, then swaps only after reconstruction succeeds. Removed skills and packages disappear because the new environment is built from the latest YAML.

`mantle delete frontend-web` deletes only the local agent. Cloud agents are deleted through the private Cloud UI/API.

Deletion asks for confirmation; `--yes` skips the prompt. Use `--keep-sessions`
to retain sessions under `~/.mantle/retained-sessions/<name>/`. A damaged or
missing `agent.yaml` can still be removed with `delete` after confirmation.

Package sources must be `npm:` references or Git URLs. Local package references
are rejected because Pi would keep a reference outside the agent instead of
installing an isolated copy. Skill sources may also be local directories.

Pass Pi options after `--`:

```bash
mantle run frontend-web -- --print "Summarize this project"
mantle run frontend-web --session <session-id>
```

The launch directory is the current directory, even inside a larger repository.
`--project-root PATH` can select an ancestor directory explicitly. Only that
root's `AGENTS.md` and `CLAUDE.md` are included; `--no-project-context` omits them.
Session files must belong to the selected agent and project. Options that override
Mantle's resource paths, project trust, or tool restrictions are rejected.
`--dry-run` prepares the launch and prints JSON without starting Pi; preparation
can install a missing runtime/MCP adapter and create session or generated files.

## `agent.yaml`

```yaml
mantle: "0.1"
agent:
  name: frontend-web
  description: Frontend development agent
runtime:
  name: pi
  version: latest
model:
  provider: openai
  name: gpt-5.6
resources:
  skills:
    - name: frontend-design
      source: https://github.com/anthropics/skills
  packages:
    - name: pi-lens
      source: npm:pi-lens
mcp:
  servers:
    local-docs:
      command: npx
      args: [-y, "@example/docs-mcp"]
      direct_tools: [search]
    hosted-docs:
      url: https://mcp.example.com/mcp
      auth: bearer
      bearer_token_env: DOCS_MCP_TOKEN
permissions:
  filesystem: all
  shell: all
secrets:
  - OPENAI_API_KEY
  - DOCS_MCP_TOKEN
```

The CLI validates YAML syntax and this schema before use or upload. Cloud validates the same model through JSON Schema generated from the CLI’s Pydantic model (`scripts/export_schema.py`).

Duplicate YAML keys and duplicate resource names are errors. Use `--force` with
`skills add` or `add-package` to replace an existing named resource. Failed resource
installs and YAML saves restore the prior resource files and configuration.
Avoid editing or operating on the same agent from multiple processes at once;
Mantle does not yet coordinate concurrent writers.

## Secrets and Pi authentication

Put local values in `~/.mantle/.env`:

```dotenv
OPENAI_API_KEY=...
ANTHROPIC_API_KEY=...
```

Only names declared by the agent are loaded from this file or passed from the calling environment; operating-system/terminal variables are retained for normal execution. The `.env` file is never read by `push` and never uploaded. Missing declared values stop launch with an actionable error.

Pi owns OAuth and session authentication through the agent-local `auth.json`; Mantle does not implement OAuth.

## MCP servers

Declare MCP servers under `mcp.servers` in `agent.yaml`. Mantle automatically installs a pinned
`pi-mcp-adapter` into that agent, generates an agent-local bridge at launch, and passes the
configuration to the adapter as an isolated in-memory snapshot. The adapter does not merge
`.mcp.json`, `.pi/mcp.json`, user-global MCP files, host configurations, or another Mantle agent's
configuration.

Each server must use exactly one transport:

- `command` plus optional `args`, `env`, and `cwd` for a local stdio server.
- `url` plus optional `headers`, `auth`, and `bearer_token_env` for HTTP.

Supported controls include `lifecycle`, `idle_timeout`, `request_timeout_ms`, `protocol_version`,
`expose_resources`, `direct_tools`, `include_tools`, `exclude_tools`, `disabled`, and `debug`.
Secret values belong in `~/.mantle/.env`; reference them by environment-variable name and declare
the same name under the agent's `secrets` list. MCP server processes run as tools inside the Pi
session—they are not separate Mantle agents.

## Isolation boundary

Mantle disables Pi’s implicit global/project discovery and project trust, and explicitly passes only the selected agent’s skills, package resources, MCP servers, local context, and root-level project instructions. Unrelated global Pi, MCP, Codex, Claude, or agent skill directories are not loaded. Package manifests cannot reference resource paths outside their package.

Mantle is not an operating-system sandbox. Pi and package extensions execute with your user permissions. Review upstream code before installation.

Useful commands:

```bash
mantle list
mantle validate ~/.mantle/agents/frontend-web/agent.yaml
mantle run frontend-web --dry-run
mantle remove-skill frontend-design frontend-web
mantle remove-package pi-lens frontend-web
mantle runtime list
```

## Command reference and troubleshooting

| Commands | Purpose |
| --- | --- |
| `create`, `list [--json]`, `delete` | Manage local agents |
| `push`, `install`, `update` | Explicitly upload YAML or reconstruct from Cloud |
| `run` | Launch an agent in the current project |
| `skills add`, `remove-skill` | Install or remove one agent's skills |
| `add-package`, `remove-package` | Install or remove one agent's Pi packages |
| `validate PATH` | Validate an agent YAML file |
| `runtime install`, `runtime list`, `runtime remove` | Manage the shared Pi runtime cache |

Every command supports `--help`; `mantle --version` prints the CLI version.
Successful commands exit with 0, expected operational failures with 1, and bad
command-line syntax with 2. `run` forwards Pi's exit status. Errors go to stderr;
`list --json` and `run --dry-run` leave stdout suitable for JSON parsing.

- **Agent only exists in Cloud:** run `mantle install NAME`. There is no implicit
  install or separate `pull` command. `update` requires an existing local agent.
- **Cloud unavailable:** local create, resource management, validation, and run
  still work. Configure `MANTLE_CLOUD_URL` only when using Cloud workflows.
- **Invalid YAML/configuration:** the error identifies the file or schema field.
  Fix it and retry; malformed remote responses do not replace your local agent.
  An explicit `mantle update NAME` can restore a damaged local `agent.yaml` from
  Cloud while preserving context, credentials, and sessions.
- **Missing skill/package:** the launch error includes the command to reinstall
  that resource with `--force`. Use `update` only when you want Cloud's full YAML.
- **Missing runtime or npm:** install Node.js/npm and check `PATH`, then run
  `mantle runtime install VERSION`. Runtime removal refuses versions used by
  pinned agents or agents using the default `latest` runtime.
- **Failed install:** check the reported upstream error and retry. Resource
  installers time out after five minutes; failed reconstruction leaves the
  previous agent intact. A successfully installed shared runtime may remain cached.

For a release, test against your chosen Pi version and Cloud instance as well as
running the offline suite. Provider login and live model requests require your
own credentials; Mantle delegates those interactions to Pi.
