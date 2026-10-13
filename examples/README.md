# Examples

Each folder has an `agent.yaml` that shows one thing Mantle can do. They are
starting points, not finished agents: change the prompt, model and permissions to
suit you.

| Example | What it shows |
| --- | --- |
| [reviewer](reviewer/agent.yaml) | The smallest useful agent: a prompt and read-only permissions |
| [researcher](researcher/agent.yaml) | A Pi package (`pi-web-access`) that adds web search |
| [osint-investigator](osint-investigator/agent.yaml) | A skill and a package together, with a short prompt that sets limits |
| [frontend-developer](frontend-developer/agent.yaml) | A skill (`frontend-design`) with write and shell access |

To use one, create an agent and copy in the parts of the example you want:

```sh
mantle agent create researcher
mantle agent edit researcher
```

Add the example's skills and packages with `mantle skills add` and
`mantle package add` so they are installed, not just listed. For the researcher:

```sh
mantle package add npm:pi-web-access@0.38.0 -a researcher
cd /path/to/your/project
mantle agent run researcher
```

The OSINT skill only runs when you ask for it. Inside the agent, type
`/skill:x-ray-a-company` followed by the company you want to look into.
