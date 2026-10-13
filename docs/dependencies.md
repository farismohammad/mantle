# Dependencies and licenses

Mantle is licensed under Apache-2.0. Its dependencies keep their own licenses.
Versions below are from the lockfiles as of 2026-10-01. This is not legal advice.

## Mantle CLI

| Dependency | Version | License |
| --- | ---: | --- |
| httpx | 0.28.1 | BSD-3-Clause |
| pydantic | 2.13.4 | MIT |
| PyYAML | 6.0.3 | MIT |
| rich | 14.3.4 | MIT |
| typer | 0.27.0 | MIT |

All other packages they pull in are MIT, BSD, ISC or PSF, except `certifi`
(MPL-2.0). `uv tool install` resolves versions at install time, so yours may differ
from `uv.lock`.

## Mantle Cloud

| Dependency | Version | License |
| --- | ---: | --- |
| next | 15.5.24 | MIT |
| react, react-dom | 19.1.4 | MIT |
| @codemirror/lang-yaml, state, view | 6.x | MIT |
| @uiw/react-codemirror | 4.24.1 | MIT |
| ajv | 8.20.0 | MIT |
| lucide-react | 0.542.0 | ISC |
| yaml | 2.8.3 | ISC |

The Cloud Docker image includes `libvips` binaries (through Next's optional `sharp`
dependency), which are LGPL-3.0-or-later. Before redistributing the image, review
`THIRD_PARTY_LICENSES.md` and `licenses/THIRD_PARTY_NOTICES.md` in the
[Cloud repository](https://github.com/farismohammad/mantle-web).

## Installed per agent

Pi (`@earendil-works/pi-coding-agent`) and any skills or packages you add are
downloaded into each agent and carry their own licenses. Mantle doesn't pin their
dependencies.
