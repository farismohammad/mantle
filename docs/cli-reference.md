# CLI reference

Run `mantle` on its own to list commands, and add `--help` to any command for its
options and examples. Most commands take the agent name first (`mantle agent run NAME`).
`skills` and `package` commands instead require `--agent NAME` (or `-a NAME`) to say
which agent to change.

## Commands

| Group | Commands |
| --- | --- |
| `mantle agent` | `create NAME [--provider P] [--model M]`, `list`, `show NAME`, `run NAME [-- PI_OPTIONS]`, `edit NAME [--provider P] [--model M]`, `delete NAME`, `validate NAME\|PATH` |
| `mantle skills` | `add SOURCE [--skill S] --agent AGENT`, `remove NAME --agent AGENT`, `list --agent AGENT` |
| `mantle package` | `add SOURCE [--name N] --agent AGENT`, `remove NAME --agent AGENT`, `list --agent AGENT` |
| `mantle auth` | `login [PROVIDER]`, `list`, `providers`, `logout PROVIDER`, `isolate AGENT`, `share AGENT`, `secrets set\|list\|remove` |
| `mantle cloud` | `start`, `stop`, `logs`, `connect [URL]`, `status`, `disconnect`, `list`, `push NAME`, `pull NAME`, `update NAME`, `diff NAME` |
| `mantle runtime` | `install [VERSION]`, `use VERSION`, `list`, `remove VERSION` |
| Other | `mantle setup`, `mantle doctor` |

`mantle create NAME` and `mantle run NAME` are shorter spellings of `mantle agent create`
and `mantle agent run`.

`agent list`, `agent show`, `auth list`, `cloud list`, `cloud status` and `doctor` accept `--json`.

## Sign in to model providers

```bash
mantle auth login                         # pick a provider and method interactively
mantle auth login anthropic               # choose an API key or a browser login
mantle auth login anthropic --oauth       # sign in with a subscription in your browser
mantle auth login deepseek --from-env     # save DEEPSEEK_API_KEY from your shell
mantle auth list                          # what you're signed in to
```

You sign in once and every agent uses that login. Each agent runs the provider in
its `agent.yaml`, so switch an agent to a provider you're signed in to with
`mantle agent edit NAME --provider PROVIDER`. A ChatGPT subscription signs in to
`openai-codex`, not `openai`.

To give one agent its own separate logins, run `mantle auth isolate NAME`
(`mantle auth share NAME` undoes it). Other secrets, such as MCP tokens, are set
with `mantle auth secrets set NAME`.

## Run an agent

```bash
cd /path/to/your/project
mantle agent run reviewer
mantle agent run reviewer -- --print "Summarize this project."   # Pi options go after --
mantle agent run reviewer --session SESSION_ID                    # resume a session
```

The agent sees the current directory's `AGENTS.md` and `CLAUDE.md`. Use
`--project-root PATH` to pick a parent directory instead, or `--no-project-context`
to leave them out.

## Skills and packages

Use the same skill URLs and Pi package names you normally would, and add
`--agent NAME` (or `-a NAME`) to choose the agent. The option is required.

| Instead of | Run |
| --- | --- |
| `npx skills add https://github.com/vercel-labs/skills --skill find-skills` | `mantle skills add https://github.com/vercel-labs/skills --skill find-skills -a reviewer` |
| `pi install npm:pi-web-access` | `mantle package add npm:pi-web-access -a reviewer` |

Skills and packages are installed only for that agent and recorded in its
`agent.yaml`. To see or remove them:

```bash
mantle skills list -a reviewer
mantle package list -a reviewer
mantle skills remove find-skills -a reviewer
mantle package remove pi-web-access -a reviewer
```

Skills come from Git repositories, GitHub URLs or local directories. Packages come
from npm (`npm:name@version`) or Git. Use `--force` to replace one that's already
installed.

## Runtimes

Agents use a pinned Pi version, or `latest` for the shared default.

```bash
mantle runtime install          # install the newest Pi and make it the default
mantle runtime use 1.0.0        # change the default
mantle runtime list             # installed versions and which agents use them
```

## Mantle Cloud

Mantle Cloud is an optional, self-hosted registry for sharing `agent.yaml` files
between machines.

```bash
mantle cloud start              # run it locally with Docker (http://localhost:3100) and connect
mantle cloud connect URL        # or connect to one that's already running
mantle cloud push reviewer      # publish an agent
mantle cloud pull reviewer      # on another machine
mantle cloud list               # what Cloud has, and whether yours is up to date
mantle cloud diff reviewer      # compare your copy with Cloud's
mantle cloud update reviewer    # take Cloud's newer version
mantle cloud stop               # stop the local server (agents are kept)
```

`cloud start` needs Docker with Compose v2. Only `agent.yaml` is uploaded: never
logins, secrets or sessions. `update` asks before replacing local edits and saves a
backup in `~/.mantle/backups/`.

**Cloud has no authentication.** Anyone who can reach it can read and change every
agent. Keep it on a trusted network or behind a reverse proxy.

## Files

Everything lives in `~/.mantle` (set `MANTLE_HOME` to change it):

```text
~/.mantle/
├── agents/NAME/        # agent.yaml, skills, packages and sessions
├── auth/auth.json      # shared provider logins
├── runtimes/pi/        # Pi versions, shared by all agents
├── backups/            # agent.yaml copies replaced by `cloud update`
├── config.json         # settings, such as the Cloud address
└── .env                # other secrets
```

## Troubleshooting

Start with `mantle doctor`. It checks Node.js, npm, Git, Pi, your logins, every
agent and Cloud, and prints a fix for each problem.

- **No credentials for a provider:** run `mantle auth login PROVIDER`, or switch the
  agent to a provider you're signed in to with `mantle agent edit NAME --provider PROVIDER`.
- **Invalid `agent.yaml`:** the error names the field. Fix it with `mantle agent edit NAME`.
- **Missing skill or package:** the error shows the command to reinstall it.
- **npm or Pi not found:** install Node.js 22.20+ (or rerun `./install.sh --with-node`),
  then run `mantle runtime install`.
- **Agent only exists in Cloud:** run `mantle cloud pull NAME`.
- **Cloud not answering:** run `mantle cloud status`, then `mantle cloud start` or
  `mantle cloud connect` with the right address. `mantle cloud logs` shows why a local
  server won't start.
