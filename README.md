# Mantle

Mantle is a local-first CLI for creating and running AI agents, built around [Pi](https://github.com/earendil-works/pi).

I built it because I kept loading too many unrelated skills into one runtime. It made runs more expensive and the agent less focused.

With Mantle, each agent has its own configuration, skills, packages, and sessions. Pi runtimes and provider logins are shared, so creating another agent doesn't mean setting everything up again.

## Get started

Mantle runs on Linux and macOS, or Windows through WSL.

From a source checkout:

```sh
./install.sh
mantle setup
```

The installer checks dependencies and helps you resolve anything missing. No `sudo` needed.

Setup downloads Pi, signs you in to your model provider, and offers to create your first agent. Every step is optional and can be run later.

To create and run an agent yourself:

```sh
mantle agent create reviewer
cd /path/to/your/project
mantle agent run reviewer
```

New agents use the provider you're already signed in to. To add another login:

```sh
mantle auth login anthropic
```

Something not working? Run `mantle doctor`.

## Give each agent what it needs

Use the same skill URLs and Pi package names you normally would. Just install them through Mantle and add `-a` to choose the agent.

For example, a skill command from skills.sh:

```sh
npx skills add https://github.com/vercel-labs/skills --skill find-skills
```

Becomes:

```sh
mantle skills add https://github.com/vercel-labs/skills --skill find-skills -a agent_name
```

The same idea applies to Pi packages. Instead of:

```sh
pi install npm:pi-web-access
```

Run:

```sh
mantle package add npm:pi-web-access -a agent_name
```

Skills and packages are installed only for that agent and recorded in its `agent.yaml`. Replace `agent_name` with your agent's name.

You can edit or inspect its configuration with:

```sh
mantle agent edit agent_name
mantle agent show agent_name
```

Agent files live under `~/.mantle`. 

## What isolation means

Mantle disables implicit discovery. It loads the selected agent's resources plus the current project's `AGENTS.md` and `CLAUDE.md`, rather than picking up unrelated global Pi configuration.

**This is configuration isolation, not an operating-system sandbox.** 

## Optional registry

[Mantle Cloud](https://github.com/farismohammad/mantle-web) is a self-hostable registry with a web interface. Use it to publish agent configurations and reuse them across machines.

Connect to an existing registry and publish an agent:

```sh
mantle cloud connect
mantle cloud push reviewer
```

On another machine connected to the same registry:

```sh
mantle cloud pull reviewer
```

You can also start a local registry with `mantle cloud start`. It requires Docker Compose v2 and listens on localhost by default.

Only `agent.yaml` is published—not your logins, `.env`, installed resources, or sessions. Keep credential values out of shared YAML.

**The v0.1 registry has no authentication.** Anyone who can reach it can read, change, or delete agents. Keep it on a trusted network or behind an authenticating reverse proxy.

## Documentation

Run `mantle --help` to explore the commands.

[Installation](docs/installation.md) · [CLI reference](docs/cli-reference.md) · [Agent configuration](docs/agent-format.md) · [Security](SECURITY.md) · [Contributing](CONTRIBUTING.md)

## License

[Apache-2.0](LICENSE). Third-party components retain their own licenses.
