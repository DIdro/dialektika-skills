import os
from dataclasses import dataclass
from pathlib import Path
import tomli
from platformdirs import user_config_dir


class ConfigMissing(Exception): ...


def config_path() -> Path:
    base = os.environ.get("PIPELINE_CLI_CONFIG_DIR") or user_config_dir("call-pipeline")
    return Path(base) / "config.toml"


@dataclass(frozen=True)
class Config:
    server_url: str
    bearer_token: str
    user_id: str
    admin_token: str | None


def load_config() -> Config:
    p = config_path()
    if not p.exists():
        raise ConfigMissing(f"config not found at {p}. Run: pipeline init")
    data = tomli.loads(p.read_text(encoding="utf-8"))
    s = data.get("server", {})
    return Config(
        server_url=s["url"],
        bearer_token=s["bearer_token"],
        user_id=s["user_id"],
        admin_token=s.get("admin_token"),
    )
