from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator


AGENT_NAME_PATTERN = r"^[a-z0-9_][a-z0-9_-]*$"
SECRET_NAME_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")
MCP_SERVER_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]*$"
RUNTIME_VERSION_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$"
RESOURCE_NAME_PATTERN = r"^(?:@[A-Za-z0-9_-]+/)?[A-Za-z0-9_][A-Za-z0-9_.-]*$"
SecretName = Annotated[str, StringConstraints(pattern=SECRET_NAME_PATTERN.pattern)]
McpServerName = Annotated[str, StringConstraints(pattern=MCP_SERVER_NAME_PATTERN)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class AgentDefinition(StrictModel):
    name: str = Field(min_length=1, max_length=64, pattern=AGENT_NAME_PATTERN)
    description: str = Field(default="", max_length=2_000)


class RuntimeDefinition(StrictModel):
    name: Literal["pi"] = "pi"
    version: str = Field(default="latest", min_length=1, max_length=64, pattern=RUNTIME_VERSION_PATTERN)


class ModelDefinition(StrictModel):
    provider: str = Field(default="openai", min_length=1, max_length=64)
    name: str = Field(default="gpt-5.6", min_length=1, max_length=160)


class ResourceDefinition(StrictModel):
    name: str = Field(min_length=1, max_length=128, pattern=RESOURCE_NAME_PATTERN)
    source: str = Field(min_length=1, max_length=2_048)
    version: None = Field(default=None, description="Deprecated: pin the version in source instead.")

    @field_validator("version", mode="before")
    @classmethod
    def no_ignored_version(cls, value):
        if value is not None:
            raise ValueError("resource.version was never used for installation; remove it and pin the version in source instead (for example npm:package@1.2.3)")
        return value

    @field_validator("name", "source", "version")
    @classmethod
    def no_control_characters(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or value != value.strip()):
            raise ValueError("must not be blank or have surrounding whitespace")
        if value is not None and any(ord(character) < 32 for character in value):
            raise ValueError("must not contain control characters")
        return value


class ResourcesDefinition(StrictModel):
    skills: list[ResourceDefinition] = Field(default_factory=list)
    packages: list[ResourceDefinition] = Field(default_factory=list)

    @field_validator("packages")
    @classmethod
    def valid_package_sources(cls, packages):
        from mantle.errors import MantleError
        from mantle.sources import validate_package_source

        for index, package in enumerate(packages):
            try:
                validate_package_source(package.source)
            except MantleError as error:
                raise ValueError(f"resources.packages.{index}.source: {error}") from error
        return packages


class PermissionsDefinition(StrictModel):
    filesystem: Literal["all", "read", "none"] = "all"
    shell: Literal["all", "none"] = "all"

    @field_validator("filesystem", mode="before")
    @classmethod
    def no_unenforced_workspace_mode(cls, value):
        if value == "workspace":
            raise ValueError("workspace confinement is not supported; choose all, read, or none. These select Pi tools, not an OS sandbox")
        return value


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
        reserved = [value for value in values if value.startswith("PI_")]
        if reserved:
            raise ValueError(f"{reserved[0]} is reserved for Mantle's Pi isolation settings; use provider credential names instead")
        if len(values) != len(set(values)):
            raise ValueError("secret names must be unique")
        return values

    @model_validator(mode="after")
    def unique_resources(self) -> "AgentConfig":
        if any("/" in resource.name for resource in self.resources.skills):
            raise ValueError("skill names must be single directory names")
        for label, resources in (("skill", self.resources.skills), ("package", self.resources.packages)):
            names = [resource.name for resource in resources]
            if len(names) != len(set(names)):
                raise ValueError(f"{label} names must be unique")
        sources = []
        for package in self.resources.packages:
            source = package.source
            if source.startswith("npm:"):
                if "@" in source[5:]:
                    source = source.rsplit("@", 1)[0]
            else:
                from mantle.sources import git_package_path
                source = "git:" + str(git_package_path(source))
            sources.append(source)
        if len(sources) != len(set(sources)):
            raise ValueError("package sources must be unique; multiple versions or aliases of the same package cannot coexist")
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
    schema_version: Literal[1] = 1
    name: Literal["pi"] = "pi"
    version: str = Field(pattern=RUNTIME_VERSION_PATTERN)
    package: str = "@earendil-works/pi-coding-agent"


class MantleConfig(StrictModel):
    schema_version: Literal[1] = 1
    cloud_url: str | None = None
    cloud_insecure: bool = False
    default_runtime: str | None = Field(default=None, pattern=RUNTIME_VERSION_PATTERN)


def default_agent_config(name: str, description: str = "", runtime_version: str = "latest") -> AgentConfig:
    return AgentConfig.model_validate({
        "agent": {"name": name, "description": description},
        "runtime": {"version": runtime_version},
    })
