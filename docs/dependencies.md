# Dependencies and third-party licenses

Audit snapshot: 2026-10-01.

The CLI uses the Python dependencies in `pyproject.toml` and exact resolutions
in `uv.lock`. Cloud is a separate Node.js project with direct dependencies in
its `package.json` and resolutions in `pnpm-lock.yaml`.

This inventory records direct and transitive dependency metadata for Mantle
CLI 0.1.0 and Mantle Cloud 0.1.0. The Python report follows `uv.lock` for a
Linux x86_64, Python 3.12 environment. The Cloud report follows the lockfile's
runtime and development dependency graphs and checks package metadata present
in the local install. Platform-specific packages absent from that install are
listed as unreviewed. This is a source inventory, not legal advice or a
compatibility certification.

The CLI's `uv.lock` is used for development and test commands. `uv tool install`
resolves the compatible ranges from `pyproject.toml` at install time, so a
user's installed versions may differ from the lockfile versions below.

## Mantle CLI

| Dependency | Locked version | License metadata |
| --- | ---: | --- |
| httpx | 0.28.1 | BSD-3-Clause |
| pydantic | 2.13.4 | MIT |
| PyYAML | 6.0.3 | MIT |
| rich | 14.3.4 | MIT |
| typer | 0.27.0 | MIT |

The locked runtime graph contains 19 packages: 12 MIT, 3 BSD-3-Clause,
1 BSD-2-Clause, 1 MPL-2.0, 1 ISC, and 1 PSF-2.0. `certifi` 2026.6.17 is the
MPL-2.0 runtime dependency. Its wheel includes its license file.

The test extra adds six packages. Across the 25-package runtime and test graph,
the license metadata is 16 MIT, 3 BSD-3-Clause, 1 BSD-2-Clause, 1
MPL-2.0, 1 ISC, 1 PSF-2.0, 1 Apache-2.0, and 1 dual Apache-2.0 OR
BSD-2-Clause. `coverage` 7.15.2 is Apache-2.0. `annotated-types` 0.7.0,
`markdown-it-py` 4.2.0, and `mdurl` 0.1.2 omit a structured license field in
their installed metadata; their installed distributions include MIT license
text. The mdurl license file also retains an MIT notice for its Node URL code.

The lock also contains `colorama` 0.4.6 for Windows and `tomli` 2.4.1 for
conditional Python environments. They are not installed in the inspected
Linux/Python 3.12 environment. PyPI metadata gives colorama only a generic BSD
classifier and gives tomli no license field or license classifier, so those
conditional builds remain unverified here. Hatchling, the PEP 517 build
backend, is not pinned in `uv.lock` and is outside these counts.

## Mantle Cloud

| Direct dependency | Locked version | License metadata |
| --- | ---: | --- |
| @codemirror/lang-yaml | 6.1.2 | MIT |
| @codemirror/state | 6.5.2 | MIT |
| @codemirror/view | 6.38.2 | MIT |
| @uiw/react-codemirror | 4.24.1 | MIT |
| ajv | 8.20.0 | MIT |
| lucide-react | 0.542.0 | ISC |
| next | 15.5.24 | MIT |
| react | 19.1.4 | MIT |
| react-dom | 19.1.4 | MIT |
| yaml | 2.8.3 | ISC |

Direct development dependencies are `@eslint/eslintrc` 3.3.1 (MIT),
`@types/node` 22.18.0 (MIT), `@types/react` 19.1.12 (MIT),
`@types/react-dom` 19.1.9 (MIT), ESLint 9.34.0 (MIT),
`eslint-config-next` 15.5.22 (MIT), TypeScript 5.9.2 (Apache-2.0), and
Vitest 4.1.11 (MIT).

The locked Cloud graph contains 85 runtime package variants and 426 development
package variants, with overlap. The installed Linux x64 package tree provided
license metadata for 56 runtime and 327 development variants. The other 29
runtime and 99 development variants are optional builds for other operating
systems, architectures, or libc targets and were not installed for this
inspection. Among the installed runtime packages, metadata counts are 41 MIT,
5 Apache-2.0, 4 ISC, 2 BSD-3-Clause, 2 LGPL-3.0-or-later, 1 0BSD, and 1
CC-BY-4.0. The installed development packages include 1 MPL-2.0 package,
`axe-core` 4.12.1; it is not in the runtime dependency graph.

Next 15.5.24 has optional `sharp` 0.35.4 (Apache-2.0). Its Linux x64
`@img/sharp-libvips-linux-x64` and Alpine/musl x64
`@img/sharp-libvips-linuxmusl-x64` 1.3.3 binaries report LGPL-3.0-or-later.
The current standalone artifact and Alpine runtime image contain both x64
libvips artifacts. The image includes Apache-2.0, LGPL-3.0, and GPL-3.0 texts,
plus exact `versions.json` manifests for both variants. The manifests enumerate
bundled native components, but their per-component notices and applicable LGPL
source/relinking duties have not been fully audited or certified. Review the
current Cloud repository's `THIRD_PARTY_LICENSES.md` and
`licenses/THIRD_PARTY_NOTICES.md` before external image distribution. The
development-only `axe-core` MPL-2.0 package is excluded from the runtime image.

## Acceptance-only packages

The release acceptance workflow fetched these packages into local agent
environments; they are not dependencies of the CLI wheel or Cloud image:

| Package | Version | Registry manifest license | Declared runtime dependencies |
| --- | ---: | --- | ---: |
| @earendil-works/pi-coding-agent | 1.0.0 | MIT | 22 |
| skills | 1.7.0 | MIT | 2 |
| pi-lens | 4.2.1 | MIT | 6 plus 2 optional |

These packages install separately under the user's agent. Their transitive
dependency trees are not pinned by Mantle's `uv.lock` or Cloud's
`pnpm-lock.yaml`; the manifests' direct MIT declarations do not establish the
licenses of their transitive packages. Package managers install their own
license files alongside these resources.

The license identifiers above were read from installed package metadata or
the exact public npm registry manifests for the versions shown. Neither lock
file is a full legal review of every optional platform package or user-selected
resource. Check the license files in the exact build output before
redistributing it.
