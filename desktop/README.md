# Mantle for macOS

A native SwiftUI desktop app for Mantle, with local agents, live conversations, and an optional external registry. The original Python engine remains independently usable and unchanged.

## Open the app

Open the supplied `Mantle.app` on an Apple Silicon Mac. This local build targets macOS 14 or later and bundles Python, its dependencies, Node.js, and npm. It is ad-hoc signed for local use, not Developer ID signed or notarized for public distribution.

Mantle reads the existing `~/.mantle` library. It does not import sample agents, move your files, connect to a registry, or start a model run automatically. `MANTLE_HOME` remains supported.

1. Open **Connections** to save a provider key or use the existing account sign-in flow.
2. Create an agent. Set its purpose, provider, model, and instructions in Details; use YAML for the complete configuration.
3. Add skills and packages from the agent's tabs. A declared resource that is missing locally has an **Install** action.
4. Choose **Run Agent**, pick a project, and start a native conversation. Pi is downloaded on first use if an appropriate runtime is not already installed.
5. Use **Sessions** to read and resume saved conversations.

Git is required for Git-based resources. Docker Compose is required only if you want to host a registry on this Mac. Provider credentials and network access are required for actual model calls.

## Connect an external registry

Open **Registry**, enter your Mantle Cloud address, and connect. You can browse agents, inspect historical YAML, compare local changes, publish a configuration, pull an agent, or update an existing agent. Optional gateway tokens remain in memory and are restricted to the selected HTTPS origin. The original registry's unauthenticated API behavior is unchanged.

Only configuration YAML is published. Sessions and credential stores stay local; avoid putting credential values directly in YAML. Overwriting local configuration saves a backup, and stale-edit checks stop a save when the file changed elsewhere.

## Native and Terminal workflows

Normal agent conversations run in a separate native window, with streaming text, expandable tool results, extension prompts, queued instructions, and a stop control. Account OAuth and advanced Pi terminal features remain available through Terminal. See [FEATURES.md](FEATURES.md) for the precise feature map and remaining UI limitations.

Keyboard shortcuts: **⌘N** creates an agent, **⌘E** edits the selected agent, **⌘R** starts it, and **⌘⇧R** refreshes the library.

## Build and checks

See [BUILD.md](BUILD.md) for portable bundling, source versions, licenses, and signing status.

Verification for this implementation includes native compilation; 43 bridge, registry transport, and credential-origin tests; native RPC event checks; and a real-process offline conversation test. Native UI checks cover onboarding, agent navigation, YAML/Details switching, validation, provider layout, registry errors, and cancellation. Test libraries are separate from the user's library.

Live provider authentication, paid model execution, external registry writes, real resource installation, and Docker hosting have not been exercised. They use the original engine and require verification with the intended accounts and services before a public release.
