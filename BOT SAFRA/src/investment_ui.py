from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from src.utils import format_currency, format_datetime, make_bank_embed, parse_amount

if TYPE_CHECKING:
    from src.bot import BancoSafraBot


class BaseInvestmentView(discord.ui.View):
    def __init__(self, bot: BancoSafraBot, owner_id: int) -> None:
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
                ephemeral=True,
            )
            return False
        return True


class InvestmentAmountModal(discord.ui.Modal):
    def __init__(
        self,
        bot: BancoSafraBot,
        user_id: int,
        *,
        mode: str,
        option_key: str,
        title: str,
    ) -> None:
        super().__init__(title=title)
        self.bot = bot
        self.user_id = user_id
        self.mode = mode
        self.option_key = option_key
        self.amount_input = discord.ui.TextInput(
            label="Valor para investir",
            placeholder="Ex: 25000",
            required=True,
            max_length=20,
        )
        self.add_item(self.amount_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            await self.bot._defer_if_needed(interaction, ephemeral=False)
            amount = parse_amount(str(self.amount_input.value))

            if self.mode == "cdb":
                investment = self.bot.investments.create_cdb(
                    self.user_id,
                    amount,
                    self.option_key,
                )
                embed = make_bank_embed(
                    "📈 CDB contratado",
                    "Seu investimento travado foi registrado com sucesso.",
                    color=0x0B4EA2,
                )
                embed.add_field(name="🆔 ID", value=f"`{investment['id']}`", inline=True)
                embed.add_field(
                    name="💰 Valor aplicado",
                    value=f"**{format_currency(float(investment['valor_inicial']))}**",
                    inline=True,
                )
                embed.add_field(
                    name="🏁 Valor no resgate",
                    value=f"**{format_currency(float(investment['valor_atual']))}**",
                    inline=True,
                )
                embed.add_field(
                    name="📅 Inicio",
                    value=format_datetime(str(investment["data_inicio"])),
                    inline=True,
                )
                embed.add_field(
                    name="⏳ Liberacao",
                    value=format_datetime(str(investment["data_resgate"])),
                    inline=True,
                )
                await self.bot._reply_embed(interaction, embed)
                await self.bot.send_transaction_log(
                    title="📈 CDB contratado",
                    lines=[
                        f"Usuario: {interaction.user.mention}",
                        f"Investimento ID: `{investment['id']}`",
                        f"Valor aplicado: **{format_currency(float(investment['valor_inicial']))}**",
                        f"Valor no resgate: **{format_currency(float(investment['valor_atual']))}**",
                    ],
                    color=0x0B4EA2,
                )
                await self.bot._notify_low_balance_if_needed(self.user_id)
                return

            if self.mode == "fundo":
                investment = self.bot.investments.create_fund(
                    self.user_id,
                    amount,
                    self.option_key,
                )
                embed = make_bank_embed(
                    "🪙 Fundo contratado",
                    "Seu aporte entrou no fundo e agora sera atualizado automaticamente.",
                    color=0x8C6B00,
                )
                embed.add_field(name="🆔 ID", value=f"`{investment['id']}`", inline=True)
                embed.add_field(
                    name="📦 Perfil",
                    value=str(investment["subtipo"]).title(),
                    inline=True,
                )
                embed.add_field(
                    name="💰 Valor aplicado",
                    value=f"**{format_currency(float(investment['valor_inicial']))}**",
                    inline=True,
                )
                embed.add_field(
                    name="📈 Valor atual",
                    value=f"**{format_currency(float(investment['valor_atual']))}**",
                    inline=True,
                )
                await self.bot._reply_embed(interaction, embed)
                await self.bot.send_transaction_log(
                    title="🪙 Fundo contratado",
                    lines=[
                        f"Usuario: {interaction.user.mention}",
                        f"Fundo ID: `{investment['id']}`",
                        f"Perfil: **{str(investment['subtipo']).title()}**",
                        f"Valor aplicado: **{format_currency(float(investment['valor_inicial']))}**",
                    ],
                    color=0x8C6B00,
                )
                await self.bot._notify_low_balance_if_needed(self.user_id)
                return

            if self.mode == "risco":
                result = self.bot.investments.run_risk_investment(
                    self.user_id,
                    amount,
                    self.option_key,
                )
                if (result.delta or 0) < 0:
                    await self.bot._credit_manager_loss(
                        abs(result.delta or 0),
                        source=f"risco {self.option_key}",
                        actor_user_id=self.user_id,
                    )
                    await self.bot.notifications.enviar(
                        self.bot,
                        self.user_id,
                        "perda_investimento",
                        f"📉 Sua operacao de risco perdeu {format_currency(abs(result.delta or 0))}.",
                    )
                elif (result.delta or 0) > 0:
                    await self.bot.notifications.enviar(
                        self.bot,
                        self.user_id,
                        "lucro_investimento",
                        f"📈 Sua operacao de risco lucrou {format_currency(result.delta or 0)}.",
                    )

                embed = make_bank_embed(
                    f"🎲 {result.title}",
                    result.description,
                    color=result.color,
                )
                embed.add_field(
                    name="💵 Resultado bruto",
                    value=f"**{format_currency(result.payout or 0)}**",
                    inline=True,
                )
                embed.add_field(
                    name="📊 Lucro / perda",
                    value=f"**{format_currency(result.delta or 0)}**",
                    inline=True,
                )
                embed.add_field(
                    name="🏦 Saldo atual no banco",
                    value=f"**{format_currency(self.bot.economy.get_balance(self.user_id))}**",
                    inline=False,
                )
                await self.bot._reply_embed(interaction, embed)
                await self.bot.send_transaction_log(
                    title="🎲 Operacao de risco",
                    lines=[
                        f"Usuario: {interaction.user.mention}",
                        f"Perfil: **{self.option_key.title()}**",
                        f"Resultado bruto: **{format_currency(result.payout or 0)}**",
                        f"Lucro / perda: **{format_currency(result.delta or 0)}**",
                    ],
                    color=result.color,
                )
                await self.bot._notify_low_balance_if_needed(self.user_id)
                return

            raise RuntimeError("Modal de investimento desconhecido.")
        except Exception as exc:
            await self.bot._reply_text(
                interaction,
                title="Operacao nao concluida",
                description=str(exc),
                color=0xB22222,
                ephemeral=True,
            )


class InvestmentHubView(BaseInvestmentView):
    @discord.ui.button(label="📈 CDB", style=discord.ButtonStyle.primary, row=0)
    async def cdb_button(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        embed = make_bank_embed(
            "📈 CDB - Investimento travado",
            "Escolha o prazo do seu CDB. O valor sai do saldo bancario e fica travado ate o vencimento.",
            color=0x0B4EA2,
        )
        embed.add_field(name="3 dias", value="+5% no vencimento", inline=True)
        embed.add_field(name="7 dias", value="+12% no vencimento", inline=True)
        await interaction.response.edit_message(
            embed=embed,
            view=CDBOptionsView(self.bot, self.owner_id),
        )

    @discord.ui.button(label="🎲 Risco", style=discord.ButtonStyle.danger, row=0)
    async def risk_button(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        embed = make_bank_embed(
            "🎲 Investimento de risco",
            "Aqui o resultado e imediato. Escolha o nivel de risco e depois informe o valor.",
            color=0xA63A50,
        )
        embed.add_field(name="Baixo risco", value="Alta chance de lucro pequeno", inline=False)
        embed.add_field(name="Medio risco", value="Equilibrio entre ganho e perda", inline=False)
        embed.add_field(name="Alto risco", value="Baixa chance, lucro alto ou perda forte", inline=False)
        await interaction.response.edit_message(
            embed=embed,
            view=RiskOptionsView(self.bot, self.owner_id),
        )

    @discord.ui.button(label="🪙 Fundos", style=discord.ButtonStyle.secondary, row=0)
    async def fund_button(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        embed = make_bank_embed(
            "🪙 Fundos de investimento",
            "Escolha o perfil do fundo. O valor sera atualizado automaticamente pelo sistema.",
            color=0x8C6B00,
        )
        embed.add_field(name="Conservador", value="Variacao entre -1% e +2%", inline=False)
        embed.add_field(name="Moderado", value="Variacao entre -2% e +4%", inline=False)
        embed.add_field(name="Agressivo", value="Variacao entre -5% e +10%", inline=False)
        await interaction.response.edit_message(
            embed=embed,
            view=FundOptionsView(self.bot, self.owner_id),
        )

    @discord.ui.button(label="Acoes", emoji="📈", style=discord.ButtonStyle.primary, row=1)
    async def stocks_button(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        from src.market_ui import StockActionsView

        await interaction.response.edit_message(
            embed=self.bot.build_market_embed("stock"),
            view=StockActionsView(self.bot, self.owner_id),
        )

    @discord.ui.button(label="Crypto", emoji="🪙", style=discord.ButtonStyle.secondary, row=1)
    async def crypto_button(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        from src.market_ui import CryptoActionsView

        await interaction.response.edit_message(
            embed=self.bot.build_market_embed("crypto"),
            view=CryptoActionsView(self.bot, self.owner_id),
        )

    @discord.ui.button(label="Carteira", emoji="💼", style=discord.ButtonStyle.success, row=1)
    async def portfolio_button(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await interaction.response.edit_message(
            embed=self.bot.build_full_portfolio_embed(self.owner_id),
            view=self,
        )

    @discord.ui.button(label="Mercado", emoji="📊", style=discord.ButtonStyle.danger, row=1)
    async def live_market_button(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await interaction.response.edit_message(
            embed=self.bot.build_live_market_embed(),
            view=self,
        )


class BackToHubButton(discord.ui.Button):
    def __init__(self, bot: BancoSafraBot, owner_id: int) -> None:
        super().__init__(label="⬅️ Voltar", style=discord.ButtonStyle.secondary, row=2)
        self.bot = bot
        self.owner_id = owner_id

    async def callback(self, interaction: discord.Interaction) -> None:
        embed = self.bot.build_investment_hub_embed()
        await interaction.response.edit_message(
            embed=embed,
            view=InvestmentHubView(self.bot, self.owner_id),
        )


class CDBOptionsView(BaseInvestmentView):
    def __init__(self, bot: BancoSafraBot, owner_id: int) -> None:
        super().__init__(bot, owner_id)
        self.add_item(BackToHubButton(bot, owner_id))

    @discord.ui.button(label="3 dias (+5%)", style=discord.ButtonStyle.primary, row=0)
    async def option_3_days(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(
            InvestmentAmountModal(
                self.bot,
                self.owner_id,
                mode="cdb",
                option_key="cdb_3",
                title="Aplicar em CDB 3 dias",
            )
        )

    @discord.ui.button(label="7 dias (+12%)", style=discord.ButtonStyle.success, row=0)
    async def option_7_days(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(
            InvestmentAmountModal(
                self.bot,
                self.owner_id,
                mode="cdb",
                option_key="cdb_7",
                title="Aplicar em CDB 7 dias",
            )
        )


class RiskOptionsView(BaseInvestmentView):
    def __init__(self, bot: BancoSafraBot, owner_id: int) -> None:
        super().__init__(bot, owner_id)
        self.add_item(BackToHubButton(bot, owner_id))

    @discord.ui.button(label="Baixo risco", style=discord.ButtonStyle.success, row=0)
    async def low_risk(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(
            InvestmentAmountModal(
                self.bot,
                self.owner_id,
                mode="risco",
                option_key="baixo",
                title="Baixo risco",
            )
        )

    @discord.ui.button(label="Medio risco", style=discord.ButtonStyle.primary, row=0)
    async def medium_risk(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(
            InvestmentAmountModal(
                self.bot,
                self.owner_id,
                mode="risco",
                option_key="medio",
                title="Medio risco",
            )
        )

    @discord.ui.button(label="Alto risco", style=discord.ButtonStyle.danger, row=0)
    async def high_risk(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(
            InvestmentAmountModal(
                self.bot,
                self.owner_id,
                mode="risco",
                option_key="alto",
                title="Alto risco",
            )
        )


class FundOptionsView(BaseInvestmentView):
    def __init__(self, bot: BancoSafraBot, owner_id: int) -> None:
        super().__init__(bot, owner_id)
        self.add_item(BackToHubButton(bot, owner_id))

    @discord.ui.button(label="Conservador", style=discord.ButtonStyle.success, row=0)
    async def conservative_fund(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(
            InvestmentAmountModal(
                self.bot,
                self.owner_id,
                mode="fundo",
                option_key="conservador",
                title="Fundo Conservador",
            )
        )

    @discord.ui.button(label="Moderado", style=discord.ButtonStyle.primary, row=0)
    async def moderate_fund(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(
            InvestmentAmountModal(
                self.bot,
                self.owner_id,
                mode="fundo",
                option_key="moderado",
                title="Fundo Moderado",
            )
        )

    @discord.ui.button(label="Agressivo", style=discord.ButtonStyle.danger, row=0)
    async def aggressive_fund(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(
            InvestmentAmountModal(
                self.bot,
                self.owner_id,
                mode="fundo",
                option_key="agressivo",
                title="Fundo Agressivo",
            )
        )
