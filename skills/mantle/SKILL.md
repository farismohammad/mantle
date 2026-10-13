---
name: mantle
description: Create, configure, run and share isolated Pi agents with the Mantle CLI (`mantle`). Use when the user mentions Mantle, runs `mantle` commands, or works with `~/.mantle` or an `agent.yaml` that starts with `mantle:`; or asks to set up a specialist agent, add skills, Pi packages, MCP servers or secrets to one, sign agents in to a model provider, hand a task to a Mantle agent, sync agents through Mantle Cloud, or fix a Mantle setup.
---

# Mantle

Mantle is a local-first CLI that creates and runs AI agents on the
[Pi](https://github.com/earendil-works/pi) coding-agent runtime. Each agent has its
own `agent.yaml`, skills, Pi packages, MCP servers and sessions. Pi runtimes and
model-provider logins are shared, so a new agent needs no new setup. Mantle Cloud,
an optional self-hosted registry, copies `agent.yaml` files between machines.

This skill assumes `mantle` is installed and on `PATH`, and covers driving it from a
non-interactive shell on a user's behalf. It matches Mantle 0.1.0 with Pi 1.x. Where
it and `mantle COMMAND --help` disagree, trust the help.

## Rules

1. **Assume there is no terminal.** Some commands prompt, open an editor or need a
   browser; use the non-interactive forms below. A confirmation nobody can answer
   aborts with exit 1.
2. **Keep secret values out of `agent.yaml`, command lines and your replies.** Pipe
   them in on stdin from an environment variable. `mantle cloud push` uploads
   `agent.yaml` as-is to a registry with no authentication.
3. **Ask the user before** deleting an agent, removing a login, pushing to Cloud,
   replacing an agent from Cloud, starting or stopping the Docker registry, removing
   a Pi runtime, or running `mantle setup`. Setup is interactive; with `--yes` it
   stores API keys from the shell as logins and creates an agent.
4. **Don't call it a sandbox.** `permissions` choose which Pi tools an agent gets.
   Agents, packages and MCP servers run with the user's OS permissions and network
   access, so install only code the user trusts.
5. **Read JSON, not tables.** Pass `--json` where offered. Progress and notes go to
   stderr, so stdout stays parseable; `MANTLE_QUIET=1` hides progress.
6. **Let Mantle own `~/.mantle`.** Hand-edit only an agent's `agent.yaml`,
   `context/*.md` and `models.json`, and never run `pi install` against an agent.
7. **Follow the fix Mantle prints.** Errors start with `Error:` on stderr and name
   the command that fixes them. When unsure what is wrong, run
   `mantle doctor --json`.

## Where things live

```text
~/.mantle/                     MANTLE_HOME; set that variable to use another directory
├── agents/NAME/
│   ├── agent.yaml             the agent's definition, and the only file Cloud stores
│   ├── context/*.md           extra local instructions for every run (never uploaded)
│   ├── models.json            custom and local model providers (never uploaded)
│   ├── skills/ npm/ git/      installed resources, managed by Mantle
│   ├── sessions/PROJECT-ID/   Pi sessions, one directory per project
│   ├── logs/mantle.jsonl      one line per run: time, runtime, project, exit status
│   └── auth.json              link to the shared logins (a private file once isolated)
├── auth/auth.json             shared provider logins (mode 600)
├── .env                       other secrets, such as MCP tokens (mode 600)
├── runtimes/pi/VERSION/       Pi installs shared by every agent
├── backups/NAME/              agent.yaml copies replaced by `mantle cloud update`
└── config.json                Cloud address and default Pi version
```

`mantle agent run NAME` starts Pi with its own discovery off and gives it only the
agent's model, installed skills, packages and MCP servers, and the tools its
`permissions` allow. Pi's system prompt gets `system_prompt`, then each
`context/*.md` in file-name order, then `AGENTS.md` and `CLAUDE.md` from the project
root (never from parent directories or subdirectories). Global Pi, Claude and Codex
settings, skills and extensions are never loaded.

The agent's environment holds only `PATH`, `HOME`, `USER`, `LOGNAME`, `SHELL`,
`TERM`, `COLORTERM`, `LANG`, `TZ`, temp-directory, `LC_*`, `XDG_*` and proxy
variables, plus the names listed under `secrets`. Anything else exported in the
shell, such as `OPENAI_API_KEY`, doesn't reach it.

## Check the setup

```bash
mantle --version
mantle doctor --json --offline
```

`doctor` checks Node.js 22.20+, npm, Git, the Mantle home, Pi runtimes, logins, and
each agent's credentials, secrets, skills and packages. Each item is
`{"group", "name", "status", "detail", "fix"}`. Resolve `fail` and `warn` items with
their `fix`; `info` items are optional. It exits 1 if any check failed. Drop
`--offline` to also check Mantle Cloud.

## Create and delete agents

```bash
mantle agent create reviewer --provider anthropic --description "Reviews diffs for correctness risks"
```

- Names are 1–64 lowercase letters, digits, `_` and `-`, not starting with `-`.
- `--provider` without `--model` picks the provider's default model (see
  `mantle auth providers`). With no `--provider`, Mantle uses `openai`, or, if
  you're signed in only to other providers, one of those.
