from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import discord

from src.utils import make_bank_embed

if TYPE_CHECKING:
    from src.bot import BancoSafraBot


NOTIFICATION_COLORS = {
    "recebimento": 0x1E8E5A,
    "envio": 0x0B4EA2,
    "lucro_investimento": 0x1E8E5A,
    "perda_investimento": 0xB22222,
    "investimento_liberado": 0x0B4EA2,
    "divida_vencendo": 0xD97706,
    "divida_vencida": 0xB22222,
    "credito_atualizado": 0x8C6B00,
    "saldo_baixo": 0xB22222,
    "bonus": 0x7C3AED,
}


class NotificationService:
    """Gerencia preferencias e envio de notificacoes diretas do bot."""

    def __init__(self, database) -> None:
        self.database = database

    async def enviar(
        self,
        bot: BancoSafraBot,
        user_id: int,
        tipo: str,
        mensagem: str,
        *,
        title: str | None = None,
        dedupe_key: str | None = None,
        dedupe_window: timedelta | None = None,
    ) -> bool:
        prefs = self.database.get_notification_preferences(user_id)
        if not prefs["ativo"] or not prefs["receber_dm"]:
            return False

        if tipo.startswith("divida") and not prefs["receber_alertas_divida"]:
            return False
        if "investimento" in tipo and not prefs["receber_investimentos"]:
            return False
        if tipo in {"recebimento", "envio"} and not prefs["receber_transferencias"]:
            return False

        if dedupe_key and self._is_duplicate(user_id, dedupe_key, dedupe_window):
            return False

        user = bot.get_user(user_id)
        if user is None:
            try:
                user = await bot.fetch_user(user_id)
            except discord.HTTPException as exc:
                print(f"[Notificacoes] Falha ao buscar usuario {user_id}: {exc}")
                return False

        embed = make_bank_embed(
            title or self._default_title(tipo),
            mensagem,
            color=NOTIFICATION_COLORS.get(tipo, 0x0B4EA2),
        )
        try:
            await user.send(embed=embed)
        except discord.Forbidden:
            print(f"[Notificacoes] DM fechada para usuario {user_id}.")
            return False
        except discord.HTTPException as exc:
            print(f"[Notificacoes] Falha ao enviar DM para {user_id}: {exc}")
            return False

        if dedupe_key:
            self.database.create_notification_log(
                user_id=user_id,
                tipo=tipo,
                chave=dedupe_key,
                created_at=self._now().isoformat(),
            )
        return True

    def _is_duplicate(
        self,
        user_id: int,
        dedupe_key: str,
        window: timedelta | None,
    ) -> bool:
        if window is None:
            return self.database.get_notification_log(user_id, dedupe_key) is not None

        log = self.database.get_notification_log(user_id, dedupe_key)
        if log is None:
            return False
        created_at = datetime.fromisoformat(str(log["created_at"]))
        return (self._now() - created_at) <= window

    @staticmethod
    def _default_title(tipo: str) -> str:
        titles = {
            "recebimento": "💰 Dinheiro recebido",
            "envio": "💸 Pagamento enviado",
            "lucro_investimento": "📈 Lucro em investimento",
            "perda_investimento": "📉 Perda em investimento",
            "investimento_liberado": "⏰ Investimento liberado",
            "divida_vencendo": "⚠️ Divida vencendo",
            "divida_vencida": "🚨 Divida vencida",
            "credito_atualizado": "💳 Credito atualizado",
            "saldo_baixo": "📉 Saldo baixo",
            "bonus": "🎁 Bonus recebido",
        }
        return titles.get(tipo, "🔔 Nova notificacao")

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)


async def enviar_notificacao(
    bot: "BancoSafraBot",
    user_id: int,
    tipo: str,
    mensagem: str,
    **kwargs,
) -> bool:
    """Funcao central reutilizavel pedida para o sistema de notificacoes."""
    return await bot.notifications.enviar(bot, user_id, tipo, mensagem, **kwargs)
