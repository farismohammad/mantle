from __future__ import annotations

import json
from pathlib import Path

from mantle.models import AgentConfig, McpServerDefinition, ResourceDefinition
from mantle.storage import atomic_write_text, write_json


MCP_ADAPTER_VERSION = "2.29.0"
MCP_ADAPTER = ResourceDefinition(
    name="pi-mcp-adapter",
    source=f"npm:pi-mcp-adapter@{MCP_ADAPTER_VERSION}",
    version=MCP_ADAPTER_VERSION,
)
MCP_CONFIG_FILENAME = "mantle-mcp.json"
MCP_EXTENSION_FILENAME = "mantle-mcp.ts"


def adapter_server(server: McpServerDefinition) -> dict[str, object]:
    values = server.model_dump(exclude_none=True, exclude_defaults=True)
    aliases = {
        "bearer_token_env": "bearerTokenEnv",
        "idle_timeout": "idleTimeout",
        "request_timeout_ms": "requestTimeoutMs",
        "protocol_version": "protocolVersion",
        "expose_resources": "exposeResources",
        "direct_tools": "directTools",
        "include_tools": "includeTools",
        "exclude_tools": "excludeTools",
    }
    return {aliases.get(key, key): value for key, value in values.items()}


def adapter_config(config: AgentConfig) -> dict[str, object]:
    return {
        "settings": {"hostConfigDiscovery": "off"},
        "mcpServers": {name: adapter_server(server) for name, server in config.mcp.servers.items()},
    }


def write_mcp_bridge(agent_root: Path, config: AgentConfig, adapter_root: Path) -> Path:
    config_path = agent_root / MCP_CONFIG_FILENAME
    extension_path = agent_root / "extensions" / MCP_EXTENSION_FILENAME
    write_json(config_path, adapter_config(config))
    source = (
        'import { readFileSync } from "node:fs";\n'
        f"import {{ createMcpAdapter }} from {json.dumps(str(adapter_root / 'index.ts'))};\n\n"
        f"const config = JSON.parse(readFileSync(new URL('../{MCP_CONFIG_FILENAME}', import.meta.url), 'utf8'));\n"
        "export default createMcpAdapter({ config });\n"
    )
    atomic_write_text(extension_path, source, mode=0o600)
    return extension_path


def remove_mcp_bridge(agent_root: Path) -> None:
    (agent_root / MCP_CONFIG_FILENAME).unlink(missing_ok=True)
    (agent_root / "extensions" / MCP_EXTENSION_FILENAME).unlink(missing_ok=True)
