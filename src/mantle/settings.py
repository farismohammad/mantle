"""Load and save MANTLE_HOME/config.json with errors that name the file."""
from __future__ import annotations

from pydantic import ValidationError

from mantle.errors import MantleError
from mantle.models import MantleConfig
from mantle.paths import MantlePaths
from mantle.storage import load_json, write_json


def load_settings(paths: MantlePaths) -> MantleConfig:
    data = load_json(paths.config, {})
    try:
        return MantleConfig.model_validate(data)
    except ValidationError as error:
        lines = []
        for issue in error.errors(include_url=False):
            field = ".".join(str(part) for part in issue["loc"]) or "settings"
            lines.append(f"  {field}: {issue['msg']}")
        raise MantleError(
            f"Invalid Mantle settings in {paths.config}:\n" + "\n".join(lines)
            + "\nFix or remove that entry. Cloud settings can be rewritten with: mantle cloud connect"
        ) from error


def save_settings(paths: MantlePaths, settings: MantleConfig) -> None:
    write_json(paths.config, settings.model_dump(mode="json"))
