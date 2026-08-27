from __future__ import annotations

from mantle.models import ResourceDefinition


def test_skills_add_uses_named_skill_and_agent_options(cli, monkeypatch):
    calls = []

    def add(manager, source, skill, agent, *, force=False):
        calls.append((source, skill, agent, force))
        return ResourceDefinition(name=skill, source=source)

    monkeypatch.setattr("mantle.cli.SkillManager.add", add)

    result = cli(
        "skills",
        "add",
        "https://github.com/anthropics/skills",
        "--skill",
        "frontend-design",
        "--agent",
        "frontend",
    )

    assert result.exit_code == 0, result.output
    assert calls == [
        (
            "https://github.com/anthropics/skills",
            "frontend-design",
            "frontend",
            False,
        )
    ]
    assert "Installed skill frontend-design into frontend" in result.output


def test_skills_add_requires_agent_option(cli):
    result = cli(
        "skills",
        "add",
        "https://github.com/anthropics/skills",
        "--skill",
        "frontend-design",
    )

    assert result.exit_code == 2
    assert "--agent" in result.output
