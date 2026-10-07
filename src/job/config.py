"""User-editable application settings; no credentials in configuration."""

import json
import os
from pathlib import Path

from pydantic import BaseModel, Field


class Settings(BaseModel):
    hub_url: str = ""
    table: str = Field(default="flights", pattern=r"^[a-z][a-z0-9_]*$")
    archive_prefix: str = Field(default="raw/flighty/", pattern=r"^[a-zA-Z0-9_/-]+/$")
    source: Path = Field(
        default_factory=lambda: (
            Path.home()
            / "Library/Containers/com.flightyapp.flighty/Data/Documents/MainFlightyDatabase.db"
        )
    )
    max_missing_fraction: float = Field(default=0.25, ge=0, le=1)


def state_dir():
    return Path(
        os.environ.get(
            "JOB_STATE_DIR", str(Path.home() / "Library/Application Support/FlightySync")
        )
    ).expanduser()


def load_settings():
    path = state_dir() / "config.json"
    return Settings.model_validate_json(path.read_text()) if path.exists() else Settings()


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_suffix(".tmp")
    with temp.open("w") as f:
        os.chmod(temp, 0o600)
        json.dump(data, f, indent=2)
        f.write("\n")
    temp.replace(path)
