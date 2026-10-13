# agent.yaml

Each agent is defined by one `agent.yaml` in `~/.mantle/agents/NAME/`. Edit it with
`mantle agent edit NAME`, which checks it before saving, or check a file with
`mantle agent validate NAME|PATH`.

## Example

```yaml
mantle: "0.1"
agent:
  name: reviewer
  description: Reviews code changes
runtime:
  name: pi
  version: latest
model:
  provider: anthropic
  name: claude-opus-4-8
system_prompt: Focus on correctness and security.
resources:
  skills:
    - name: frontend-design
      source: https://github.com/anthropics/skills
  packages:
    - name: pi-lens
      source: npm:pi-lens@4.2.1
permissions:
  filesystem: all
  shell: all
secrets:
  - DOCS_MCP_TOKEN
```

For complete agents to copy from, see the [examples](../examples).

## Fields

| Field | Meaning | Default |
| --- | --- | --- |
| `mantle` | Format version: `"0.1"` | `"0.1"` |
| `agent.name` | Required. Lowercase letters, digits, `_` and `-` (up to 64) | |
| `agent.description` | What the agent is for | empty |
| `runtime.name` | `pi` | `pi` |
| `runtime.version` | A Pi version, or `latest` | `latest` |
| `model.provider`, `model.name` | The provider and model to use | `openai`, `gpt-5.6` |
| `system_prompt` | Text added to Pi's system prompt | none |
| `resources.skills` | Skills, each with a `name` and `source` | none |
| `resources.packages` | Pi packages, each with a `name` and `source` | none |
| `mcp.servers` | MCP servers (see below) | none |
| `permissions.filesystem` | `all`, `read` (no write/edit tools) or `none` (no file tools) | `all` |
| `permissions.shell` | `all` or `none` (no shell tools) | `all` |
| `secrets` | Names of environment variables the agent needs, never their values | none |

Quote version numbers. Unknown fields are errors.

## Repeatable installs

To get the same versions on every machine, pin them: an exact `runtime.version`,
`npm:package@version` for packages, and a commit in Git or GitHub URLs. `latest`,
version ranges and branches can change between installs.

## MCP servers

```yaml
mcp:
  servers:
    local-docs:
      command: npx
      args: [-y, "@example/docs-mcp"]
    hosted-docs:
      url: https://mcp.example.com/mcp
      auth: bearer
      bearer_token_env: DOCS_MCP_TOKEN
secrets:
  - DOCS_MCP_TOKEN
```

Each server uses either `command` (with optional `args`, `env`, `cwd`) or `url` (with
optional `headers`, `auth`, `bearer_token_env`). Other options include
`include_tools`, `exclude_tools`, `direct_tools` and `disabled`.

Never put a token in the YAML: it is uploaded by `mantle cloud push`. Save it with
`mantle auth secrets set DOCS_MCP_TOKEN` and list its name under `secrets`.

## Local model servers

For an OpenAI-compatible local server, add the provider to the agent's
`~/.mantle/agents/NAME/models.json`:

```json
{
  "providers": {
    "local": {
      "baseUrl": "http://127.0.0.1:11434/v1",
      "api": "openai-completions",
      "apiKey": "local-placeholder",
      "models": [{"id": "your-local-model"}]
    }
  }
}
```

Then set `model.provider: local` and `model.name: your-local-model`. `models.json`
stays on this machine, so set it up again on each one.

## What an agent can see

An agent loads only its own skills, packages and MCP servers, plus the project's
`AGENTS.md` and `CLAUDE.md`. Your global Pi, Claude or Codex settings and skills are
not loaded. Provider logins are shared by all agents unless you run
`mantle auth isolate NAME`.

**This is not a sandbox.** Agents, packages and MCP servers run with your user's
permissions and can reach any file or network you can. Only install code you trust.
