# Contributing

Mantle CLI and Mantle Cloud are separate repositories. Changes to the shared
agent configuration format may need matching updates in both repositories;
the CLI model and Cloud schema checks cover that contract.

## Development

Use Python 3.11 or newer and `uv` for the CLI. From this repository:

```bash
uv sync --locked --extra test
uv run pytest
uv build
sh -n install.sh
```

The CLI tests use temporary homes and controlled external commands. Do not use
real credentials or point tests at a personal Mantle home or registry.

CI also installs the supported Pi runtime into a temporary home and tests actual
extension discovery. Run that check locally with Node/npm available:

```sh
MANTLE_RUN_REAL_PI_TEST=1 uv run pytest tests/test_real_pi_isolation.py
```

For the complete two-installation test, start an isolated local registry on port
3317, then use a new temporary directory:

```sh
python3 scripts/release_acceptance.py --registry-url http://127.0.0.1:3317 \
  --work-dir /tmp/mantle-release-acceptance
```

This downloads real Pi/skill/package dependencies, installs Mantle independently
for two disposable users, and exercises execution, isolation, registry revisions
and update through the CLI. A local deterministic model fixture supplies replies;
it does not test access to a commercial model provider. Evidence is retained in
the work directory. Test agent names are unique and the script does not delete
existing registry data. Use only a disposable registry instance.

For Cloud development, use the separate
[Mantle Cloud repository](https://github.com/farismohammad/mantleagent.cloud)
and follow its README.

## Changes

- Keep CLI and Cloud validation aligned when changing `agent.yaml` fields.
- Add or update tests for behavior changes and user-visible error cases.
- Keep examples free of real credentials and private infrastructure details.
- Update the changelog and user documentation when commands, configuration,
  prerequisites, or observable behavior change.
- Do not include generated environments, package caches, runtime data, or
  registry data in commits.

Contributions are licensed under the repository's Apache-2.0 license. No
contributor license agreement is required.
