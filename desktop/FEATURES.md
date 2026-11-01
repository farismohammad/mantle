# Desktop feature coverage

This is a native macOS interface around the existing Mantle engine, with a native Pi conversation client. It carries over the principal local-agent and external-registry workflows. It is not a claim that every Pi terminal feature has an equivalent native control or that live provider integrations have been exercised.

The source baseline is [Mantle CLI](https://github.com/farismohammad/mantle/tree/50ae59ccfd69af8fd58cd15b4eb49fc7d4d512f6). The external registry contract was checked against [Mantle Web](https://github.com/farismohammad/mantle-web/tree/60a1fe1beaa551a2d609de132567cd1085dbbac7). Unpublished changes in another checkout are not included.

| Original workflow | Desktop location and behavior | Scope |
| --- | --- | --- |
| Create, list, inspect, edit, validate agents | Agents; native Details and complete YAML editors; validation uses the original schema | Native |
| Runtime, provider, model, prompt, permissions | Agent editor | Native |
| MCP server settings and secret declarations | Agent Connections summary and complete YAML editor | Preserved, with YAML editing rather than a separate form for every MCP field |
| Add and remove skills | Agent Skills tab; Git/local sources and optional skill selection | Native controls, original installers |
| Add and remove packages | Agent Packages tab; npm/Git sources and optional package name | Native controls, original installers |
| Agent execution and project context | Start Session; folder picker, optional first prompt, project-context switch, context-root override | Native conversation window |
| Resume and inspect sessions | Sessions; search, text history, original working directory, resume by ID or path | Native |
| Interactive Pi terminal | More Options when launching | Terminal remains available |
| API-key provider connections | Connections; add/replace keys and sign out | Native; “saved” is not a claim that a provider accepted a key |
| Provider OAuth/browser login | Connections → Sign in | Delegates to Mantle's existing interactive Terminal login flow |
| Per-agent authentication isolation | Agent action menu | Native switch between separate and shared credentials |
| Local secrets | Connections; names, add/replace/remove values | Native; existing Mantle local credential storage is retained |
| Runtime installation/default/removal | Runtimes; installed versions, users, default selection | Native controls, original runtime manager |
| External registry connection | Registry; custom address, health check, optional certificate setting | Native; registry remains optional |
| Registry browse, search, revisions, YAML | Registry library and configuration detail | Native |
| Publish, pull, compare, update | Agent Registry section and Registry library | Native; only agent YAML is published |
| Registry create/edit/delete | Registry editor and actions | Native; deletion and replacement require confirmation |
| Local Docker registry | Registry → local hosting controls | Start, stop, image version, port and recent logs; Docker Compose must already be available |
| Diagnostics | Health | Original engine checks, plus authenticated registry status when applicable |
| Export and local folders | Agent action menu | Native save panel and Finder |

## Native session protocol

The session window launches the bundled engine in Pi RPC mode and uses newline-delimited JSON over process pipes. It supports:

- Startup and saved-history synchronization before accepting a first prompt.
- Streaming assistant text and authoritative completed messages.
- Tool start, output, error and completion displays.
- Instructions queued while the agent works.
- Stopping through Pi's `abort` command, with queued instructions returned to the composer.
- Extension `select`, `confirm`, `input` and `editor` requests with matching response IDs.
- Extension notifications, status text, editor text, and text widgets.
- Session status, token totals and a diagnostics view.
- Orderly shutdown by closing stdin. The app does not forcibly terminate the agent process.

The client recognizes `agent_settled` and also queries `get_state` after `agent_end`, which accommodates versions that do not emit the newer settled event. This fallback was tested with an offline process fixture; it is not evidence of compatibility with every released Pi version.

Protocol references: [RPC mode](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/rpc.md), [commands](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/rpc-commands.md), [JSON events](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/json.md), and [extension UI](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/rpc-extension-ui.md).

## Deliberate boundaries and remaining gaps

The native conversation displays selectable text and expandable tool details. It does not currently provide an image attachment composer, rich image output rendering, or a full Markdown/code renderer. Image content is represented by an `[Image]` placeholder.

Pi extensions that depend on custom terminal components, terminal input interception, theme switching, or other TUI-only APIs require the Terminal launch option. The native client does not reproduce every Pi slash command, model-switching control, session-tree navigation, branch/fork control, export mode, or arbitrary forwarded command-line option. Those capabilities remain in the bundled engine/installed Pi terminal interface where supported.

MCP's full configuration schema is preserved, but advanced settings are edited in YAML. Editing resource declarations alone does not install resources; the Skills and Packages actions perform installation. Some advanced original CLI flags, such as copying credentials when isolating an agent or forcing a resource replacement, are not separate native controls.

Credential values use the existing Mantle credential and `.env` files, with private file permissions and protected backups. They have not been migrated to macOS Keychain. Registry proxy bearer tokens are held in memory for the current app session, restricted to HTTPS and the explicitly bound origin, and not forwarded across redirects. The original v0.1 registry still has no built-in authentication or atomic conditional-write API; revision checks reduce accidental stale edits but cannot prevent every simultaneous-writer race on that server.

The final local app bundles Python, its engine dependencies, and the official portable Node.js 22.23.4 distribution with npm. Git is still needed for Git-based resources, and running agents requires provider credentials. Pi itself is not bundled or pinned by this desktop build: it uses the user's installed/pinned runtime or Mantle's first-run download. Docker is only required for local registry hosting. See [BUILD.md](BUILD.md) for packaging and distribution status.

## Verification and limits

Verification covers native compilation, JSON bridge contracts, filesystem boundaries, backups, stale-edit handling, secret redaction, registry origin binding, isolated loopback HTTP registry interactions, and native RPC framing/lifecycle checks.

A separate offline process smoke test exercises the actual Swift `Process` and pipe path using `Tests/fixtures/rpc_engine.py`: startup/history, command arguments, fragmented Unicode output, tools, extension replies, an older-style completion sequence, abort, and graceful EOF shutdown. It does not import Mantle's runtime, call a model provider, run real tools, or modify a real project.

No real provider login, paid model request, real resource installation, user registry publication, or Docker deployment was performed as part of these checks. Those paths are implemented through the existing engine and need live end-to-end verification in the intended environment. The local build is not a signed/notarized public release, and declaring macOS 14 as the deployment target does not establish testing on every supported macOS version.
