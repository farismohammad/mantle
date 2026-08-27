from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator


AGENT_NAME_PATTERN = r"^[a-z0-9_][a-z0-9_-]*$"
SECRET_NAME_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")
MCP_SERVER_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]*$"
SecretName = Annotated[str, StringConstraints(pattern=SECRET_NAME_PATTERN.pattern)]
McpServerName = Annotated[str, StringConstraints(pattern=MCP_SERVER_NAME_PATTERN)]


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


class McpServerDefinition(StrictModel):
    command: str | None = Field(default=None, min_length=1, max_length=2_048)
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = Field(default=None, min_length=1, max_length=4_096)
    url: str | None = Field(default=None, min_length=1, max_length=8_192)
    headers: dict[str, str] = Field(default_factory=dict)
    auth: Literal["bearer", "oauth"] | None = None
    bearer_token_env: SecretName | None = None
    lifecycle: Literal["lazy", "eager", "keep-alive", "lazy-keep-alive"] = "lazy"
    idle_timeout: int | None = Field(default=None, ge=0)
    request_timeout_ms: int | None = Field(default=None, ge=0)
    protocol_version: Literal["legacy", "auto", "2026-07-28"] = "legacy"
    expose_resources: bool = True
    direct_tools: bool | list[str] = False
    include_tools: list[str] = Field(default_factory=list)
    exclude_tools: list[str] = Field(default_factory=list)
    disabled: bool = False
    debug: bool = False

    @field_validator("command", "cwd", "url")
    @classmethod
    def no_mcp_scalar_control_characters(cls, value: str | None) -> str | None:
        if value is not None and any(ord(character) < 32 for character in value):
            raise ValueError("must not contain control characters")
        return value

    @field_validator("args", "env", "headers", "include_tools", "exclude_tools")
    @classmethod
    def no_mcp_control_characters(cls, value):
        strings = value.values() if isinstance(value, dict) else value
        if any(any(ord(character) < 32 for character in item) for item in strings):
            raise ValueError("must not contain control characters")
        return value

    @model_validator(mode="after")
    def exactly_one_transport(self) -> "McpServerDefinition":
        if (self.command is None) == (self.url is None):
            raise ValueError("exactly one of command or url must be set")
        if self.url is not None and not self.url.startswith(("http://", "https://")):
            raise ValueError("url must use http or https")
        if self.command is None and (self.args or self.env or self.cwd is not None):
            raise ValueError("args, env, and cwd require a command transport")
        if self.url is None and (self.headers or self.auth is not None or self.bearer_token_env is not None):
            raise ValueError("headers and authentication require a url transport")
        if self.bearer_token_env is not None and self.auth != "bearer":
            raise ValueError("bearer_token_env requires auth: bearer")
        return self


class McpDefinition(StrictModel):
    servers: dict[McpServerName, McpServerDefinition] = Field(default_factory=dict)


class AgentConfig(StrictModel):
    mantle: Literal["0.1"] = "0.1"
    agent: AgentDefinition
    runtime: RuntimeDefinition = Field(default_factory=RuntimeDefinition)
    model: ModelDefinition = Field(default_factory=ModelDefinition)
    system_prompt: str | None = Field(default=None, max_length=100_000)
    resources: ResourcesDefinition = Field(default_factory=ResourcesDefinition)
    mcp: McpDefinition = Field(default_factory=McpDefinition)
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
        if self.mcp.servers and any(package.name == "pi-mcp-adapter" for package in self.resources.packages):
            raise ValueError("pi-mcp-adapter is managed automatically when mcp.servers are declared")
        undeclared = sorted({
            server.bearer_token_env
            for server in self.mcp.servers.values()
            if server.bearer_token_env is not None and server.bearer_token_env not in self.secrets
        })
        if undeclared:
            raise ValueError(f'MCP secret "{undeclared[0]}" must also be declared in secrets')
        return self


class RuntimeMetadata(StrictModel):
    schema_version: int = 1
    name: Literal["pi"] = "pi"
    version: str
    package: str = "@earendil-works/pi-coding-agent"


class MantleConfig(StrictModel):
    schema_version: int = 1
    cloud_url: str | None = None
    cloud_insecure: bool = False
    default_runtime: str | None = None


def default_agent_config(name: str, description: str = "", runtime_version: str = "latest") -> AgentConfig:
    return AgentConfig(
        agent=AgentDefinition(name=name, description=description),
        runtime=RuntimeDefinition(version=runtime_version),
    )
