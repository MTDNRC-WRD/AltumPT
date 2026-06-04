from __future__ import annotations
from pathlib import Path
import tomllib  # Python 3.11+

PROJECT_CONFIG = Path(r"config.toml")

def load_config(config_path: Path | None = None) -> dict:
    cfg_path = config_path or PROJECT_CONFIG
    with cfg_path.open("rb") as f:
        cfg = tomllib.load(f)
    return cfg

def date_config(cfg: dict, yyyymmdd: str) -> dict:
    key = yyyymmdd
    if "dates" not in cfg or key not in cfg["dates"]:
        raise KeyError(f"No date config for {yyyymmdd}")
    return cfg["dates"][key]