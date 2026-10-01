# agent.yaml and isolation

`agent.yaml` is the portable, public agent definition. Mantle validates it on
load, before resource installation, and before upload. The registry uses a
generated JSON Schema plus the same semantic constraints. Unknown fields are
errors. A file without `mantle` still uses the v0.1 defaults for compatibility;
new files should explicitly declare `mantle: "0.1"`.

See [reviewer](../examples/reviewer/agent.yaml) and
[local assistant](../examples/local-assistant/agent.yaml). Create the matching
agent first, then copy the example over its generated YAML. Example model names
must be adjusted to models your provider makes available.

| Field | Meaning and default |
| --- | --- |
| `mantle` | Format version, quoted string `"0.1"` |
| `agent.name` | Required, 1–64 lowercase letters/digits/underscores/hyphens; cannot begin with a hyphen |
| `agent.description` | Human-readable purpose; default empty |
| `runtime.name` | `pi` only |
| `runtime.version` | Exact npm Pi version or tag; default `latest` |
| `model.provider`, `model.name` | Pi provider/model identifiers; defaults `openai`, `gpt-5.6` |
| `system_prompt` | Optional text appended to Pi's system prompt |
| `resources.skills` | List of `{name, source}` skill declarations |
| `resources.packages` | List of `{name, source}` Pi package declarations |
| `mcp.servers` | Optional existing MCP adapter configuration, detailed below |
| `permissions.filesystem` | `all`, `read` or `none`; default `all` |
| `permissions.shell` | `all` or `none`; default `all` |
| `secrets` | Unique uppercase environment variable names, never their values |

Resource names must be unique within each kind. Skill names are single directory
names. Package aliases may be scoped npm names. Two versions/aliases of the same
npm package or Git repository cannot coexist within one agent's cache; use
separate agents. Revisions belong to the registry and are not fields in this file.

YAML uses 1.2 scalar rules: `true`/`false` are booleans, `on`/`off` are strings,
and dates stay strings. A string `"false"` is not a boolean. Quote versions and
identifiers. Duplicate mapping keys, merge keys, non-string keys, unsupported
tags/types and recursive/excessive aliases are rejected. Limits: 1 MB per file,
64 nesting levels, 100 aliases, and 10,000 expanded values.

## Repeatable resources

Pin references when publishing a configuration intended to reconstruct the same
versions. For example:

```yaml
runtime:
  name: pi
  version: "1.0.0"
resources:
  skills:
    - name: web-design-guidelines
      source: https://github.com/vercel-labs/agent-skills/tree/063bee94c3f4df8453406c830b0a7df0f2860278/skills/web-design-guidelines
  packages:
    - name: pi-lens
      source: npm:pi-lens@4.2.1
```

Skills use the pinned `skills` installer. Supported upstream Git repositories,
GitHub tree URLs and local directories retain the upstream installer's source
semantics. A GitHub tree URL can name an immutable commit. Package sources use
Pi's `npm:package@version` or Git URL with `@ref` syntax. Prefer immutable commit
IDs over Git branches/tags when possible. Local package references are rejected;
local skill paths must exist independently on each target machine.

`latest`, npm version ranges and unpinned upstream sources are mutable by choice.
They do not guarantee identical installs. Exact references also still depend on
upstream availability and the dependency locks shipped by upstream packages;
Mantle is not a package mirror or a fully hermetic build system.

## Migration from pre-release files

- Remove a resource's separate `version` field. It was metadata only and never
  controlled installation. Put the version into its `source`; conflicting
  references must be resolved deliberately. A null `version` remains accepted.
- Replace `permissions.filesystem: workspace` with an intentional choice of
  `all`, `read` or `none`. Workspace confinement was never implemented. Use an
  external OS sandbox when confinement is required.
- Replace quoted booleans and integers with correctly typed YAML values.
- YAML 1.1 booleans such as `yes` must be written as `true` in boolean fields.

Run `mantle validate PATH` to see the affected field before installing or pushing.

## Resource and context boundaries

Mantle points Pi at `MANTLE_HOME/agents/NAME` for configuration/authentication and
passes explicit agent resources. It disables Pi's implicit resource/context
discovery and project trust. Global Pi, Codex, Claude and `.agents` resource
directories do not become resources for this agent. Project `.pi` resources are
also disabled deliberately. Only the selected project root's `AGENTS.md` and
`CLAUDE.md` are included, along with the agent's own `context/*.md` files.

The default root is the invocation directory. `--project-root PATH` may select
an ancestor; `--no-project-context` omits its instruction files. Project content
is still accessible to tools according to the process user's filesystem access.
Sessions are scoped to both agent and project. An explicit session file must be
inside that agent's project session directory.

Mantle retains operating-system and terminal environment variables needed to
launch Pi and tools. Application-specific variables must be declared in
`secrets`. A declared variable comes from the invoking environment first, then
`MANTLE_HOME/.env`. Missing declared values stop launch. Pi-specific path and
discovery overrides are controlled by Mantle. Agent-local Pi authentication is
separate from declared environment variables.

This is not an OS sandbox. `read` removes Pi's write/edit tools; `none` removes
its built-in filesystem tools. `shell: none` removes built-in shell tools.
Extensions, MCP servers, other tools, and subprocesses still run with your user
permissions. They can access files/network outside the project. Trust their code.

## Local model providers

Custom provider definitions stay in the agent's `models.json`, never global Pi
configuration. For an OpenAI-compatible local server, adapt:

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

The placeholder is for a server that does not authenticate. Use the local
assistant example and a model actually available on your server. `models.json`
is local state preserved by registry updates; it is not uploaded. It must be
configured independently on another machine.

## DeepSeek example

From the source checkout or archive:

```sh
mantle create deepseek-reviewer --runtime-version 1.0.0
cp examples/deepseek-reviewer/agent.yaml ~/.mantle/agents/deepseek-reviewer/agent.yaml
```

Add `DEEPSEEK_API_KEY=your-key-here` to `~/.mantle/.env` using your editor and
keep the file private with `chmod 600 ~/.mantle/.env`. If you already have other
credentials there, retain those lines. The example declares the variable name;
no `models.json` is needed for Pi's built-in DeepSeek provider.

```sh
cd /path/to/your/project
mantle run deepseek-reviewer -- --print --thinking off "Read AGENTS.md and summarize it."
```

This example's `deepseek-v4-pro` model completed a real file-reading request
during release testing. Availability can change; DeepSeek's
[model-list API](https://api-docs.deepseek.com/api/list-models/) identifies the
models available to an account. Credentials are local values, not YAML fields.

## MCP

Existing MCP support installs a pinned adapter per agent and creates a bridge
from `mcp.servers`; ambient host MCP files are not merged. Each server specifies
either `command` (with optional `args`, `env`, `cwd`) or HTTP `url` (with optional
`headers`, `auth`, `bearer_token_env`). A bearer token variable must also appear
in `secrets`. See the [CLI reference](cli-reference.md#mcp-servers) for controls.

Literal headers or environment values are uploaded if placed in YAML. Put
credential values in local configuration and refer to their variable names.
Anyone who can reach an unauthenticated registry can read all current and
historical YAML; deleting a current secret value does not remove old revisions.
