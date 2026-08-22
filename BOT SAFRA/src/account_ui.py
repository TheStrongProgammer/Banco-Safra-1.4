from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import discord

from src.utils import format_currency, format_datetime, make_bank_embed, parse_amount

if TYPE_CHECKING:
    from src.bot import BancoSafraBot


class AccountApprovalView(discord.ui.View):
    def __init__(self, bot: "BancoSafraBot", user_id: int) -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self.user_id = user_id

        approve_button = discord.ui.Button(
            label="Aprovar conta",
            style=discord.ButtonStyle.success,
            custom_id=f"safra_account_approve:{user_id}",
        )
        approve_button.callback = self._approve_callback
        self.add_item(approve_button)

        reject_button = discord.ui.Button(
            label="Recusar conta",
            style=discord.ButtonStyle.danger,
            custom_id=f"safra_account_reject:{user_id}",
        )
        reject_button.callback = self._reject_callback
        self.add_item(reject_button)

    async def _approve_callback(self, interaction: discord.Interaction) -> None:
        if not self.bot.is_admin_authorized(interaction):
            await interaction.response.send_message(
                embed=make_bank_embed(
                    "Acesso negado",
                    "Apenas administradores autorizados podem aprovar contas.",
                    color=0xB22222,
                ),
                ephemeral=True,
            )
            return

        profile = self.bot.database.get_account_profile(self.user_id)
        if profile is None or str(profile["status"]) != "pendente":
            await interaction.response.send_message(
                embed=make_bank_embed(
                    "Solicitacao indisponivel",
                    "Esta solicitacao ja foi processada ou nao existe mais.",
                    color=0xB22222,
                ),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        try:
            await self.bot.finalize_account_approval(
                interaction.guild,
                user_id=self.user_id,
                approved_by=interaction.user,
            )
            for child in self.children:
                child.disabled = True
            if interaction.message is not None:
                await interaction.message.edit(view=self)
            await interaction.followup.send(
                embed=make_bank_embed(
                    "Conta aprovada",
                    f"A conta de <@{self.user_id}> foi aprovada com sucesso.",
                    color=0x1E8E5A,
                ),
                ephemeral=True,
            )
        except Exception as exc:
            await interaction.followup.send(
                embed=make_bank_embed(
                    "Falha na aprovacao",
                    str(exc),
                    color=0xB22222,
                ),
                ephemeral=True,
            )

    async def _reject_callback(self, interaction: discord.Interaction) -> None:
        if not self.bot.is_admin_authorized(interaction):
            await interaction.response.send_message(
                embed=make_bank_embed(
                    "Acesso negado",
                    "Apenas administradores autorizados podem recusar contas.",
                    color=0xB22222,
                ),
                ephemeral=True,
            )
            return

        profile = self.bot.database.get_account_profile(self.user_id)
        if profile is None or str(profile["status"]) != "pendente":
            await interaction.response.send_message(
                embed=make_bank_embed(
                    "Solicitacao indisponivel",
                    "Esta solicitacao ja foi processada ou nao existe mais.",
                    color=0xB22222,
                ),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        await self.bot.finalize_account_rejection(
            user_id=self.user_id,
            rejected_by=interaction.user,
        )
        for child in self.children:
            child.disabled = True
        if interaction.message is not None:
            await interaction.message.edit(view=self)
        await interaction.followup.send(
            embed=make_bank_embed(
                "Conta recusada",
                f"A solicitacao de <@{self.user_id}> foi recusada.",
                color=0xB22222,
            ),
            ephemeral=True,
        )


class AccountCreationModal(discord.ui.Modal):
    def __init__(self, bot: "BancoSafraBot") -> None:
        super().__init__(title="Abertura de conta - Banco Safra")
        self.bot = bot

        self.nome_input = discord.ui.TextInput(
            label="Nome completo (RP)",
            placeholder="Ex: Joao Henrique da Silva",
            required=True,
            max_length=80,
        )
        self.senha_input = discord.ui.TextInput(
            label="Senha escolhida (RP)",
            placeholder="Crie uma senha para saldo e investimentos",
            required=True,
            min_length=4,
            max_length=64,
        )
        self.deposito_input = discord.ui.TextInput(
            label="Deposito inicial (minimo R$ 100)",
            placeholder="Ex: 15000",
            required=True,
            max_length=20,
        )
        self.tipo_conta_input = discord.ui.TextInput(
            label="Tipo de conta",
            placeholder="Corrente, VIP, Premium, Empresarial...",
            required=True,
            max_length=40,
        )
        self.telefone_input = discord.ui.TextInput(
            label="Telefone ou contato RP",
            placeholder="Ex: (11) 99999-0000",
            required=True,
            max_length=40,
        )

        self.add_item(self.nome_input)
        self.add_item(self.senha_input)
        self.add_item(self.deposito_input)
        self.add_item(self.tipo_conta_input)
        self.add_item(self.telefone_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            existing = self.bot.database.get_account_profile(interaction.user.id)
            if existing is not None:
                status = str(existing["status"])
                if status == "pendente":
                    await self.bot._reply_text(
                        interaction,
                        title="Solicitacao em analise",
                        description=(
                            "Voce ja possui uma solicitacao de conta aguardando "
                            "aprovacao dos administradores."
                        ),
                        color=0xC97C00,
                        ephemeral=True,
                    )
                    return
                if status == "ativa":
                    await self.bot._reply_text(
                        interaction,
                        title="Conta ja existente",
                        description="Voce ja possui uma conta aberta no Banco Safra.",
                        color=0xB22222,
                        ephemeral=True,
                    )
                    return

            deposito_inicial = parse_amount(str(self.deposito_input.value))
            if deposito_inicial < 100:
                raise ValueError("O deposito inicial minimo para abrir a conta e de R$ 100,00.")

            await self.bot._defer_if_needed(interaction, ephemeral=True)

            now = datetime.now(UTC)
            next_fee_at = now + timedelta(days=7)
            password_hash = self.bot.hash_password(str(self.senha_input.value))

            if existing is not None and str(existing["status"]) == "recusada":
                self.bot.database.delete_account_profile(interaction.user.id)

            self.bot.database.create_account_profile(
                user_id=interaction.user.id,
                nome_completo=str(self.nome_input.value).strip(),
                senha_rp_hash=password_hash,
                deposito_inicial=deposito_inicial,
                discord_id=str(interaction.user.id),
                tipo_conta=str(self.tipo_conta_input.value).strip(),
                telefone_rp=str(self.telefone_input.value).strip(),
                profissao_rp="Nao informado",
                created_at=now.isoformat(),
                next_fee_at=next_fee_at.isoformat(),
                status="pendente",
            )

            embed = make_bank_embed(
                "Solicitacao enviada",
                (
                    "Sua solicitacao de abertura de conta foi registrada e enviada "
                    "para analise dos administradores. Voce sera notificado quando "
                    "a conta for aprovada ou recusada."
                ),
                color=0x0B4EA2,
            )
            embed.add_field(
                name="Nome RP",
                value=str(self.nome_input.value).strip(),
                inline=False,
            )
            embed.add_field(
                name="Tipo de conta",
                value=str(self.tipo_conta_input.value).strip(),
                inline=True,
            )
            embed.add_field(
                name="Deposito inicial",
                value=f"**{format_currency(deposito_inicial)}**",
                inline=True,
            )
            embed.add_field(
                name="Status",
                value="**Aguardando aprovacao**",
                inline=True,
            )
            await self.bot._reply_embed(interaction, embed, ephemeral=True)
            await self.bot.post_account_approval_request(
                guild=interaction.guild,
                member=interaction.user,
            )
        except Exception as exc:
            await self.bot._reply_text(
                interaction,
                title="Abertura nao concluida",
                description=str(exc),
                color=0xB22222,
                ephemeral=True,
            )
