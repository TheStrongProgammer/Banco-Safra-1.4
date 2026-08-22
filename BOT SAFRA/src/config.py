from __future__ import annotations

import os
from dataclasses import dataclass


def _normalize_env_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return value.strip()


@dataclass(frozen=True)
class Settings:
    token: str
    prefix: str = "!"
    bot_name: str = "Banco Safra BOT"
    database_path: str = "data/banco_safra.db"
    log_path: str = "data/transactions.log"
    logo_path: str = "logo.png"
    low_balance_alert: float = 1000.0


def load_settings() -> Settings:
    token = _normalize_env_value(os.getenv("DISCORD_TOKEN", ""))
    if not token:
        raise RuntimeError(
            "DISCORD_TOKEN nao foi definido. Configure a variavel no arquivo .env."
        )
    if len(token) < 20 or any(char.isspace() for char in token):
        raise RuntimeError(
            "DISCORD_TOKEN parece invalido. Gere um novo token de bot no Discord Developer Portal e substitua o valor em .env."
        )

    prefix = _normalize_env_value(os.getenv("BOT_PREFIX", "!")).strip() or "!"
    return Settings(token=token, prefix=prefix)
