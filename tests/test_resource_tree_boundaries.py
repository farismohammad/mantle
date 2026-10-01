from pathlib import Path

import pytest

from mantle.errors import MantleError
from mantle.launcher import AgentLauncher
from mantle.skills import DirectSkillProvider


@pytest.mark.parametrize("validate", [AgentLauncher._validate_tree, DirectSkillProvider._validate_tree])
def test_internal_directory_link_cannot_hide_external_resource(tmp_path: Path, validate):
    agent = tmp_path / "agent"
    resources = agent / "skills"
    internal = agent / "cache/other"
    external = tmp_path / "another-agent"
    for directory in (resources, internal, external):
        directory.mkdir(parents=True)
    (external / "SKILL.md").write_text("Unrelated skill")
    (internal / "SKILL.md").symlink_to(external / "SKILL.md")
    (resources / "linked").symlink_to(internal, target_is_directory=True)
    with pytest.raises(MantleError, match="outside"):
        validate(resources, agent)


@pytest.mark.parametrize("validate", [AgentLauncher._validate_tree, DirectSkillProvider._validate_tree])
def test_internal_directory_link_cycles_do_not_recurse_forever(tmp_path: Path, validate):
    (tmp_path / "child").mkdir()
    (tmp_path / "child/back").symlink_to(tmp_path, target_is_directory=True)
    validate(tmp_path, tmp_path)
