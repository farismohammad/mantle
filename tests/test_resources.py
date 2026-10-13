from __future__ import annotations

import subprocess
from pathlib import Path

import yaml
import pytest

from mantle.agents import AgentStore
from mantle.models import RuntimeMetadata
from mantle.packages import PackageManager, package_name_from_source
from mantle.paths import MantlePaths
from mantle.skills import SkillManager
from mantle.storage import write_json


class FakeSkillProvider:
    def install(self, source, skill, destination):
        assert source == "https://github.com/example/skills"
        destination.mkdir(parents=True)
        (destination / "SKILL.md").write_text(f"---\nname: {skill}\n---\n")


class FakePi:
    def __init__(self):
        self.commands = []

    def __call__(self, command, **kwargs):
        self.commands.append((command, kwargs["env"]))
        return subprocess.CompletedProcess(command, 0, "", "")


def install_fake_runtime(paths: MantlePaths, version="1.0.0"):
    executable = paths.runtimes / version / "node_modules/.bin/pi"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n")
    write_json(paths.runtimes / version / "metadata.json", RuntimeMetadata(version=version).model_dump(mode="json"))


def test_skill_add_and_remove_change_local_yaml_without_network(mantle_home):
    paths = MantlePaths.discover()
    AgentStore(paths).create("developer")
    manager = SkillManager(paths, provider=FakeSkillProvider())
    manager.add("https://github.com/example/skills", "frontend-design", "developer")
    assert (paths.agent("developer") / "skills/frontend-design/SKILL.md").is_file()
    document = yaml.safe_load(paths.yaml("developer").read_text())
    assert document["resources"]["skills"] == [{"name": "frontend-design", "source": "https://github.com/example/skills"}]
    assert not (mantle_home / "cloud").exists()
    manager.remove("frontend-design", "developer")
    assert AgentStore(paths).get("developer").resources.skills == []


def test_package_add_and_remove_delegate_to_pi_and_change_yaml(mantle_home):
    paths = MantlePaths.discover()
    install_fake_runtime(paths)
    AgentStore(paths).create("developer", runtime_version="1.0.0")
    pi = FakePi()
    manager = PackageManager(paths, runner=pi)
    record = manager.add("npm:pi-lens", "developer")
    assert record.name == "pi-lens"
    assert pi.commands[-1][0][-2:] == ["install", "npm:pi-lens"]
    assert pi.commands[-1][1]["PI_CODING_AGENT_DIR"] == str(paths.agent("developer"))
    assert AgentStore(paths).get("developer").resources.packages[0].source == "npm:pi-lens"
    manager.remove("pi-lens", "developer")
    assert pi.commands[-1][0][-2:] == ["remove", "npm:pi-lens"]
    assert AgentStore(paths).get("developer").resources.packages == []


def test_package_operations_ignore_host_pi_overrides(mantle_home, monkeypatch):
    paths = MantlePaths.discover()
    install_fake_runtime(paths)
    AgentStore(paths).create("developer", runtime_version="1.0.0")
    monkeypatch.setenv("PI_PACKAGE_DIR", "/host/pi-package")
    monkeypatch.setenv("PI_CODING_AGENT_DIR", "/host/pi-agent")
    monkeypatch.setenv("PI_HOST_OVERRIDE", "host-value")
    monkeypatch.setenv("PRIVATE_REGISTRY_TOKEN", "installer-token")
    pi = FakePi()

    PackageManager(paths, runner=pi).add("npm:pi-lens", "developer")

    environment = pi.commands[-1][1]
    assert environment["PI_CODING_AGENT_DIR"] == str(paths.agent("developer"))
    assert environment["PI_TELEMETRY"] == "0"
    assert environment["PI_SKIP_VERSION_CHECK"] == "1"
    assert "PI_PACKAGE_DIR" not in environment
    assert "PI_HOST_OVERRIDE" not in environment
    assert environment["PRIVATE_REGISTRY_TOKEN"] == "installer-token"


def test_skill_sources_cannot_be_mistaken_for_cli_options(mantle_home):
    paths = MantlePaths.discover()
    AgentStore(paths).create("developer")
    manager = SkillManager(paths, provider=FakeSkillProvider())

    from mantle.errors import MantleError

    with pytest.raises(MantleError, match="must not begin with an option marker"):
        manager.add("--help", "frontend-design", "developer")

    assert AgentStore(paths).get("developer").resources.skills == []


def test_skill_installer_strips_pi_overrides_but_keeps_upstream_credentials(
    tmp_path, monkeypatch
):
    from mantle.skills import DirectSkillProvider

    captured = {}

    def fake_runner(command, **kwargs):
        captured["command"] = command
        captured["environment"] = kwargs["env"]
        installed = kwargs["cwd"] / ".pi/skills/frontend-design"
        installed.mkdir(parents=True)
        (installed / "SKILL.md").write_text("skill")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("mantle.skills.run_installer", fake_runner)
    monkeypatch.setenv("PI_PACKAGE_DIR", "/host/pi-package")
    monkeypatch.setenv("PI_HOST_OVERRIDE", "host-value")
    monkeypatch.setenv("GIT_AUTH_TOKEN", "upstream-token")
    destination = tmp_path / "agent/skills/frontend-design"

    DirectSkillProvider().install(
        "https://github.com/example/skills", "frontend-design", destination
    )

    assert "skills@1.7.0" in captured["command"]
    assert "PI_PACKAGE_DIR" not in captured["environment"]
    assert "PI_HOST_OVERRIDE" not in captured["environment"]
    assert captured["environment"]["GIT_AUTH_TOKEN"] == "upstream-token"
    assert captured["environment"]["DISABLE_TELEMETRY"] == "1"
    assert (destination / "SKILL.md").read_text() == "skill"


def test_skill_installer_rejects_nested_symlink_escape(tmp_path, monkeypatch):
    from mantle.errors import MantleError
    from mantle.skills import DirectSkillProvider

    outside = tmp_path / "private.md"
    outside.write_text("outside")

    def fake_runner(command, **kwargs):
        installed = kwargs["cwd"] / ".pi/skills/frontend-design"
        installed.mkdir(parents=True)
        (installed / "SKILL.md").write_text("skill")
        (installed / "private.md").symlink_to(outside)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("mantle.skills.run_installer", fake_runner)

    with pytest.raises(MantleError, match="outside its isolated directory"):
        DirectSkillProvider().install(
            "https://github.com/example/skills", "frontend-design",
            tmp_path / "agent/skills/frontend-design",
        )


def test_package_name_supports_native_pi_sources():
    assert package_name_from_source("npm:@scope/demo@1.2.3") == "@scope/demo"
    assert package_name_from_source("https://github.com/example/pi-tool.git") == "pi-tool"
