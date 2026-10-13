# Changelog

User-visible changes are recorded here. The CLI and Cloud repositories are
released independently.

## 0.1.0 (unreleased)

- Add a Python CLI for creating, validating, running, and managing isolated Pi
  agents on a local machine.
- Add per-agent skills, packages, local secrets, runtime management, and MCP
  configuration.
- Add explicit push, pull, and update workflows for the self-hosted Mantle
  Cloud registry.
- Add staged resource reconstruction and actionable validation and process
  errors.
- Add `mantle auth`: sign in to a model provider once (API key, verified before
  saving, or Pi's browser login) and use it in every agent without signing in
  again inside Pi. Existing per-agent logins are migrated; `mantle auth isolate`
  keeps an agent separate.
- Add `mantle setup` (guided first run) and `mantle doctor` (health checks with fixes).
- Group commands by what they act on: `mantle agent create|list|show|run|edit|delete|validate`,
  `mantle cloud push|pull|update|diff` and `mantle auth secrets`. `mantle --help`
  lists only the groups; `mantle GROUP --help` lists a group's commands.
  `mantle create` and `mantle run` are shorter spellings of `mantle agent create`
  and `mantle agent run`.
- Rename `mantle cloud install` to `mantle cloud pull`.
- Add four short examples: `reviewer`, `researcher`, `osint-investigator` and
  `frontend-developer`.
- Browser logins (`mantle auth login --oauth`, and in `mantle setup`) run
  directly in the terminal through Pi's login code instead of opening Pi and
  asking you to type `/login`.
- The installer opens a new shell after adding Mantle to PATH, so `mantle` works
  in the same terminal without reopening it.
- Add `mantle cloud connect|status|list|disconnect`: discover, verify and save the
  registry address, with specific errors for wrong ports, wrong services, TLS
  mismatches and DNS failures. A non-Mantle 404 is no longer reported as a missing agent.
- Add `mantle cloud start|stop|logs`: run Mantle Cloud on this machine with
  Docker (the published `ghcr.io/farismohammad/mantle-web` image), connect
  the CLI to it, and print the URL to open. Data is kept in a Docker volume.
  `mantle setup` offers it when no registry is running, and `cloud status` and
  `doctor` report the local server's state.
- `mantle cloud update` shows a diff and asks before discarding unpushed local edits,
  keeps a backup, and summarizes what changed. Add `mantle cloud diff`.
- Add `mantle agent show`, `mantle agent edit` (validated), `mantle auth secrets`,
  `agent create --provider/--model`, and next-step hints.
- Reorganize commands: `mantle skills|package add|remove|list … --agent AGENT`
  (`-a` for short). Help is grouped by task.
- `mantle runtime install` (latest) now moves agents on `latest` to the new Pi;
  add `runtime use`, and show which agents use each runtime.
- Long downloads and installs now show progress on stderr.
- `mantle agent create` without `--provider` uses a provider you are signed in to
  when you are not signed in to OpenAI. `mantle agent edit NAME --provider P
  [--model M]` switches an agent's model without an editor, and `agent show`,
  `doctor`, `agent run` and `auth login` suggest it when an agent's provider has
  no login but another one does (a ChatGPT subscription is `openai-codex`, not `openai`).
- Commands that change `agent.yaml` keep its comments and formatting.
- `mantle skills add` without `--skill` uses the `name` in the skill's `SKILL.md`
  for tree URLs and local directories, and records local paths as absolute paths.
- One damaged `agent.yaml` no longer stops `agent list`, `auth list`,
  `auth secrets list` or the runtime commands; `runtime use` no longer reports a
  failure after changing the default.
- `agent validate NAME` rejects a file that declares another agent; `agent show`
  and `doctor` report an unreadable agent `auth.json` (`auth share` sets it aside)
  and custom-provider credentials accurately; `doctor` checks packages.
- A cancelled or failed browser login exits with 1, also when an older login is kept.
- `runtime install --no-default` is honoured; empty `--provider`/`--model` values
  and out-of-range provider numbers are rejected; bare command groups exit with 0.
- An isolated agent no longer receives a shared login's API key for a provider key
  variable it lists under `secrets`, such as `OPENAI_API_KEY`: its declared secrets
  come only from the shell and `MANTLE_HOME/.env`. `agent run`, `agent show`,
  `doctor` and `auth secrets list` agree, and for an isolated agent they suggest
  `mantle auth secrets set NAME` or signing in inside Pi instead of `mantle auth login`.
- Installer: one preflight report with platform-specific fixes, optional
  checksum-verified user-local Node.js (`--with-node`), consent-based PATH setup,
  source verification, `--uninstall`, and a hand-off to `mantle setup`.
