from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

APP_DIR = Path.home() / ".draftsmith"
CONFIG_PATH = APP_DIR / "config.json"
DB_PATH = APP_DIR / "state.sqlite3"


@dataclass
class AppConfig:
    client_secrets_path: str = ""
    labels: list[str] = field(default_factory=lambda: ["INBOX"])
    refresh_minutes: int = 5
    model: str = "gpt-5-mini"
    sent_context_count: int = 12
    drafting_prompt: str = "Draft the most likely reply."

    @classmethod
    def load(cls) -> "AppConfig":
        try:
            values = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            allowed = cls.__dataclass_fields__
            return cls(**{k: v for k, v in values.items() if k in allowed})
        except (FileNotFoundError, json.JSONDecodeError, TypeError):
            return cls()

    def save(self) -> None:
        APP_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        CONFIG_PATH.chmod(0o600)
