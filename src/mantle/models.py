from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator


AGENT_NAME_PATTERN = r"^[a-z0-9_][a-z0-9_-]*$"
SECRET_NAME_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")
SecretName = Annotated[str, StringConstraints(pattern=SECRET_NAME_PATTERN.pattern)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentDefinition(StrictModel):
    name: str = Field(min_length=1, max_length=64, pattern=AGENT_NAME_PATTERN)
    description: str = Field(default="", max_length=2_000)


class RuntimeDefinition(StrictModel):
    name: Literal["pi"] = "pi"
    version: str = Field(default="latest", min_length=1, max_length=64)


class ModelDefinition(StrictModel):
    provider: str = Field(default="openai", min_length=1, max_length=64)
    name: str = Field(default="gpt-5.6", min_length=1, max_length=160)


class ResourceDefinition(StrictModel):
    name: str = Field(min_length=1, max_length=128)
    source: str = Field(min_length=1, max_length=2_048)
    version: str | None = Field(default=None, max_length=256)

    @field_validator("name", "source", "version")
    @classmethod
    def no_control_characters(cls, value: str | None) -> str | None:
        if value is not None and any(ord(character) < 32 for character in value):
            raise ValueError("must not contain control characters")
        return value


class ResourcesDefinition(StrictModel):
    skills: list[ResourceDefinition] = Field(default_factory=list)
    packages: list[ResourceDefinition] = Field(default_factory=list)


class PermissionsDefinition(StrictModel):
    filesystem: Literal["all", "workspace", "read", "none"] = "all"
    shell: Literal["all", "none"] = "all"


class AgentConfig(StrictModel):
    mantle: Literal["0.1"] = "0.1"
    agent: AgentDefinition
    runtime: RuntimeDefinition = Field(default_factory=RuntimeDefinition)
    model: ModelDefinition = Field(default_factory=ModelDefinition)
    system_prompt: str | None = Field(default=None, max_length=100_000)
    resources: ResourcesDefinition = Field(default_factory=ResourcesDefinition)
    permissions: PermissionsDefinition = Field(default_factory=PermissionsDefinition)
    secrets: list[SecretName] = Field(default_factory=list)

    @field_validator("secrets")
    @classmethod
    def valid_secrets(cls, values: list[str]) -> list[str]:
        invalid = [value for value in values if not SECRET_NAME_PATTERN.fullmatch(value)]
        if invalid:
            raise ValueError(f"invalid environment variable name: {invalid[0]}")
        if len(values) != len(set(values)):
            raise ValueError("secret names must be unique")
        return values

    @model_validator(mode="after")
    def unique_resources(self) -> "AgentConfig":
        for label, resources in (("skill", self.resources.skills), ("package", self.resources.packages)):
            names = [resource.name for resource in resources]
            if len(names) != len(set(names)):
                raise ValueError(f"{label} names must be unique")
        return self


class RuntimeMetadata(StrictModel):
    schema_version: int = 1
    name: Literal["pi"] = "pi"
    version: str
    package: str = "@earendil-works/pi-coding-agent"


class MantleConfig(StrictModel):
    schema_version: int = 1
    cloud_url: str | None = None
    default_runtime: str | None = None


def default_agent_config(name: str, description: str = "", runtime_version: str = "latest") -> AgentConfig:
    return AgentConfig(
        agent=AgentDefinition(name=name, description=description),
        runtime=RuntimeDefinition(version=runtime_version),
    )
