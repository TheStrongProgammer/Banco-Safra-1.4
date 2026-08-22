from __future__ import annotations

from typing import TYPE_CHECKING, Awaitable, Callable

import discord

if TYPE_CHECKING:
    from src.bot import BancoSafraBot


ActionCallback = Callable[[discord.Interaction], Awaitable[None]]


class PasswordModal(discord.ui.Modal):
    def __init__(
        self,
        bot: "BancoSafraBot",
        *,
        area: str,
        callback: ActionCallback,
    ) -> None:
        super().__init__(title=f"Acesso protegido - {area.title()}")
        self.bot = bot
        self.area = area
        self.callback = callback
        self.password_input = discord.ui.TextInput(
            label="Senha",
            placeholder="Digite sua senha",
            required=True,
            max_length=64,
        )
        self.add_item(self.password_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            if not self.bot.validate_password(
                interaction.user.id,
                self.area,
                str(self.password_input.value),
            ):
                await self.bot._reply_text(
                    interaction,
                    title="Senha incorreta",
                    description="A senha informada nao confere com a area solicitada.",
                    color=0xB22222,
                    ephemeral=True,
                )
                return
            await self.callback(interaction)
        except Exception as exc:
            await self.bot._reply_text(
                interaction,
                title="Operacao nao concluida",
                description=str(exc),
                color=0xB22222,
                ephemeral=True,
            )
