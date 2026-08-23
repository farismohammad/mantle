from __future__ import annotations

import json
from pathlib import Path

from mantle.models import AgentConfig


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT.parent / "mantleagent.cloud" / "src" / "schema" / "agent.schema.json"


def main() -> None:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    schema = AgentConfig.model_json_schema()
    TARGET.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(TARGET)


if __name__ == "__main__":
    main()
