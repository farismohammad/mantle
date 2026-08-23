from __future__ import annotations

from mantle.agents import AgentStore
from mantle.configuration import dump_agent_yaml
from mantle.models import ResourceDefinition, RuntimeMetadata, default_agent_config
from mantle.paths import MantlePaths
from mantle.provisioning import Provisioner


class FakeRuntimes:
    def ensure(self, version, **_kwargs):
        return RuntimeMetadata(version="1.0.0" if version == "latest" else version)


class FakeSkills:
    def install_reference(self, reference, root):
        target = root / "skills" / reference.name
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text(reference.source)


class FakePackages:
    def install_reference(self, reference, root, runtime_version):
        target = root / "packages" / reference.name
        target.mkdir(parents=True)
        (target / "source").write_text(f"{reference.source}@{runtime_version}")


class FailingSkills(FakeSkills):
    def install_reference(self, reference, root):
        raise RuntimeError("upstream unavailable")


def provisioner(paths, skills=None):
    return Provisioner(
        paths,
        runtime_manager=FakeRuntimes(),
        skill_manager=skills or FakeSkills(),
        package_manager=FakePackages(),
    )


def desired(description="First", skills=("alpha",), packages=("tool",)):
    config = default_agent_config("developer", description)
    config.resources.skills = [ResourceDefinition(name=name, source=f"https://example.test/{name}") for name in skills]
    config.resources.packages = [ResourceDefinition(name=name, source=f"npm:{name}") for name in packages]
    return config


def test_install_reconstructs_complete_agent(mantle_home):
    paths = MantlePaths.discover()
    config = desired()
    cloud_yaml = "# exact Cloud text is retained\n" + dump_agent_yaml(config)
    provisioner(paths).install(config, yaml_text=cloud_yaml)
    assert (paths.agent("developer") / "skills/alpha/SKILL.md").is_file()
    assert (paths.agent("developer") / "packages/tool/source").is_file()
    assert AgentStore(paths).get("developer").agent.description == "First"
    assert paths.yaml("developer").read_text() == cloud_yaml


def test_update_adds_and_removes_resources_and_preserves_local_state(mantle_home):
    paths = MantlePaths.discover()
    manager = provisioner(paths)
    manager.install(desired(skills=("alpha",), packages=("old",)))
    (paths.agent("developer") / "context/local.md").write_text("private local context")
    (paths.agent("developer") / "sessions/session.jsonl").write_text("{}")
    manager.update(desired("Second", skills=("beta",), packages=("new",)))
    root = paths.agent("developer")
    assert not (root / "skills/alpha").exists()
    assert not (root / "packages/old").exists()
    assert (root / "skills/beta/SKILL.md").is_file()
    assert (root / "packages/new/source").is_file()
    assert (root / "context/local.md").read_text() == "private local context"
    assert (root / "sessions/session.jsonl").is_file()


def test_failed_update_leaves_existing_agent_untouched(mantle_home):
    paths = MantlePaths.discover()
    provisioner(paths).install(desired())
    before = paths.yaml("developer").read_text()
    try:
        provisioner(paths, FailingSkills()).update(desired("Broken", skills=("beta",)))
    except Exception:
        pass
    assert paths.yaml("developer").read_text() == before
    assert (paths.agent("developer") / "skills/alpha/SKILL.md").is_file()
