"""Configuration + secret loading.

Secrets come from .env (gitignored, chmod 600). Everything else from config.yaml.
No secret is ever written to the database or logged.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
CONFIG_PATH = ROOT / "config.yaml"
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "propintel.db"


def load_env(path: Path = ENV_PATH) -> dict[str, str]:
    """Minimal .env parser (no external dependency, tolerant of comments)."""
    env: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            env[key.strip()] = val.strip().strip('"').strip("'")
    # Process env overrides file (useful for CI / one-offs)
    for k in ("DOMAIN_CLIENT_ID", "DOMAIN_CLIENT_SECRET"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    with open(path) as fh:
        return yaml.safe_load(fh)


class Settings:
    """Bundles config.yaml + secrets, with convenient accessors."""

    def __init__(self) -> None:
        self.env = load_env()
        self.cfg = load_config()
        DATA_DIR.mkdir(exist_ok=True)

    # --- secrets ---
    @property
    def domain_client_id(self) -> str | None:
        return self.env.get("DOMAIN_CLIENT_ID")

    @property
    def domain_client_secret(self) -> str | None:
        return self.env.get("DOMAIN_CLIENT_SECRET")

    def has_domain_creds(self) -> bool:
        return bool(self.domain_client_id and self.domain_client_secret)

    # --- config sections ---
    @property
    def criteria(self) -> dict[str, Any]:
        return self.cfg["criteria"]

    @property
    def scan(self) -> dict[str, Any]:
        return self.cfg["scan"]

    @property
    def ranking(self) -> dict[str, Any]:
        return self.cfg["ranking"]

    @property
    def domain(self) -> dict[str, Any]:
        return self.cfg["domain"]

    @property
    def db_path(self) -> Path:
        return DB_PATH


settings = Settings()
