from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from mantle.agents import AgentStore
from mantle.errors import MantleError
from mantle.launcher import AgentLauncher
from mantle.mcp import MCP_CONFIG_FILENAME, MCP_EXTENSION_FILENAME
from mantle.models import AgentConfig, AgentDefinition, McpServerDefinition, RuntimeMetadata
from mantle.paths import MantlePaths
from mantle.runtimes import RuntimeManager


@contextmanager
def mock_launch(paths, mcp_enabled):
    config = AgentConfig(agent=AgentDefinition(name="developer"))
    if mcp_enabled:
        config.mcp.servers = {"docs": McpServerDefinition(url="https://mcp.example.test/mcp")}
    agent_root = paths.agent("developer")
    (agent_root / "npm/node_modules/pi-mcp-adapter").mkdir(parents=True)
    with (
        patch.object(AgentStore, "get", return_value=config),
        patch.object(RuntimeManager, "ensure", return_value=RuntimeMetadata(version="1.0.0")),
        patch.object(RuntimeManager, "executable", return_value=agent_root / "pi"),
        patch("mantle.launcher.normalize_models_config"),
        patch("mantle.launcher.write_mcp_bridge") as write_bridge,
        patch("mantle.launcher.remove_mcp_bridge") as remove_bridge,
    ):
        yield AgentLauncher(paths), write_bridge, remove_bridge


@pytest.mark.parametrize("mcp_enabled", [False, True])
@pytest.mark.parametrize("escape", ["directory", "bridge", "config", "directory_with_internal_leaf"])
def test_launch_rejects_mcp_symlink_escape_before_bridge_effects(tmp_path, mcp_enabled, escape):
    paths = MantlePaths(tmp_path / "mantle")
    agent_root = paths.agent("developer")
    agent_root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "untouched"
    sentinel.write_text("unrelated content")
    extensions = agent_root / "extensions"
    if escape in {"directory", "directory_with_internal_leaf"}:
        extensions.symlink_to(outside, target_is_directory=True)
        if escape == "directory_with_internal_leaf":
            internal = agent_root / "internal.ts"
            internal.write_text("internal content")
            (outside / MCP_EXTENSION_FILENAME).symlink_to(internal)
    elif escape == "bridge":
        extensions.mkdir()
        (extensions / MCP_EXTENSION_FILENAME).symlink_to(sentinel)
    else:
        (agent_root / MCP_CONFIG_FILENAME).symlink_to(sentinel)

    with mock_launch(paths, mcp_enabled) as (launcher, write_bridge, remove_bridge):
        with pytest.raises(MantleError, match="outside its isolated directory"):
            launcher.build("developer", current=tmp_path)
        write_bridge.assert_not_called()
        remove_bridge.assert_not_called()
    assert sentinel.read_text() == "unrelated content"


@pytest.mark.parametrize("mcp_enabled", [False, True])
def test_launch_allows_local_mcp_destinations(tmp_path, mcp_enabled):
    paths = MantlePaths(tmp_path / "mantle")
    with mock_launch(paths, mcp_enabled) as (launcher, write_bridge, remove_bridge):
        launcher.build("developer", current=tmp_path)
        assert write_bridge.call_count == int(mcp_enabled)
        assert remove_bridge.call_count == int(not mcp_enabled)
