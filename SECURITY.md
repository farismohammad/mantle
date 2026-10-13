# Security policy

Mantle is configuration and resource isolation for Pi, not an operating-system
sandbox. Pi and installed package extensions run with the permissions of the
user who starts them. Do not use Mantle as a boundary between mutually
untrusted users.

The optional Mantle Cloud API has no account, authentication, or ownership
layer. Anyone who can reach an instance can read, edit, and delete its agent
configurations. Keep it on a trusted network or add access control at a
trusted reverse proxy. Do not expose an unprotected instance to the public
Internet.

Provider logins from `mantle auth login` are stored once in
`MANTLE_HOME/auth/auth.json` (directory mode 700, file mode 600) and linked
into every agent, so every agent, and any extension or tool running in it, can
use them. Agents that must not have that access should run
`mantle auth isolate AGENT`. Mantle never uploads this file or `.env`, never
prints credential values, and does not implement OAuth itself; browser logins
are handled by Pi. `mantle auth logout` removes a login locally but does not
revoke it at the provider.

The installer runs without sudo. It verifies a Node.js download against the
official release's SHA-256 checksums, but downloads the uv installer script over
HTTPS without pinning it.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting for the affected
repository. If private reporting is unavailable, contact a repository
maintainer through GitHub and ask for a private reporting channel. Do not post
credentials, exploit details, or unpatched vulnerability reports in a public
issue.

Include the affected component and version, the impact, and steps to reproduce.
Remove credentials and personal data from logs or reproduction files.

Security fixes are published through the corresponding repository's release
notes. The CLI and Cloud have separate release histories.
