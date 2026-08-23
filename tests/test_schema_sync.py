from __future__ import annotations

import json
from pathlib import Path

from mantle.models import AgentConfig


def test_cloud_schema_is_generated_from_cli_pydantic_model():
    cloud_schema = Path(__file__).resolve().parents[2] / "mantleagent.cloud/src/schema/agent.schema.json"
    assert json.loads(cloud_schema.read_text()) == AgentConfig.model_json_schema()
