# Mantle CLI

Mantle creates reproducible, isolated Pi agents on your own machines. Each agent is declared by one editable `agent.yaml`; a self-hosted Mantle Cloud can retain that file and its immutable revision history.

## Install

Requirements: Linux or macOS, Node.js/npm, and Git. The installer uses `uv`,
which will obtain a compatible Python 3.11+ interpreter when necessary.

```bash
./install.sh
mantle --help
```

Run that command from the repository root. To install on another machine from a
Git remote, copy `install.sh` there and provide the repository URL:

```bash
./install.sh --source https://github.com/farismohammad/mantle.git --ref main
```

The install is per-user and does not require `sudo`. If `uv` is missing, the
script installs it for the current user. Re-running the script upgrades or
repairs the existing Mantle CLI installation. `MANTLE_SOURCE` and `MANTLE_REF`
are environment-variable equivalents of the two options.

For development:

```bash
uv sync --extra test
uv run pytest
```

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

Set the private instance URL:

```bash
export MANTLE_CLOUD_URL=http://mantle.local
```

Or write `~/.mantle/config.json`:

```json
{
  "schema_version": 1,
  "cloud_url": "http://mantle.local"
}
```

Cloud has no Mantle login or authentication layer. Keep it on a trusted private network or protect it at your reverse proxy.

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

## Secrets and Pi authentication

Put local values in `~/.mantle/.env`:

```dotenv
OPENAI_API_KEY=...
ANTHROPIC_API_KEY=...
```

Only names declared by the agent are loaded from this file. The `.env` file is never read by `push` and never uploaded. Missing declared values stop launch with an actionable error.

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

Mantle disables Pi’s implicit global/project discovery and explicitly passes only the selected agent’s skills, package resources, MCP servers, local context, and root-level project instructions. Unrelated global Pi, MCP, Codex, Claude, or agent skill directories are not loaded.

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
