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
