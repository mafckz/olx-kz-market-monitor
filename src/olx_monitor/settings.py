from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from olx_monitor.paths import app_home, ensure_directories


@dataclass(frozen=True, slots=True)
class Settings:
    home: Path
    poll_hours: int = 2
    deep_check_hours: int = 24
    request_delay_seconds: float = 2.0
    request_timeout_seconds: float = 25.0
    max_pages: int = 25
    max_retries: int = 3
    ai_model: str = "gpt-6-luna"
    ai_monthly_budget_usd: float = 0.0
    ai_input_usd_per_million: float = 0.10
    ai_output_usd_per_million: float = 0.50
    ai_cost_reserve_multiplier: float = 2.0
    ai_prices_checked_on: str = "2026-10-22"

    @classmethod
    def load(cls, home: Path | None = None) -> Settings:
        root = ensure_directories(home or app_home())
        path = root / "settings.json"
        if not path.exists():
            return cls(home=root)
        try:
            raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise ValueError(f"Не удалось прочитать {path}: {exc}") from exc
        allowed = set(cls.__dataclass_fields__) - {"home"}
        data = {key: value for key, value in raw.items() if key in allowed}
        return cls(home=root, **data)

    def save(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        payload = asdict(self)
        payload.pop("home", None)
        (self.home / "settings.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
