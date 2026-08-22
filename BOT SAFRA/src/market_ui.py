from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from src.utils import format_currency, make_bank_embed, parse_amount

if TYPE_CHECKING:
    from src.bot import BancoSafraBot


class MarketTradeModal(discord.ui.Modal):
    def __init__(
        self,
        bot: "BancoSafraBot",
        user_id: int,
        *,
        tipo: str,
        action: str,
        code: str,
    ) -> None:
        label = "Quantidade" if tipo == "stock" or action == "sell" else "Valor em reais"
        super().__init__(title=f"{action.title()} {code.upper()}")
        self.bot = bot
        self.user_id = user_id
        self.tipo = tipo
        self.action = action
        self.code = code.upper()
        self.value_input = discord.ui.TextInput(
            label=label,
            placeholder="Ex: 10 ou 25000",
            required=True,
            max_length=20,
        )
        self.add_item(self.value_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            await self.bot._defer_if_needed(interaction, ephemeral=False)
            raw_value = parse_amount(str(self.value_input.value))
            discount = self.bot.get_vip_discount(interaction.user)
            if self.tipo == "stock" and self.action == "buy":
                result = self.bot.market.buy_stock(
                    self.user_id,
                    self.code,
                    int(raw_value),
                    discount_rate=discount,
                )
                title = "📈 Compra de acoes concluida"
            elif self.tipo == "stock" and self.action == "sell":
                result = self.bot.market.sell_stock(self.user_id, self.code, int(raw_value))
                title = "📉 Venda de acoes concluida"
            elif self.tipo == "crypto" and self.action == "buy":
                result = self.bot.market.buy_crypto(
                    self.user_id,
                    self.code,
                    raw_value,
                    discount_rate=discount,
                )
                title = "🪙 Compra de crypto concluida"
            else:
                result = self.bot.market.sell_crypto(self.user_id, self.code, raw_value)
                title = "💱 Venda de crypto concluida"

            color = 0x1E8E5A if result.profit >= 0 else 0xB22222
            embed = make_bank_embed(
                title,
                "Operacao registrada na corretora Safra.",
                color=color,
            )
            embed.add_field(name="Ativo", value=f"**{result.asset['name']} ({result.asset['code']})**", inline=False)
            embed.add_field(name="Quantidade", value=f"`{result.quantity:.8f}`".rstrip("0").rstrip("."), inline=True)
            embed.add_field(name="Preco unitario", value=f"**{format_currency(float(result.asset['price']))}**", inline=True)
            embed.add_field(name="Valor total", value=f"**{format_currency(result.total)}**", inline=True)
            if self.action == "buy" and discount > 0:
                embed.add_field(
                    name="Desconto VIP",
                    value=f"**{discount * 100:.0f}% aplicado**",
                    inline=True,
                )
            if self.action == "sell":
                sign = "+" if result.profit >= 0 else "-"
                embed.add_field(name="Lucro / prejuizo", value=f"**{sign}{format_currency(abs(result.profit))}**", inline=False)

            await self.bot._reply_embed(interaction, embed)
            await self.bot.send_transaction_log(
                title=title,
                lines=[
                    f"Usuario: {interaction.user.mention}",
                    f"Ativo: **{result.asset['name']} ({result.asset['code']})**",
                    f"Quantidade: `{result.quantity:.8f}`",
                    f"Total: **{format_currency(result.total)}**",
                ],
                color=color,
            )
        except Exception as exc:
            await self.bot._reply_text(
                interaction,
                title="Operacao nao concluida",
                description=str(exc),
                color=0xB22222,
                ephemeral=True,
            )


class AssetSelect(discord.ui.Select):
    def __init__(self, bot: "BancoSafraBot", owner_id: int, *, tipo: str, action: str) -> None:
        self.bot = bot
        self.owner_id = owner_id
        self.tipo = tipo
        self.action = action
        assets = bot.market.list_assets(tipo)
        options = [
            discord.SelectOption(
                label=f"{asset['code']} - {asset['name']}"[:100],
                description=f"{format_currency(float(asset['price']))} | {float(asset['daily_change']):+.2f}%",
                value=str(asset["code"]),
            )
            for asset in assets[:25]
        ]
        super().__init__(
            placeholder="Selecione o ativo",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.owner_id:
            await self.bot._reply_text(
                interaction,
                title="Acesso negado",
                description="Esse painel pertence a outro usuario.",
                color=0xB22222,
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(
            MarketTradeModal(
                self.bot,
                self.owner_id,
                tipo=self.tipo,
                action=self.action,
                code=self.values[0],
            )
        )


class MarketTradeView(discord.ui.View):
    def __init__(self, bot: "BancoSafraBot", owner_id: int, *, tipo: str, action: str) -> None:
        super().__init__(timeout=180)
        self.add_item(AssetSelect(bot, owner_id, tipo=tipo, action=action))


class MarketHubView(discord.ui.View):
    def __init__(self, bot: "BancoSafraBot", owner_id: int) -> None:
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

    @discord.ui.button(label="Acoes", emoji="📈", style=discord.ButtonStyle.primary, row=0)
    async def stocks(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(embed=self.bot.build_market_embed("stock"), view=StockActionsView(self.bot, self.owner_id))

    @discord.ui.button(label="Criptomoedas", emoji="🪙", style=discord.ButtonStyle.secondary, row=0)
    async def crypto(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(embed=self.bot.build_market_embed("crypto"), view=CryptoActionsView(self.bot, self.owner_id))

    @discord.ui.button(label="Minha Carteira", emoji="💼", style=discord.ButtonStyle.success, row=1)
    async def portfolio(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(embed=self.bot.build_full_portfolio_embed(self.owner_id), view=self)

    @discord.ui.button(label="Mercado ao vivo", emoji="📊", style=discord.ButtonStyle.danger, row=1)
    async def live_market(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(embed=self.bot.build_live_market_embed(), view=self)


class StockActionsView(MarketHubView):
    @discord.ui.button(label="Comprar", emoji="🟢", style=discord.ButtonStyle.success, row=2)
    async def buy(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            embed=make_bank_embed("📈 Comprar acoes", "Selecione a empresa e informe a quantidade.", color=0x1E8E5A),
            view=MarketTradeView(self.bot, self.owner_id, tipo="stock", action="buy"),
        )

    @discord.ui.button(label="Vender", emoji="🔴", style=discord.ButtonStyle.danger, row=2)
    async def sell(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            embed=make_bank_embed("📉 Vender acoes", "Selecione a empresa e informe a quantidade.", color=0xB22222),
            view=MarketTradeView(self.bot, self.owner_id, tipo="stock", action="sell"),
        )


class CryptoActionsView(MarketHubView):
    @discord.ui.button(label="Comprar", emoji="🟢", style=discord.ButtonStyle.success, row=2)
    async def buy(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            embed=make_bank_embed("🪙 Comprar crypto", "Selecione a moeda e informe o valor em reais.", color=0x1E8E5A),
            view=MarketTradeView(self.bot, self.owner_id, tipo="crypto", action="buy"),
        )

    @discord.ui.button(label="Vender", emoji="🔴", style=discord.ButtonStyle.danger, row=2)
    async def sell(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            embed=make_bank_embed("💱 Vender crypto", "Selecione a moeda e informe a quantidade.", color=0xB22222),
            view=MarketTradeView(self.bot, self.owner_id, tipo="crypto", action="sell"),
        )
