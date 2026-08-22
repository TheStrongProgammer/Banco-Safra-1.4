from __future__ import annotations

import math
from datetime import UTC, datetime
from pathlib import Path

import discord


def parse_amount(raw_value: str) -> float:
    normalized = raw_value.replace(",", ".").strip()
    try:
        amount = float(normalized)
    except ValueError as exc:
        raise ValueError("Informe um valor numerico valido.") from exc

    if not math.isfinite(amount) or amount <= 0:
        raise ValueError("O valor deve ser maior que zero.")

    return round(amount, 2)


def format_currency(value: float) -> str:
    formatted = f"{value:,.2f}"
    formatted = formatted.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {formatted}"


def format_datetime(value: str | datetime) -> str:
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value)
    else:
        parsed = value
    local = parsed.astimezone()
    return local.strftime("%d/%m/%Y %H:%M")


def make_embed(title: str, description: str, color: int = 0x2E8B57) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=color)
    embed.timestamp = datetime.now(UTC)
    return embed


def make_bank_embed(
    title: str,
    description: str,
    *,
    color: int = 0x0B4EA2,
) -> discord.Embed:
    embed = make_embed(title, description, color=color)
    embed.set_author(name="Banco Safra BOT", icon_url="attachment://logo.png")
    embed.set_thumbnail(url="attachment://logo.png")
    embed.set_footer(text="Safra RP • Economia premium")
    return embed


def log_transaction(log_path: str, message: str) -> None:
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with path.open("a", encoding="utf-8") as log_file:
        log_file.write(f"[{timestamp}] {message}\n")
