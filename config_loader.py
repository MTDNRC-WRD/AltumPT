"""Load project configuration from a TOML file.

This module provides small helper functions for reading the project TOML
configuration and retrieving commonly used sections such as folder settings.
It supports Python 3.11+ via `tomllib` and falls back to `tomli` on older
Python versions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    import tomllib  # type: ignore[attr-defined]
except ModuleNotFoundError:
    import tomli as tomllib  # pip install tomli


ConfigDict = dict[str, Any]

PROJECT_CONFIG = Path("config.toml")


def load_config(config_path: Path | None = None) -> ConfigDict:
    """Load the TOML project configuration file.

    Args:
        config_path: Optional path to a TOML configuration file. If not
            provided, the default project config path is used.

    Returns:
        The parsed TOML configuration as a nested dictionary.

    Raises:
        FileNotFoundError: If the configuration file does not exist.
        tomllib.TOMLDecodeError: If the file cannot be parsed as valid TOML.
    """
    cfg_path = config_path or PROJECT_CONFIG

    with cfg_path.open("rb") as file_obj:
        cfg = tomllib.load(file_obj)

    return cfg


def folder_config(cfg: ConfigDict) -> dict[str, str]:
    """Return the configured folder section from the project config.

    Args:
        cfg: Parsed project configuration dictionary.

    Returns:
        The `folders` configuration section.

    Raises:
        KeyError: If the `folders` section is missing.
    """
    if "folders" not in cfg:
        raise KeyError("Missing required [folders] section in config.toml")
    return cfg["folders"]


def get_path(cfg: ConfigDict, key: str) -> Path:
    """Return a path value from the config as a `Path` object.

    This helper checks the `[folders]` section first, then falls back to the
    `[paths]` section.

    Args:
        cfg: Parsed project configuration dictionary.
        key: Name of the path key to retrieve.

    Returns:
        The requested config value converted to a `Path`.

    Raises:
        KeyError: If the requested key is not found in either section.
    """
    if "folders" in cfg and key in cfg["folders"]:
        return Path(cfg["folders"][key])

    if "paths" in cfg and key in cfg["paths"]:
        return Path(cfg["paths"][key])

    raise KeyError(f"Path key '{key}' not found in [folders] or [paths]")