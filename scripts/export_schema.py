from __future__ import annotations

import argparse
import json
from pathlib import Path

from mantle.models import AgentConfig


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT.parent / "mantleagent.cloud" / "src" / "schema" / "agent.schema.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Export the public agent.yaml JSON Schema.")
    parser.add_argument("--output", type=Path, default=TARGET)
    target = parser.parse_args().output
    target.parent.mkdir(parents=True, exist_ok=True)
    schema = AgentConfig.model_json_schema()
    target.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(target)


if __name__ == "__main__":
    main()
