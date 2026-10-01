# v0.1.0 release gap assessment

Audit date: 2026-10-02. This assessment precedes implementation. Existing
uncommitted changes in both component repositories are the starting baseline.

## Existing product and preserved decisions

The CLI and registry are separate Git repositories. The CLI is a small Python
Typer application with Pydantic models and YAML configuration. An agent owns
its resources, authentication, context and project sessions under
`MANTLE_HOME/agents/NAME`; Pi installations are shared by version under
`MANTLE_HOME/runtimes/pi`. Launch suppresses Pi discovery and passes resources
explicitly. Project context is deliberately limited to the selected root's
`AGENTS.md` and `CLAUDE.md`. This is configuration isolation, not an OS sandbox.

Existing commands cover local create/list/delete/run, validation, skills,
packages, runtime management, and explicit registry push/install/update.
Staged reconstruction and resource transactions already protect failed updates.
MCP integration exists and will be preserved, without expanding its scope.

The optional registry is a Next.js service with unauthenticated YAML APIs and
filesystem revisions. Identical pushes deduplicate; updates remain explicit.
Docker uses one unprivileged process and a persistent volume, bound to loopback.
One process per volume remains a documented limitation. No hosted deployment
or automatic publication is part of this release work.

## Baseline evidence

- CLI: 130 tests passed, predominantly using simulated Pi/npm/skills tools.
- Registry: 24 tests passed, plus lint and TypeScript checks.
- No existing clean-machine evidence meets this release's acceptance gate.
- Existing integration script shares a local skill source between installations
  and verifies launch preparation rather than model execution.
- Pattern-based scans of current files and reachable Git history reported no
  concrete credentials. This is evidence, not a guarantee of absence.

## Release blockers and workstreams

1. **Real isolation verification:** prove behavior with installed Pi, hostile
   global/project resources, separate agents, and separate user homes. Close
   session/context/resource symlink escape paths and ambient Pi override gaps.
2. **Truthful configuration:** `permissions.filesystem: workspace` currently has
   no enforcement. Resource `version` is accepted but ignored. Deeply nested
   YAML raises an uncaught recursion error; CLI coercion differs from registry
   validation. Reject unsupported behavior with field-specific migration errors
   or implement existing intended semantics; keep schema and registry aligned.
3. **Reproducibility:** mutable runtime/package/skill references are permitted.
   Define and test an exact-reference workflow and clearly distinguish mutable
   references. The upstream runtime now reports 1.0.0; installed development
   state uses 0.80.10. Verify the supported release version independently.
4. **Fresh installation:** validate the actual installer, Node requirements,
   PATH instructions, runtime provisioning, and Git/skill/package dependencies
   in two clean local environments before using the disposable VM.
5. **Registry reliability:** verify real Docker startup and complete CLI
   revisions/update lifecycle; harden malformed responses and credential-bearing
   endpoint errors. Keep revision state exclusively in the registry.
6. **Open-source packaging:** add the preferred Apache-2.0 license, accurate
   package metadata, contribution/security/release docs, usable example agents,
   and a registry CI workflow. Keep the two existing repository boundaries.
7. **Final release gate:** install from clean source on the dedicated bare VM,
   using public documentation only. Verify model execution, resources,
   isolation, registry revisions, and expected failures. No copied host state.

The primary agent integrates and verifies changes. Luna agents independently
audit isolation, registry behavior, and release hygiene. A final release report
must record actual results and distinguish unverified criteria.
