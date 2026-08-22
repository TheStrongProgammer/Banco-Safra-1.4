from __future__ import annotations

import discord

from src.utils import make_bank_embed


class NotificationsView(discord.ui.View):
    def __init__(self, bot, owner_id: int) -> None:
        super().__init__(timeout=180)
        self.bot = bot
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await self.bot._reply_text(
                interaction,
                title="Acesso negado",
                description="Esse painel pertence a outro usuario.",
                color=0xB22222,
            )
            return False
        return True

    @discord.ui.button(label="Ativar", style=discord.ButtonStyle.success)
    async def activate(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        self.bot.database.update_notification_preferences(self.owner_id, ativo=True)
        await interaction.response.edit_message(
            embed=self.bot.build_notifications_embed(self.owner_id),
            view=self,
        )

    @discord.ui.button(label="Desativar", style=discord.ButtonStyle.danger)
    async def deactivate(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        self.bot.database.update_notification_preferences(self.owner_id, ativo=False)
        await interaction.response.edit_message(
            embed=self.bot.build_notifications_embed(self.owner_id),
            view=self,
        )


def build_notifications_embed(bot, user_id: int) -> discord.Embed:
    prefs = bot.database.get_notification_preferences(user_id)
    status = "Ativas" if prefs["ativo"] else "Desativadas"
    dm = "Ligado" if prefs["receber_dm"] else "Desligado"
    invest = "Ligado" if prefs["receber_investimentos"] else "Desligado"
    transfer = "Ligado" if prefs["receber_transferencias"] else "Desligado"
    debt = "Ligado" if prefs["receber_alertas_divida"] else "Desligado"

    embed = make_bank_embed(
        "🔔 Central de notificacoes",
        "Gerencie os alertas automaticos da sua conta no Banco Safra.",
        color=0x0B4EA2,
    )
    embed.add_field(name="Status geral", value=f"**{status}**", inline=False)
    embed.add_field(name="📬 DM", value=dm, inline=True)
    embed.add_field(name="📈 Investimentos", value=invest, inline=True)
    embed.add_field(name="💸 Transferencias", value=transfer, inline=True)
    embed.add_field(name="⚠️ Alertas de divida", value=debt, inline=True)
    embed.add_field(
        name="💡 O que voce recebe",
        value=(
            "Recebimentos, pagamentos, saldo baixo, credito atualizado, "
            "investimentos liberados, lucro, perda e alertas de divida."
        ),
        inline=False,
    )
    return embed