- `--runtime-version 1.0.0` pins Pi; the default, `latest`, follows the shared
  default.
- Creating is local. The output ends with `Next:` commands; a `mantle auth login`
  line there means the agent has no usable credentials yet.
- A new agent has no prompt and every file and shell tool. Set `system_prompt` and
  `permissions` next ([Edit agent.yaml](#edit-agentyaml)).
- `mantle agent delete NAME --yes [--keep-sessions]` removes the local agent only;
  `--keep-sessions` moves its sessions to `~/.mantle/retained-sessions/NAME/`.

The Mantle repository's `examples/` folder has four starting points: `reviewer`,
`researcher`, `osint-investigator` and `frontend-developer`. Copy an example's
`system_prompt` and `permissions` into the new agent, then install its skills and
packages with `mantle skills add` and `mantle package add`. Copying `resources`
alone lists them without installing them.

## Edit agent.yaml

Bare `mantle agent edit NAME` opens `$VISUAL` or `$EDITOR` (else nano or vi) and
waits for a person. Use one of these instead:

```bash
# 1. Switch the model; comments and the rest of the file are kept
mantle agent edit reviewer --provider openai-codex        # that provider's default model
mantle agent edit reviewer --provider anthropic --model claude-opus-4-8
# 2. Replace the whole file, validated before saving (use a draft path without spaces)
VISUAL="cp /tmp/reviewer.yaml" mantle agent edit reviewer
# 3. After editing the file in place, validate it at once
mantle agent validate reviewer
```

With method 2, Mantle saves the draft only if it parses, fits the schema and
declares the same `agent.name`. Otherwise it prints each problem, exits 1 and leaves
`agent.yaml` untouched. If the draft adds skills or packages, the `Next:` lines give
the commands that install them.

- Add and remove skills and packages with `mantle skills` and `mantle package`, not
  by editing `resources`. A listed resource that isn't installed stops `agent run`
  with the command that reinstalls it.
- Mantle's own changes rewrite only the values they touch, so comments survive.
- If the agent is synced with Cloud, edits stay local until `mantle cloud push NAME`.

## agent.yaml reference

```yaml
mantle: "0.1"                    # required, quoted
agent:
  name: reviewer                 # required; must match the agent's directory name
  description: Reviews diffs     # up to 2,000 characters
runtime:
  name: pi
  version: "1.0.0"               # default latest; or a Pi version or npm tag
model:
  provider: anthropic            # default openai; see `mantle auth providers` or models.json
  name: claude-opus-4-8          # default gpt-5.6
system_prompt: |                 # added to Pi's system prompt; up to 100,000 characters
  Read the relevant files before making claims. Report each finding with its
  location, triggering condition and practical impact. Do not change files.
resources:                       # written by `mantle skills add` and `mantle package add`
  skills:
    - name: frontend-design      # a single directory name, unique
      source: https://github.com/anthropics/skills/tree/main/skills/frontend-design
  packages:
    - name: pi-web-access        # unique; one version per package
      source: npm:pi-web-access@0.38.0
mcp:
  servers: {}                    # see MCP servers
permissions:
  filesystem: read               # all (default), read or none
  shell: none                    # all (default) or none
secrets:                         # UPPER_SNAKE_CASE names, never values; no PI_ prefix
  - DOCS_MCP_TOKEN
```

Validation is strict. Unknown fields, duplicate keys and a `version` key on a skill
or package are errors; pin versions inside `source` instead. YAML 1.2 rules apply:
`yes`, `no`, `on` and `off` are strings, and unquoted `0.1` or `1.0` are numbers, so
quote `mantle` and every version. The file must be 1 MB or smaller.
`permissions.filesystem: workspace` is rejected: Mantle can't confine an agent to a
directory.

`filesystem: read` removes Pi's `edit` and `write` tools, `filesystem: none` also
removes `read`, `grep`, `find` and `ls`, and `shell: none` removes `bash` and
`powershell`. With `shell: all`, an agent can still change files through the shell,
so a read-only agent needs `filesystem: read` **and** `shell: none`.

For identical installs on every machine, pin an exact `runtime.version`,
`npm:package@version`, and a commit in Git and GitHub URLs.

## Skills

```bash
mantle skills add https://github.com/vercel-labs/agent-skills --skill web-design-guidelines -a reviewer
mantle skills add https://github.com/anthropics/skills/tree/main/skills/frontend-design -a frontend
mantle skills add ./skills/release-notes -a reviewer
mantle skills list -a reviewer                   # INSTALLED column: yes or missing
mantle skills remove web-design-guidelines -a reviewer
```

- `skills` and `package` commands name the agent with `-a NAME` (`--agent NAME`);
  every other command takes it as a positional argument.
- `npx skills add SOURCE --skill NAME` becomes
  `mantle skills add SOURCE --skill NAME -a AGENT`.
- Sources are Git repositories (name the skill with `--skill`), GitHub tree or blob
  URLs to one skill, and local directories (`--skill` only when the directory holds
  several). Without `--skill`, the name comes from the skill's `SKILL.md`.
- Start local paths with `./`, `../`, `~` or `/`. Those are recorded as absolute
  paths, while a bare `dir/name` can be read as GitHub `owner/repo`. Local paths
  won't exist on other machines, so use Git URLs for agents you share.
- Installing runs `npx skills@1.7.0`, which needs npm and network access and times
  out after five minutes. `--force` replaces or reinstalls an existing skill.
- Pi shows the model each skill's name and description and loads the full
  `SKILL.md` when a task matches. In an interactive session, `/skill:NAME` forces it.

## Pi packages

```bash
mantle package add npm:pi-web-access@0.38.0 -a researcher     # adds web search
mantle package add https://github.com/OWNER/REPO -a researcher
mantle package list -a researcher
mantle package remove pi-web-access -a researcher
```

- `pi install SOURCE` becomes `mantle package add SOURCE -a AGENT`.
- Sources are `npm:name[@version]`, `npm:@scope/name[@version]`, or a Git URL
  (`https://`, `ssh://`, `git@host:owner/repo`, `github:owner/repo`). Local paths
  and URLs that contain credentials are rejected.
- The name defaults to the npm package (without version) or the repository name;
  `--name` overrides it. To change versions, run
  `mantle package add npm:NAME@NEW_VERSION -a AGENT --force`.
- Packages install with the agent's own Pi runtime and load only for that agent.
- Don't add `pi-mcp-adapter`; Mantle manages it whenever `mcp.servers` is set.

## MCP servers

Declare servers in `agent.yaml`; there's no `mantle mcp` command.

```yaml
mcp:
  servers:
    local-docs:                  # letters, digits, ".", "_" and "-"
      command: npx
      args: [-y, "@example/docs-mcp"]
    hosted-docs:
      url: https://mcp.example.com/mcp
      auth: bearer
      bearer_token_env: DOCS_MCP_TOKEN
secrets:
  - DOCS_MCP_TOKEN
```

- Each server has exactly one transport: `command` (optional `args`, `env`, `cwd`)
  or an `http(s)` `url` (optional `headers`, `auth: bearer|oauth`,
  `bearer_token_env`).
- `bearer_token_env` requires `auth: bearer` and must also be listed under
  `secrets`. Store the value with `mantle auth secrets set`, then check the launch
  with `mantle agent run NAME --dry-run`.
- Never put a token in `headers` or `env`: both are uploaded with `agent.yaml`.
- Other options: `include_tools`, `exclude_tools`, `direct_tools` (`true` or a
  list), `disabled`, `lifecycle` (`lazy`, `eager`, `keep-alive`, `lazy-keep-alive`),
  `idle_timeout`, `request_timeout_ms`, `protocol_version` (`legacy`, `auto`,
  `2026-07-28`), `expose_resources` and `debug`.
- Mantle installs `pi-mcp-adapter` on the next run, `cloud pull` or `cloud update`,
  and ignores MCP servers configured elsewhere on the machine.

## Models, providers and logins

Provider logins live in one shared store that every agent uses unless it's
isolated.

```bash
mantle auth providers                                 # IDs, sign-in methods, key variables, default models
mantle auth list --json                               # logins and the agents that use each
mantle auth login openai --from-env                   # save this shell's OPENAI_API_KEY
printenv ANTHROPIC_API_KEY | mantle auth login anthropic --key-stdin
```

- Where it can, `auth login` checks the key with one read-only request and saves
  nothing if the provider rejects it. `--no-verify` skips the check.
- Without `--key-stdin`, `--from-env` or `--oauth`, `auth login` needs a terminal.
  Never type a key into a command. If it isn't in an environment variable, ask the
  user to run `mantle auth login PROVIDER` and paste it at the hidden prompt.
- Browser and subscription logins (`--oauth`) need the user at a terminal. A ChatGPT
  subscription is provider `openai-codex`, not `openai`, and signs in only that way.
- A login serves only its own provider. If an agent's provider has no login but
  another one does, switch with `mantle agent edit NAME --provider OTHER`;
  `agent show`, `doctor` and `agent run` suggest this.
- Any extension or tool in any agent can use the shared logins.
  `mantle auth isolate NAME [--copy-credentials]` gives an agent its own
  `auth.json` (the user signs in inside Pi with `/login`), and
  `mantle auth share NAME` undoes it.
- `mantle auth logout PROVIDER --yes` removes a login from this machine only; revoke
  a leaked key at the provider.
- To list model IDs the agent's logins can use:
  `mantle agent run NAME -- --offline --list-models [SEARCH]`.

For a local OpenAI-compatible server, add a provider to the agent's
`~/.mantle/agents/NAME/models.json`, then run
`mantle agent edit NAME --provider local --model your-local-model`:

```json
{
  "providers": {
    "local": {
      "baseUrl": "http://127.0.0.1:11434/v1", "api": "openai-completions",
      "apiKey": "local-placeholder", "models": [{"id": "your-local-model"}]
    }
  }
}
```

`models.json` is never uploaded, so set it up on each machine. A custom provider
that needs a key can store it with `mantle auth login local --key-stdin`.

## Secrets and other environment variables

An agent gets a variable only if its name is listed under `secrets`. Mantle reads
each listed name from the shell, then `~/.mantle/.env`. For a provider key variable
such as `OPENAI_API_KEY`, an agent on the shared logins falls back to that
provider's shared API key. A missing value stops the run with
`Missing required local secret(s): …` and the fix.

```bash
printenv DOCS_MCP_TOKEN | mantle auth secrets set DOCS_MCP_TOKEN --value-stdin
mantle auth secrets list            # names, where each value comes from, declaring agents
mantle auth secrets remove DOCS_MCP_TOKEN
```

- Values are one non-empty line, and names are `UPPER_SNAKE_CASE`. Without
  `--value-stdin`, `secrets set` prompts, so if the value isn't in an environment
  variable, ask the user to run `mantle auth secrets set NAME`.
- `secrets` can pass any variable, secret or not. List `NODE_EXTRA_CA_CERTS`, for
  example, to give the agent the shell's value.

## Run an agent and hand it a task

To pick an agent, compare `agent.description`, `system_prompt`, `permissions` and
`resources` in `mantle agent list --json`. Run it from the project it should work
on: `AGENTS.md` and `CLAUDE.md` are read from the current directory only, so use the
project root, or pass `--project-root PATH` from a subdirectory (Pi then works in
that root). `--no-project-context` leaves both files out.

```bash
cd /path/to/project
mantle agent run reviewer -- -p "Review src/auth for correctness risks." </dev/null
git diff main | mantle agent run reviewer -- -p "Review this change."     # stdin goes before the prompt
mantle agent run reviewer -- -p @docs/spec.md "Check the code against this spec." </dev/null
mantle agent run reviewer -- --mode json "Review src/auth." </dev/null > events.jsonl
```

- Everything after `--` goes to Pi. A Pi option before `--` fails with exit 2 and
  `No such option`.
- Give one-shot runs `-p` (`--print`) and, unless you're piping input, `</dev/null`.
  Pi reads a non-terminal stdin into the prompt, and an open pipe can stall it.
- Print mode writes only the final answer to stdout and exits nonzero if the run
  errors or is aborted. JSON mode streams a session header and events (text arrives
  as `message_update` deltas, finished messages as `message_end`). It can exit 0
  even when the model's turn failed, so check the events.
- Check `credentials` in `mantle agent show NAME --json` first. A missing login only
  prints a `Note:`, and Pi then fails at its first model request.
- Runs can take many minutes, and Mantle sets no time limit. If your shell tool has
  one, run in the background with stdout redirected to a file.
- Useful Pi options: `--thinking off|minimal|low|medium|high|xhigh|max`,
  `--no-session` (don't save), `-c` or `--continue` (continue the agent's latest
  session in this project), `--name NAME` and `--offline`.
- Resume a specific session with Mantle's own `--session ID_OR_FILE`. Sessions live
  in `~/.mantle/agents/NAME/sessions/PROJECT-ID/`, one directory per project root.
- `mantle agent run NAME --dry-run [-- PI_OPTIONS]` prints the Pi command, working
  directory and session directory as JSON without starting Pi. It still prepares
  the agent, downloading Pi or the MCP adapter if needed, and fails on a missing
  secret or resource, so it doubles as a preflight check.
- An interactive session (no `-p`) needs a terminal; give the user the command.
- If the agent exists only in Cloud, `agent run` says so and exits 1.
- The exit status is Pi's (a signal N becomes 128+N), and each run appends a line
  to the agent's `logs/mantle.jsonl`.

Mantle refuses Pi options that would bypass the agent's configuration, and its error
names what to use instead: `--session`, `--session-dir`, `--fork`, `--skill`,
`-e`/`--extension`, `--prompt-template`, `--theme`, `-t`/`--tools`,
`-xt`/`--exclude-tools`, `-a`/`--approve`, and the `install`, `remove`, `update`,
`list` and `config` subcommands.

## Pi runtimes

Agents on `runtime.version: latest` use the shared default Pi; the others use their
pinned version. Runtimes download on first use.

```bash
mantle runtime list              # versions, the default, and the agents using each
mantle runtime install           # newest Pi, made the default for every `latest` agent
mantle runtime install 1.0.0     # download; default only with --default or if none is set
mantle runtime use 1.0.0         # make it the default, downloading it if needed
mantle runtime remove 1.0.0      # refused while any agent uses it
```

`cloud pull` and `cloud update` of an agent on `latest` also install the newest Pi
and make it the default for every `latest` agent. Mention this when other agents
depend on the current version.

## Mantle Cloud

Mantle Cloud is a self-hosted registry of `agent.yaml` revisions with a web
interface. **It has no authentication:** anyone who can reach it can read, change
and delete every agent. Only `agent.yaml` travels, never logins, `.env`, `context/`,
`models.json`, installed resources or sessions.

```bash
mantle cloud status --json                         # configured, url, reachable, agents; exit 1 if unreachable
mantle cloud connect http://localhost:3100 --yes   # verify and save; without URL, probes :3100 and :3000
mantle cloud list --json                           # [{"name", "revision", "updated", "status"}]
mantle cloud diff reviewer                         # this machine's agent.yaml vs the latest revision
mantle cloud push reviewer                         # upload agent.yaml as a new revision
mantle cloud pull reviewer                         # install an agent that is only in Cloud
mantle cloud update reviewer                       # replace the local agent with the latest revision
```

| `status` in `cloud list` | Next step |
| --- | --- |
| `in sync` | None |
| `local changes not pushed` | `cloud diff`, then `cloud push` |
| `Cloud has a newer revision (N)` | `cloud update` |
| `both changed` | `cloud diff`, reconcile, then push or update |
| `only local (not pushed)` | `cloud push`, if the user wants it shared |
| `only in Cloud (mantle cloud pull NAME)` | `cloud pull` |
| `unknown (not synced from this machine yet)` | `cloud diff` |

- `push` validates and never merges. Your file becomes the newest revision even if
  Cloud had newer changes (older revisions stay in its history), so check
  `cloud list` or `cloud diff` first.
- `update` rebuilds skills and packages from the revision but keeps `context/`,
  sessions, logs, logins and `models.json`. If `agent.yaml` has unpushed changes, it
  shows a diff, asks, and backs the file up to `~/.mantle/backups/NAME/`. Without a
  terminal it exits 1 unless you pass `--yes`, which still makes the backup.
- `pull` refuses if the agent exists locally; use `update`. After a pull, sign in
  and set secrets as the `Next:` lines say, and copy over any `context/*.md` and
  `models.json` the agent relies on.
- `mantle agent delete` never touches Cloud, and the CLI can't delete Cloud agents.
- `MANTLE_CLOUD_URL` overrides the saved address. `MANTLE_CLOUD_INSECURE=1`, or
  `connect --insecure`, accepts a self-signed certificate.
- `mantle cloud start [--port N] [--version TAG] [--no-connect]` runs the registry
  with Docker Compose v2 on `127.0.0.1:3100`, keeps its data in a Docker volume, and
  connects the CLI. `cloud stop` keeps its agents; `cloud logs [-f] [-n N]` shows
  its output.

## Build a specialist agent

1. Check logins with `mantle auth list --json`, then choose a name, provider and
   model.
2. Run `mantle agent create NAME --provider P --description "…"`.
3. Write the complete `agent.yaml` to a draft, with a short, specific
   `system_prompt` and the narrowest `permissions` that still let it work. Save it
   with `VISUAL="cp DRAFT" mantle agent edit NAME`.
4. Install skills and packages with `mantle skills add` and `mantle package add`.
   Add MCP servers and `secrets` to the YAML, and store the secret values.
5. In `mantle agent show NAME --json`, `credentials` must not start with `missing`
   or `unusable`, and every entry in `secrets` must be `set`.
6. Smoke-test from the project root:
   `mantle agent run NAME -- -p "In one sentence, what can you do here?" </dev/null`.

A read-only reviewer, for example, is the [reference example](#agentyaml-reference)
without `resources`, `mcp` and `secrets`. Once it's saved, hand it work with
`git diff main | mantle agent run reviewer -- -p "Review this change."`.

## Troubleshooting

Most errors include their fix. These need more:

| Symptom | Fix |
| --- | --- |
| Pi fails at its first model request | If `credentials` in `agent show` starts with `missing`, run `mantle auth login PROVIDER` or switch providers |
| `Installation timed out` | Check the network, proxy (`HTTPS_PROXY`), CA settings (`NODE_EXTRA_CA_CERTS`) and free memory, then retry |
| `No such option` from `agent run` | Move the Pi options after `--` |
| `Agent "X" is not installed locally` | Check `mantle agent list`, then `mantle agent create X` or `mantle cloud pull X` |
| `Configuration declares agent "X", but "Y" was requested` | Make `agent.name` match the directory name |
| `… is not a valid Pi credentials file` | `mantle auth share NAME` sets the file aside |
| `Credentials are locked by another process` | Retry in a moment |

## Reference

Exit status: `0` for success; `1` for a Mantle error (`Error: …` on stderr), a
declined or unanswerable confirmation, `doctor` with a failed check, or
`cloud status` with an unreachable registry; `2` for a usage error, or
`mantle setup` without a terminal or `--yes`. `mantle agent run` returns Pi's
status.

| Variable | Effect |
| --- | --- |
| `MANTLE_HOME` | Data directory (default `~/.mantle`). An empty temporary directory lets you experiment without touching the user's agents; it has no logins, and Pi downloads again. |
| `MANTLE_CLOUD_URL` | Registry address; overrides the saved one |
| `MANTLE_CLOUD_INSECURE` | `1` skips TLS certificate checks for the registry |
| `MANTLE_INTERACTIVE` | `0` or `1` overrides terminal detection for prompts |
| `MANTLE_QUIET` | Any non-empty value hides progress lines |
| `VISUAL`, `EDITOR` | The editor `mantle agent edit` opens |

`mantle agent show NAME --json` returns `name`, `description`, `path`, `model`,
`credentials` (such as `shared login`, `isolated logins`, `via declared secret VAR`
or `missing → FIX`), `runtime`, `permissions`, `skills`, `packages`, `mcp_servers`,
`secrets` (each name mapped to `set` or `missing`) and `cloud`.
`mantle agent list --json` returns each agent's full `agent.yaml` as an object, and
names unreadable agents on stderr.
