from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Awaitable, Callable

import discord
from discord import app_commands
from discord.app_commands import AppCommandContext, AppInstallationType
from discord.ext import commands, tasks

from src.account_ui import AccountApprovalView, AccountCreationModal
from src.config import Settings, load_settings
from src.database import Database
from src.economy import EconomyService
from src.investment_ui import InvestmentHubView
from src.investments import FundUpdateResult, InvestmentService
from src.market_service import MarketService
from src.market_ui import MarketHubView, MarketTradeView
from src.notification_ui import NotificationsView, build_notifications_embed
from src.notifications import NotificationService, enviar_notificacao
from src.security_ui import PasswordModal
from src.utils import format_currency, format_datetime, log_transaction, make_bank_embed


FUND_CHOICES = [
    app_commands.Choice(name="Conservador", value="conservador"),
    app_commands.Choice(name="Moderado", value="moderado"),
    app_commands.Choice(name="Agressivo", value="agressivo"),
]

ADMIN_CONTEXT = AppCommandContext(guild=True, dm_channel=False, private_channel=False)
ADMIN_INSTALL = AppInstallationType(guild=True, user=False)

PASSWORD_AREA_CHOICES = [
    app_commands.Choice(name="Saldo", value="saldo"),
    app_commands.Choice(name="Investimentos", value="investimentos"),
]

ROLE_FUNCTION_CHOICES = [
    app_commands.Choice(name="Comandos administrativos", value="admin_role_id"),
    app_commands.Choice(name="VIP / descontos", value="vip_role_id"),
    app_commands.Choice(name="Cargo Cliente", value="cliente_role_id"),
    app_commands.Choice(name="Cargo Cliente VIP", value="cliente_vip_role_id"),
    app_commands.Choice(name="Cargo Cliente Empresarial", value="cliente_empresarial_role_id"),
]

CHANNEL_FUNCTION_CHOICES = [
    app_commands.Choice(name="Transacoes", value="transactions_channel_id"),
    app_commands.Choice(name="Canal de aprovacao de contas", value="account_approval_channel_id"),
]

CATEGORY_FUNCTION_CHOICES = [
    app_commands.Choice(name="Categoria das contas privadas", value="account_category_id"),
]

VOLATILITY_CHOICES = [
    app_commands.Choice(name="Baixa", value="baixa"),
    app_commands.Choice(name="Media", value="media"),
    app_commands.Choice(name="Alta", value="alta"),
]

ACCOUNT_WEEKLY_FEE = 10000.0
VIP_DISCOUNT_RATE = 0.10

ProtectedCallback = Callable[[discord.Interaction], Awaitable[None]]


class BancoSafraBot(commands.Bot):
    def __init__(self, settings: Settings, database: Database) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True

        super().__init__(
            command_prefix=settings.prefix,
            intents=intents,
            help_command=None,
        )
        self.settings = settings
        self.database = database
        self.economy = EconomyService(database)
        self.investments = InvestmentService(database)
        self.market = MarketService(database)
        self.notifications = NotificationService(database)
        self._commands_synced = False

    async def _resolve_target_members(
        self,
        interaction: discord.Interaction,
        usuario: discord.Member | None,
        cargo: discord.Role | None,
    ) -> list[discord.Member] | None:
        if usuario is not None and cargo is not None:
            await self._reply_text(
                interaction,
                title="Alvo invalido",
                description="Escolha apenas um usuario ou um cargo por vez.",
                color=0xB22222,
                ephemeral=True,
            )
            return None

        if usuario is not None:
            return [usuario]

        if cargo is not None:
            members = list(cargo.members)
            if not members:
                await self._reply_text(
                    interaction,
                    title="Cargo sem membros",
                    description="Esse cargo nao possui membros para receber a alteracao.",
                    color=0xB22222,
                    ephemeral=True,
                )
                return None
            return members

        await self._reply_text(
            interaction,
            title="Alvo nao informado",
            description="Informe um usuario ou um cargo para aplicar a alteracao.",
            color=0xB22222,
            ephemeral=True,
        )
        return None

    async def setup_hook(self) -> None:
        self._register_commands()
        self._register_persistent_views()
        self.tree.on_error = self.on_app_command_error
        if not self.automation_loop.is_running():
            self.automation_loop.start()
        if not self.stock_market_loop.is_running():
            self.stock_market_loop.start()
        if not self.crypto_market_loop.is_running():
            self.crypto_market_loop.start()
        if not self.dividend_loop.is_running():
            self.dividend_loop.start()

    async def on_ready(self) -> None:
        if not self._commands_synced:
            for guild in self.guilds:
                self.tree.clear_commands(guild=guild)
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                print(f"Slash commands sincronizados em {guild.name}: {len(synced)}.")
            self._commands_synced = True
        print(f"{self.settings.bot_name} conectado como {self.user}.")

    async def on_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await self._reply_text(
                interaction,
                title="Permissao negada",
                description="Apenas administradores podem usar este comando.",
                color=0xB22222,
                ephemeral=True,
            )
            return

        if isinstance(error, app_commands.CheckFailure):
            await self._reply_text(
                interaction,
                title="Acesso negado",
                description=(
                    "Voce precisa ser administrador ou possuir o cargo autorizado "
                    "em `/selecionar cargo` para usar este comando."
                ),
                color=0xB22222,
                ephemeral=True,
            )
            return

        original_error = getattr(error, "original", error)
        if isinstance(original_error, discord.NotFound):
            print(f"[Interacao] Ignorada: interacao expirada ou comando antigo. {original_error}")
            return
        try:
            await self._reply_text(
                interaction,
                title="Operacao nao concluida",
                description=str(original_error),
                color=0xB22222,
                ephemeral=True,
            )
        except discord.HTTPException as exc:
            if getattr(exc, "code", None) == 40060:
                print(f"[Interacao] Erro ignorado: interacao ja reconhecida. {exc}")
                return
            print(f"[Interacao] Falha ao responder erro: {exc}")

    @tasks.loop(hours=24)
    async def automation_loop(self) -> None:
        updated_funds = self.investments.update_funds()
        matured_cdbs = self.investments.check_matured_investments()
        debts = self.database.list_active_debts()
        due_profiles = self.database.list_due_account_fees(datetime.now(UTC).isoformat())

        if updated_funds:
            log_transaction(
                self.settings.log_path,
                f"FUNDS_UPDATE | atualizados={len(updated_funds)}",
            )

        for update in updated_funds:
            await self._handle_fund_update(update)

        for investment in matured_cdbs:
            await enviar_notificacao(
                self,
                int(investment["user_id"]),
                "investimento_liberado",
                (
                    f"⏰ Seu CDB #{investment['id']} esta pronto para resgate. "
                    f"Valor liberado: {format_currency(float(investment['valor_atual']))}."
                ),
                dedupe_key=f"cdb_liberado:{investment['id']}",
            )

        for debt in debts:
            await self._process_debt_notification(debt)

        for profile in due_profiles:
            await self._process_weekly_account_fee(profile)

    @automation_loop.before_loop
    async def before_automation_loop(self) -> None:
        await self.wait_until_ready()

    @tasks.loop(hours=24)
    async def stock_market_loop(self) -> None:
        updated = self.market.update_market("stock")
        log_transaction(
            self.settings.log_path,
            f"STOCK_MARKET_UPDATE | ativos={len(updated)}",
        )

    @stock_market_loop.before_loop
    async def before_stock_market_loop(self) -> None:
        await self.wait_until_ready()

    @tasks.loop(hours=24)
    async def crypto_market_loop(self) -> None:
        updated = self.market.update_market("crypto")
        log_transaction(
            self.settings.log_path,
            f"CRYPTO_MARKET_UPDATE | ativos={len(updated)}",
        )

    @crypto_market_loop.before_loop
    async def before_crypto_market_loop(self) -> None:
        await self.wait_until_ready()

    @tasks.loop(hours=12)
    async def dividend_loop(self) -> None:
        dividends = self.market.distribute_dividends()
        for item in dividends:
            await enviar_notificacao(
                self,
                int(item["user_id"]),
                "lucro_investimento",
                (
                    f"💵 Voce recebeu dividendos de {item['name']} "
                    f"no valor de {format_currency(float(item['amount']))}."
                ),
            )
        if dividends:
            await self.send_transaction_log(
                title="💵 Dividendos distribuidos",
                lines=[
                    f"Pagamentos realizados: **{len(dividends)}**",
                    "Os valores foram depositados automaticamente nas contas bancarias.",
                ],
                color=0x1E8E5A,
            )

    @dividend_loop.before_loop
    async def before_dividend_loop(self) -> None:
        await self.wait_until_ready()

    def run_from_env(self) -> None:
        self.run(self.settings.token)

    async def _reply_embed(
        self,
        interaction: discord.Interaction,
        embed: discord.Embed,
        *,
        view: discord.ui.View | None = None,
        ephemeral: bool = False,
    ) -> None:
        file = discord.File(self.settings.logo_path, filename="logo.png")
        kwargs: dict[str, object] = {
            "embed": embed,
            "file": file,
            "ephemeral": ephemeral,
        }
        if view is not None:
            kwargs["view"] = view

        if interaction.response.is_done():
            try:
                await interaction.followup.send(**kwargs)
            except discord.NotFound as exc:
                print(f"[Interacao] Followup expirado: {exc}")
            except discord.HTTPException as exc:
                if getattr(exc, "code", None) == 40060:
                    print(f"[Interacao] Followup falhou apos 40060: {exc}")
                    return
                raise
            return

        try:
            await interaction.response.send_message(**kwargs)
        except discord.NotFound as exc:
            print(f"[Interacao] Expirada antes da resposta: {exc}")
        except discord.HTTPException as exc:
            if getattr(exc, "code", None) == 40060:
                retry_kwargs = dict(kwargs)
                retry_kwargs["file"] = discord.File(
                    self.settings.logo_path,
                    filename="logo.png",
                )
                try:
                    await interaction.followup.send(**retry_kwargs)
                except discord.NotFound as followup_exc:
                    print(f"[Interacao] Followup expirado apos 40060: {followup_exc}")
                except discord.HTTPException as followup_exc:
                    if getattr(followup_exc, "code", None) == 40060:
                        print(f"[Interacao] Followup falhou apos 40060: {followup_exc}")
                        return
                    raise
                return
            print(f"[Interacao] HTTPException ao enviar resposta: {exc}")
            return

    async def _defer_if_needed(
        self,
        interaction: discord.Interaction,
        *,
        ephemeral: bool = False,
    ) -> None:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=ephemeral)

    async def _reply_text(
        self,
        interaction: discord.Interaction,
        *,
        title: str,
        description: str,
        color: int,
        ephemeral: bool = False,
    ) -> None:
        await self._reply_embed(
            interaction,
            make_bank_embed(title, description, color=color),
            ephemeral=ephemeral,
        )

    def hash_password(self, password: str) -> str:
        return hashlib.sha256(password.encode("utf-8")).hexdigest()

    def validate_password(self, user_id: int, area: str, password: str) -> bool:
        stored_hash = self.database.get_user_password(user_id, area)
        if stored_hash is None:
            return False
        return stored_hash == self.hash_password(password)

    async def _run_protected(
        self,
        interaction: discord.Interaction,
        *,
        area: str,
        callback: ProtectedCallback,
    ) -> None:
        if self.database.get_user_password(interaction.user.id, area) is None:
            await self._reply_text(
                interaction,
                title="Senha nao definida",
                description=(
                    "Defina uma senha antes de acessar essa area com "
                    "`/definir senha`."
                ),
                color=0xB22222,
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(
            PasswordModal(self, area=area, callback=callback)
        )

    def get_manager_id(self) -> int | None:
        raw = self.database.get_bot_setting("manager_user_id")
        return None if raw is None else int(raw)

    def get_transactions_channel_id(self) -> int | None:
        raw = self.database.get_bot_setting("transactions_channel_id")
        return None if raw is None else int(raw)

    def get_account_approval_channel_id(self) -> int | None:
        raw = self.database.get_bot_setting("account_approval_channel_id")
        return None if raw is None else int(raw)

    def get_account_category_id(self) -> int | None:
        raw = self.database.get_bot_setting("account_category_id")
        return None if raw is None else int(raw)

    def get_configured_role_id(self, key: str) -> int | None:
        raw = self.database.get_bot_setting(key)
        return None if raw is None else int(raw)

    def member_has_configured_role(self, member: discord.abc.User, key: str) -> bool:
        role_id = self.get_configured_role_id(key)
        if role_id is None or not isinstance(member, discord.Member):
            return False
        return any(role.id == role_id for role in member.roles)

    def is_admin_authorized(self, interaction: discord.Interaction) -> bool:
        user = interaction.user
        if isinstance(user, discord.Member) and user.guild_permissions.administrator:
            return True
        return self.member_has_configured_role(user, "admin_role_id")

    def get_vip_discount(self, member: discord.abc.User) -> float:
        if self.member_has_configured_role(member, "vip_role_id"):
            return VIP_DISCOUNT_RATE
        return 0.0

    async def send_transaction_log(
        self,
        *,
        title: str,
        lines: list[str],
        color: int = 0x0B4EA2,
    ) -> None:
        channel_id = self.get_transactions_channel_id()
        if channel_id is None:
            return

        channel = self.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.fetch_channel(channel_id)
            except discord.HTTPException as exc:
                print(f"[Transacoes] Falha ao buscar canal {channel_id}: {exc}")
                return

        if not isinstance(channel, discord.abc.Messageable):
            return

        embed = make_bank_embed(title, "\n".join(lines), color=color)
        file = discord.File(self.settings.logo_path, filename="logo.png")
        try:
            await channel.send(embed=embed, file=file)
        except discord.HTTPException as exc:
            print(f"[Transacoes] Falha ao enviar log no canal {channel_id}: {exc}")

    def _register_persistent_views(self) -> None:
        for profile in self.database.list_pending_account_profiles():
            self.add_view(AccountApprovalView(self, int(profile["user_id"])))

    def resolve_client_role_key(self, tipo_conta: str) -> str:
        normalized = tipo_conta.strip().lower()
        if any(keyword in normalized for keyword in ("vip", "premium")):
            return "cliente_vip_role_id"
        if any(keyword in normalized for keyword in ("empresarial", "empresa", "corporate")):
            return "cliente_empresarial_role_id"
        return "cliente_role_id"

    async def assign_client_role(
        self,
        guild: discord.Guild,
        member: discord.Member,
        tipo_conta: str,
    ) -> discord.Role | None:
        role_key = self.resolve_client_role_key(tipo_conta)
        role_id = self.get_configured_role_id(role_key)
        if role_id is None:
            return None
        role = guild.get_role(role_id)
        if role is None:
            return None
        try:
            await member.add_roles(role, reason="Conta bancaria aprovada no Banco Safra")
        except discord.HTTPException as exc:
            print(f"[Contas] Falha ao atribuir cargo {role.id} para {member.id}: {exc}")
            return None
        return role

    async def post_account_approval_request(
        self,
        *,
        guild: discord.Guild | None,
        member: discord.abc.User,
    ) -> None:
        if guild is None:
            print("[Contas] Solicitacao ignorada: comando usado fora de um servidor.")
            return

        channel_id = self.get_account_approval_channel_id()
        if channel_id is None:
            print("[Contas] Canal de aprovacao nao configurado.")
            return

        channel = guild.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.fetch_channel(channel_id)
            except discord.HTTPException as exc:
                print(f"[Contas] Falha ao buscar canal de aprovacao {channel_id}: {exc}")
                return

        if not isinstance(channel, discord.TextChannel):
            print(f"[Contas] Canal {channel_id} nao e um canal de texto.")
            return

        profile = self.database.get_account_profile(member.id)
        if profile is None:
            return

        embed = make_bank_embed(
            "Nova solicitacao de conta",
            "Revise os dados abaixo e aprove ou recuse a abertura da conta.",
            color=0x0B4EA2,
        )
        embed.add_field(name="Cliente", value=member.mention, inline=True)
        embed.add_field(name="Nome RP", value=str(profile["nome_completo"]), inline=True)
        embed.add_field(name="Tipo de conta", value=str(profile["tipo_conta"]), inline=True)
        embed.add_field(
            name="Deposito inicial",
            value=f"**{format_currency(float(profile['deposito_inicial']))}**",
            inline=True,
        )
        embed.add_field(
            name="Contato RP",
            value=str(profile["telefone_rp"] or "Nao informado"),
            inline=True,
        )
        embed.add_field(
            name="Solicitado em",
            value=format_datetime(str(profile["created_at"])),
            inline=True,
        )
        file = discord.File(self.settings.logo_path, filename="logo.png")
        view = AccountApprovalView(self, member.id)
        try:
            message = await channel.send(embed=embed, file=file, view=view)
            self.database.update_account_profile(
                member.id,
                approval_message_id=str(message.id),
            )
            self.add_view(view)
        except discord.HTTPException as exc:
            print(f"[Contas] Falha ao publicar solicitacao no canal {channel_id}: {exc}")

    async def create_account_private_channel(
        self,
        guild: discord.Guild,
        *,
        member: discord.Member,
        account_number: str,
        profile_name: str,
    ) -> discord.TextChannel:
        category_id = self.get_account_category_id()
        if category_id is None:
            raise ValueError(
                "A categoria das contas privadas nao foi configurada. "
                "Use `/selecionar categoria`."
            )

        category = guild.get_channel(category_id)
        if category is None:
            category = await guild.fetch_channel(category_id)
        if not isinstance(category, discord.CategoryChannel):
            raise ValueError("A categoria configurada para contas privadas e invalida.")

        overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            member: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                manage_channels=True,
                manage_messages=True,
            ),
        }
        admin_role_id = self.get_configured_role_id("admin_role_id")
        if admin_role_id is not None:
            admin_role = guild.get_role(admin_role_id)
            if admin_role is not None:
                overwrites[admin_role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                )

        channel_name = f"conta-{account_number}"
        return await guild.create_text_channel(
            name=channel_name,
            category=category,
            overwrites=overwrites,
            reason=f"Conta bancaria #{account_number} - {profile_name}",
        )

    async def finalize_account_approval(
        self,
        guild: discord.Guild | None,
        *,
        user_id: int,
        approved_by: discord.abc.User,
    ) -> None:
        if guild is None:
            raise ValueError("A aprovacao precisa ser feita dentro do servidor.")

        profile = self.database.get_account_profile(user_id)
        if profile is None or str(profile["status"]) != "pendente":
            raise ValueError("Esta solicitacao nao esta mais pendente.")

        member = guild.get_member(user_id)
        if member is None:
            member = await guild.fetch_member(user_id)

        account_number = self.database.generate_account_number()
        now = datetime.now(UTC)
        next_fee_at = now + timedelta(days=7)
        password_hash = str(profile["senha_rp_hash"])
        deposito_inicial = float(profile["deposito_inicial"])

        self.database.set_user_password(user_id, "saldo", password_hash)
        self.database.set_user_password(user_id, "investimentos", password_hash)
        self.database.update_balance(user_id, deposito_inicial)

        private_channel = await self.create_account_private_channel(
            guild,
            member=member,
            account_number=account_number,
            profile_name=str(profile["nome_completo"]),
        )
        assigned_role = await self.assign_client_role(
            guild,
            member,
            str(profile["tipo_conta"]),
        )

        self.database.update_account_profile(
            user_id,
            status="ativa",
            account_number=account_number,
            channel_id=str(private_channel.id),
            next_fee_at=next_fee_at.isoformat(),
        )
        profile = self.database.get_account_profile(user_id)
        if profile is None:
            raise RuntimeError("Falha ao carregar perfil apos aprovacao.")

        wallet = self.economy.get_wallet(user_id)
        balance = self.economy.get_balance(user_id)
        total = self.economy.get_total_balance(user_id)
        credit = self.economy.get_credit(user_id)

        welcome_embed = self._build_profile_embed(
            title="Conta aprovada - Banco Safra",
            member=member,
            profile=profile,
            wallet=wallet,
            balance=balance,
            total=total,
            credit=credit,
            color=0x1E8E5A,
        )
        welcome_embed.description = (
            f"Bem-vindo(a) ao Banco Safra! Seu canal privado de conta foi criado.\n"
            f"ID da conta: **#{account_number}**"
        )
        if assigned_role is not None:
            welcome_embed.add_field(
                name="Cargo atribuido",
                value=assigned_role.mention,
                inline=False,
            )
        file = discord.File(self.settings.logo_path, filename="logo.png")
        await private_channel.send(content=member.mention, embed=welcome_embed, file=file)

        await enviar_notificacao(
            self,
            user_id,
            "bonus",
            (
                f"Sua conta bancaria #{account_number} foi aprovada! "
                f"Acesse seu canal privado: <#{private_channel.id}>."
            ),
            title="Conta aprovada",
        )
        await self.send_transaction_log(
            title="Conta aprovada",
            lines=[
                f"Aprovada por: {approved_by.mention}",
                f"Cliente: {member.mention}",
                f"ID da conta: **#{account_number}**",
                f"Canal privado: <#{private_channel.id}>",
                f"Tipo de conta: **{profile['tipo_conta']}**",
                f"Deposito inicial: **{format_currency(deposito_inicial)}**",
            ],
            color=0x1E8E5A,
        )

    async def finalize_account_rejection(
        self,
        *,
        user_id: int,
        rejected_by: discord.abc.User,
    ) -> None:
        profile = self.database.get_account_profile(user_id)
        if profile is None or str(profile["status"]) != "pendente":
            return

        self.database.update_account_profile(user_id, status="recusada")
        await enviar_notificacao(
            self,
            user_id,
            "envio",
            (
                "Sua solicitacao de abertura de conta no Banco Safra foi recusada "
                "pela administracao. Voce pode enviar uma nova solicitacao se desejar."
            ),
            title="Conta recusada",
        )
        await self.send_transaction_log(
            title="Conta recusada",
            lines=[
                f"Recusada por: {rejected_by.mention}",
                f"Cliente: <@{user_id}>",
                f"Nome RP: **{profile['nome_completo']}**",
            ],
            color=0xB22222,
        )

    async def _credit_manager_loss(
        self,
        amount: float,
        *,
        source: str,
        actor_user_id: int | None = None,
    ) -> None:
        if amount <= 0:
            return
        manager_id = self.get_manager_id()
        if manager_id is None:
            return

        new_balance = self.database.update_balance(manager_id, amount)
        message = (
            f"🎁 A conta gerente recebeu {format_currency(amount)} "
            f"de perdas em {source}."
        )
        if actor_user_id is not None:
            message += f" Conta de origem: <@{actor_user_id}>."

        await enviar_notificacao(
            self,
            manager_id,
            "bonus",
            f"{message} Saldo atual do gerente: {format_currency(new_balance)}.",
        )
        await self.send_transaction_log(
            title="🎁 Credito para conta gerente",
            lines=[
                message,
                f"Saldo atual do gerente: **{format_currency(new_balance)}**",
            ],
            color=0x7C3AED,
        )

    async def _notify_low_balance_if_needed(self, user_id: int) -> None:
        current_balance = self.economy.get_balance(user_id)
        if current_balance >= self.settings.low_balance_alert:
            return

        await enviar_notificacao(
            self,
            user_id,
            "saldo_baixo",
            (
                f"📉 Seu saldo no banco esta abaixo do limite de alerta. "
                f"Saldo atual: {format_currency(current_balance)}."
            ),
            dedupe_key=f"saldo_baixo:{user_id}",
            dedupe_window=timedelta(hours=6),
        )

    async def _handle_fund_update(self, update: FundUpdateResult) -> None:
        if update.delta < 0:
            await self._credit_manager_loss(
                abs(update.delta),
                source=f"fundo #{update.investment['id']}",
                actor_user_id=int(update.investment["user_id"]),
            )

    async def _process_debt_notification(self, debt) -> None:
        due_at = datetime.fromisoformat(str(debt["vencimento"]))
        now = datetime.now(UTC)
        last_alert = str(debt["ultimo_alerta"] or "")

        if due_at <= now and last_alert != "vencida":
            sent = await enviar_notificacao(
                self,
                int(debt["user_id"]),
                "divida_vencida",
                (
                    f"🚨 Sua divida #{debt['id']} venceu. "
                    f"Valor em aberto: {format_currency(float(debt['valor']))}."
                ),
                dedupe_key=f"divida_vencida:{debt['id']}",
            )
            if sent:
                self.database.update_debt_alert(int(debt["id"]), "vencida")
            return

        if due_at - now <= timedelta(hours=24) and last_alert not in {"vencendo", "vencida"}:
            sent = await enviar_notificacao(
                self,
                int(debt["user_id"]),
                "divida_vencendo",
                (
                    f"⚠️ Sua divida #{debt['id']} vence em breve. "
                    f"Valor: {format_currency(float(debt['valor']))} | "
                    f"Vencimento: {format_datetime(str(debt['vencimento']))}."
                ),
                dedupe_key=f"divida_vencendo:{debt['id']}",
            )
            if sent:
                self.database.update_debt_alert(int(debt["id"]), "vencendo")

    async def _process_weekly_account_fee(self, profile) -> None:
        manager_id = self.get_manager_id()
        if manager_id is None:
            print("[Conta] Cobranca semanal ignorada: conta gerente nao configurada.")
            return

        user_id = int(profile["user_id"])
        due_at = datetime.fromisoformat(str(profile["next_fee_at"]))
        now = datetime.now(UTC)
        next_fee_at = due_at
        while next_fee_at <= now:
            next_fee_at += timedelta(days=7)

        current_balance = self.economy.get_balance(user_id)
        charged = min(current_balance, ACCOUNT_WEEKLY_FEE)
        remaining = round(ACCOUNT_WEEKLY_FEE - charged, 2)

        if charged > 0:
            self.database.update_balance(user_id, -charged)
            manager_balance = self.database.update_balance(manager_id, charged)
            await enviar_notificacao(
                self,
                manager_id,
                "bonus",
                (
                    f"💼 A conta gerente recebeu {format_currency(charged)} "
                    f"da tarifa semanal da conta de <@{user_id}>. "
                    f"Saldo atual do gerente: {format_currency(manager_balance)}."
                ),
            )

        debt_text = "Nenhuma pendencia foi gerada."
        if remaining > 0:
            debt_id = self.database.create_debt(
                user_id=user_id,
                valor=remaining,
                vencimento=(now + timedelta(days=3)).isoformat(),
            )
            debt_text = (
                f"Foi gerada a divida **#{debt_id}** no valor de "
                f"**{format_currency(remaining)}**."
            )

        self.database.update_account_fee_date(user_id, next_fee_at.isoformat())

        await enviar_notificacao(
            self,
            user_id,
            "envio",
            (
                f"🏦 A tarifa semanal da sua conta foi processada.\n"
                f"Valor da tarifa: {format_currency(ACCOUNT_WEEKLY_FEE)}\n"
                f"Valor debitado: {format_currency(charged)}\n"
                f"{debt_text}"
            ),
            title="🏦 Tarifa semanal da conta",
            dedupe_key=f"tarifa_conta:{user_id}:{due_at.isoformat()}",
        )
        await self.send_transaction_log(
            title="🏦 Tarifa semanal processada",
            lines=[
                f"Cliente: <@{user_id}>",
                f"Tarifa prevista: **{format_currency(ACCOUNT_WEEKLY_FEE)}**",
                f"Valor debitado: **{format_currency(charged)}**",
                debt_text,
                f"Conta gerente: <@{manager_id}>",
            ],
            color=0x0B4EA2,
        )
        await self._notify_low_balance_if_needed(user_id)

    def build_investment_hub_embed(self) -> discord.Embed:
        embed = make_bank_embed(
            "Central de Investimentos - Banco Safra",
            (
                "Escolha uma modalidade para investir o saldo do banco com "
                "seguranca, risco calculado ou fundos de longo prazo."
            ),
            color=0x123E7C,
        )
        embed.add_field(
            name="\U0001F4C8 CDB travado",
            value="Prazo fixo com retorno previsivel.",
            inline=False,
        )
        embed.add_field(
            name="\U0001F3B2 Investimento de risco",
            value="Resultado imediato com chance de lucro ou perda.",
            inline=False,
        )
        embed.add_field(
            name="\U0001FA99 Fundos",
            value="Carteiras com variacao automatica ao longo do tempo.",
            inline=False,
        )
        embed.add_field(
            name="📈 Acoes",
            value="Bolsa de valores RP com empresas do servidor.",
            inline=False,
        )
        embed.add_field(
            name="🪙 Criptomoedas",
            value="Mercado crypto volátil com Safra Coin e moedas RP.",
            inline=False,
        )
        return embed

    def build_market_hub_embed(self) -> discord.Embed:
        embed = make_bank_embed(
            "📊 Central de Investimentos Safra",
            "Escolha uma area da corretora para acompanhar mercado, comprar ativos ou consultar sua carteira.",
            color=0x0B4EA2,
        )
        embed.add_field(name="📈 Acoes", value="Empresas RP com cotacao dinamica.", inline=True)
        embed.add_field(name="🪙 Criptomoedas", value="Mercado de alta volatilidade.", inline=True)
        embed.add_field(name="💼 Minha Carteira", value="Resumo de CDB, fundos, acoes e crypto.", inline=True)
        embed.add_field(name="📊 Mercado ao vivo", value="Cotacoes e variacoes recentes.", inline=False)
        return embed

    def build_market_embed(self, tipo: str) -> discord.Embed:
        is_crypto = tipo == "crypto"
        title = "🪙 Mercado de Criptomoedas" if is_crypto else "📈 Bolsa de Valores Safra"
        description = (
            "Cotacoes atualizadas automaticamente a cada 24 horas."
            if is_crypto
            else "Cotacoes atualizadas automaticamente a cada 24 horas."
        )
        embed = make_bank_embed(title, description, color=0x8C6B00 if is_crypto else 0x0B4EA2)
        for asset in self.market.list_assets(tipo):
            change = float(asset["daily_change"])
            icon = "🟢" if change >= 0 else "🔴"
            embed.add_field(
                name=f"{icon} {asset['name']} ({asset['code']})",
                value=(
                    f"Preco: **{format_currency(float(asset['price']))}**\n"
                    f"Variacao: **{change:+.2f}%**\n"
                    f"Market Cap: **{format_currency(float(asset['market_cap']))}**"
                ),
                inline=True,
            )
        return embed

    def build_asset_details_embed(self, code: str) -> discord.Embed:
        asset = self.market.get_asset(code)
        if asset is None:
            raise ValueError("Ativo nao encontrado.")
        history = list(reversed(self.market.market_history(str(asset["code"]), limit=10)))
        chart_points = []
        for row in history:
            change = float(row["change_percent"])
            chart_points.append("▰" if change >= 0 else "▱")
        chart = "".join(chart_points) or "Sem historico"
        change = float(asset["daily_change"])
        embed = make_bank_embed(
            f"🔎 {asset['name']} ({asset['code']})",
            "Detalhes do ativo e historico visual simplificado.",
            color=0x1E8E5A if change >= 0 else 0xB22222,
        )
        embed.add_field(name="Preco atual", value=f"**{format_currency(float(asset['price']))}**", inline=True)
        embed.add_field(name="Variacao", value=f"**{change:+.2f}%**", inline=True)
        embed.add_field(name="Volume", value=f"**{float(asset['volume']):,.2f}**", inline=True)
        embed.add_field(name="Grafico recente", value=f"`{chart}`", inline=False)
        return embed

    def build_wallet_embed(self, user_id: int, tipo: str) -> discord.Embed:
        rows = self.market.list_wallet(user_id, tipo)
        title = "💼 Carteira de Crypto" if tipo == "crypto" else "💼 Carteira de Acoes"
        embed = make_bank_embed(title, "Posicoes atuais na corretora Safra.", color=0x0B4EA2)
        if not rows:
            embed.description = "Nenhum ativo encontrado nessa carteira."
            return embed
        total_current = 0.0
        total_profit = 0.0
        for row in rows:
            quantity = float(row["quantity"])
            average = float(row["average_price"])
            price = float(row["price"])
            current = quantity * price
            profit = current - (quantity * average)
            total_current += current
            total_profit += profit
            sign = "+" if profit >= 0 else "-"
            embed.add_field(
                name=f"{row['name']} ({row['code']})",
                value=(
                    f"Qtd: `{quantity:.8f}`\n"
                    f"Preco medio: **{format_currency(average)}**\n"
                    f"Preco atual: **{format_currency(price)}**\n"
                    f"Resultado: **{sign}{format_currency(abs(profit))}**"
                ),
                inline=False,
            )
        embed.add_field(name="Valor atual", value=f"**{format_currency(total_current)}**", inline=True)
        embed.add_field(name="Lucro / prejuizo", value=f"**{format_currency(total_profit)}**", inline=True)
        return embed

    def build_live_market_embed(self) -> discord.Embed:
        embed = make_bank_embed(
            "📊 Mercado ao vivo",
            "Resumo rapido das ultimas cotacoes da corretora Safra.",
            color=0x0B4EA2,
        )
        for tipo in ("stock", "crypto"):
            assets = sorted(
                self.market.list_assets(tipo),
                key=lambda row: abs(float(row["daily_change"])),
                reverse=True,
            )[:5]
            label = "Acoes" if tipo == "stock" else "Crypto"
            value = "\n".join(
                f"`{asset['code']}` {format_currency(float(asset['price']))} ({float(asset['daily_change']):+.2f}%)"
                for asset in assets
            )
            embed.add_field(name=label, value=value or "Sem ativos.", inline=False)
        return embed

    def build_market_history_embed(self, code: str | None = None) -> discord.Embed:
        rows = self.market.market_history(code, limit=12)
        embed = make_bank_embed(
            "📜 Historico de mercado",
            "Ultimas variacoes e eventos registrados.",
            color=0x0B4EA2,
        )
        if not rows:
            embed.description = "Nenhum historico encontrado."
            return embed
        for row in rows:
            embed.add_field(
                name=f"{row['code']} | {float(row['change_percent']):+.2f}%",
                value=(
                    f"Preco: **{format_currency(float(row['price']))}**\n"
                    f"Evento: **{row['event_name'] or 'Mercado regular'}**\n"
                    f"Data: {format_datetime(str(row['created_at']))}"
                ),
                inline=True,
            )
        return embed

    def build_full_portfolio_embed(self, user_id: int) -> discord.Embed:
        summary = self.market.portfolio_summary(user_id)
        active_investments = self.investments.get_all_active_investments(user_id)
        traditional_current = sum(float(row["valor_atual"]) for row in active_investments)
        traditional_initial = sum(float(row["valor_inicial"]) for row in active_investments)
        invested = summary["invested"] + traditional_initial
        current = summary["current"] + traditional_current
        profit = current - invested
        profitability = (profit / invested * 100) if invested else 0.0
        embed = make_bank_embed(
            "💼 Minha Carteira Safra",
            "Resumo consolidado de CDB, fundos, acoes e criptomoedas.",
            color=0x1E8E5A if profit >= 0 else 0xB22222,
        )
        embed.add_field(name="Valor investido", value=f"**{format_currency(invested)}**", inline=True)
        embed.add_field(name="Valor atual", value=f"**{format_currency(current)}**", inline=True)
        embed.add_field(name="Lucro total", value=f"**{format_currency(profit)}**", inline=True)
        embed.add_field(name="Rentabilidade", value=f"**{profitability:+.2f}%**", inline=True)
        embed.add_field(name="CDB/Fundos ativos", value=f"`{len(active_investments)}`", inline=True)
        embed.add_field(
            name="Acoes + Crypto",
            value=f"**{format_currency(summary['current'])}**",
            inline=True,
        )
        return embed

    def build_investor_ranking_embed(self) -> discord.Embed:
        rows = self.market.ranking_investors()
        embed = make_bank_embed(
            "🏆 Ranking de investidores",
            "Top investidores por valor atual em acoes e criptomoedas.",
            color=0x8C6B00,
        )
        if not rows:
            embed.description = "Ainda nao ha investidores no ranking."
            return embed
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for index, row in enumerate(rows, start=1):
            medal = medals[index - 1] if index <= 3 else f"`#{index}`"
            lines.append(f"{medal} <@{row['user_id']}> - **{format_currency(float(row['total_value']))}**")
        embed.add_field(name="Classificacao", value="\n".join(lines), inline=False)
        return embed

    def build_notifications_embed(self, user_id: int) -> discord.Embed:
        return build_notifications_embed(self, user_id)

    def _build_account_embed(
        self,
        *,
        title: str,
        member: discord.abc.User,
        wallet: float,
        balance: float,
        total: float,
        color: int,
        credit: float | None = None,
    ) -> discord.Embed:
        embed = make_bank_embed(
            title,
            "Painel financeiro atualizado com sucesso.",
            color=color,
        )
        embed.add_field(name="\U0001F464 Cliente", value=member.mention, inline=False)
        embed.add_field(
            name="\U0001F4B5 Em maos / fisico",
            value=f"**{format_currency(wallet)}**",
            inline=True,
        )
        embed.add_field(
            name="\U0001F3E6 Depositado no banco",
            value=f"**{format_currency(balance)}**",
            inline=True,
        )
        embed.add_field(
            name="\U0001F4A0 Patrimonio total",
            value=f"**{format_currency(total)}**",
            inline=False,
        )
        if credit is not None:
            embed.add_field(
                name="\U0001F4B3 Credito disponivel",
                value=f"**{format_currency(credit)}**",
                inline=False,
            )
        return embed

    def _build_action_embed(
        self,
        *,
        title: str,
        color: int,
        lines: list[str],
    ) -> discord.Embed:
        return make_bank_embed(title, "\n".join(lines), color=color)

    def _build_profile_embed(
        self,
        *,
        title: str,
        member: discord.abc.User,
        profile,
        wallet: float,
        balance: float,
        total: float,
        credit: float,
        color: int,
    ) -> discord.Embed:
        embed = make_bank_embed(
            title,
            "Painel completo da conta bancaria RP.",
            color=color,
        )
        embed.add_field(name="📛 Nome RP", value=str(profile["nome_completo"]), inline=False)
        embed.add_field(name="👤 Cliente", value=member.mention, inline=True)
        embed.add_field(name="🪪 ID do Discord", value=f"`{profile['discord_id']}`", inline=True)
        if profile["account_number"]:
            embed.add_field(
                name="🔢 ID da conta",
                value=f"**#{profile['account_number']}**",
                inline=True,
            )
        embed.add_field(name="🎯 Tipo de conta", value=str(profile["tipo_conta"]).title(), inline=True)
        embed.add_field(
            name="📱 Contato RP",
            value=str(profile["telefone_rp"] or "Nao informado"),
            inline=True,
        )
        embed.add_field(
            name="📅 Conta criada",
            value=format_datetime(str(profile["created_at"])),
            inline=True,
        )
        embed.add_field(
            name="⏰ Proxima tarifa semanal",
            value=format_datetime(str(profile["next_fee_at"])),
            inline=True,
        )
        embed.add_field(
            name="🟢 Status",
            value=str(profile["status"]).title(),
            inline=True,
        )
        embed.add_field(
            name="💵 Em maos / fisico",
            value=f"**{format_currency(wallet)}**",
            inline=True,
        )
        embed.add_field(
            name="🏦 Depositado no banco",
            value=f"**{format_currency(balance)}**",
            inline=True,
        )
        embed.add_field(
            name="💠 Patrimonio total",
            value=f"**{format_currency(total)}**",
            inline=True,
        )
        embed.add_field(
            name="💳 Credito disponivel",
            value=f"**{format_currency(credit)}**",
            inline=True,
        )
        embed.add_field(
            name="💼 Deposito inicial",
            value=f"**{format_currency(float(profile['deposito_inicial']))}**",
            inline=True,
        )
        embed.add_field(
            name="🔐 Seguranca",
            value="Saldo e investimentos protegidos por senha.",
            inline=True,
        )
        return embed

    def _build_investments_overview_embed(self, user_id: int) -> discord.Embed:
        investments = self.investments.get_all_active_investments(user_id)
        embed = make_bank_embed(
            "\U0001F4DA Carteira de investimentos",
            "Resumo dos seus investimentos ativos.",
            color=0x1F3C88,
        )
        if not investments:
            embed.description = "Voce nao possui investimentos ativos no momento."
            return embed

        for investment in investments[:10]:
            if investment["tipo"] == "cdb":
                remaining = self.investments.investment_remaining(investment)
                status = (
                    "Liberado"
                    if remaining is not None and remaining.total_seconds() == 0
                    else self.investments.format_remaining_time(remaining or timedelta(0))
                )
                embed.add_field(
                    name=f"\U0001F4C8 CDB #{investment['id']}",
                    value=(
                        f"Valor atual: **{format_currency(float(investment['valor_atual']))}**\n"
                        f"Resgate: **{format_datetime(str(investment['data_resgate']))}**\n"
                        f"Tempo restante: **{status}**"
                    ),
                    inline=False,
                )
            elif investment["tipo"] == "fundo":
                delta, percent = self.investments.describe_fund_performance(investment)
                sign = "+" if delta >= 0 else "-"
                embed.add_field(
                    name=f"\U0001FA99 Fundo #{investment['id']} - {str(investment['subtipo']).title()}",
                    value=(
                        f"Atual: **{format_currency(float(investment['valor_atual']))}**\n"
                        f"Variacao: **{sign}{format_currency(abs(delta))} ({sign}{abs(percent):.2f}%)**\n"
                        f"Inicio: **{format_datetime(str(investment['data_inicio']))}**"
                    ),
                    inline=False,
                )
        return embed

    def _build_fund_status_embed(self, user_id: int) -> discord.Embed:
        funds = self.investments.list_user_funds(user_id)
        embed = make_bank_embed(
            "\U0001FA99 Status dos fundos",
            "Acompanhe o desempenho dos seus fundos ativos.",
            color=0x8C6B00,
        )
        if not funds:
            embed.description = "Voce nao possui fundos ativos no momento."
            return embed

        for fund in funds[:10]:
            delta, percent = self.investments.describe_fund_performance(fund)
            sign = "+" if delta >= 0 else "-"
            embed.add_field(
                name=f"Fundo #{fund['id']} - {str(fund['subtipo']).title()}",
                value=(
                    f"Aplicado: **{format_currency(float(fund['valor_inicial']))}**\n"
                    f"Atual: **{format_currency(float(fund['valor_atual']))}**\n"
                    f"Resultado: **{sign}{format_currency(abs(delta))} ({sign}{abs(percent):.2f}%)**"
                ),
                inline=False,
            )
        return embed

    def _build_help_embed(self) -> discord.Embed:
        embed = make_bank_embed(
            "\U0001F4D8 Painel de comandos",
            "Central de comandos do Banco Safra BOT.",
            color=0x0B4EA2,
        )
        embed.add_field(
            name="\U0001F512 Seguranca",
            value="`/criar_conta`\n`/conta`\n`/definir senha`",
            inline=True,
        )
        embed.add_field(
            name="\U0001F4B5 Economia",
            value="`/depositar`\n`/sacar`\n`/pagar`\n`/saldo`\n`/credito`",
            inline=True,
        )
        embed.add_field(
            name="\U0001F4C8 Investimentos",
            value=(
                "`/investir`\n`/resgatar`\n`/investimentos`\n"
                "`/fundo investir`\n`/fundo status`\n`/fundo sacar`\n"
                "`/acoes mercado`\n`/crypto mercado`\n`/carteira`"
            ),
            inline=True,
        )
        embed.add_field(
            name="\U0001F514 Notificacoes",
            value="`/notificacoes`",
            inline=True,
        )
        embed.add_field(
            name="\U0001F6E0\ufe0f Administracao",
            value=(
                "`/addmoney`\n`/removemoney`\n`/addcredito`\n"
                "`/removecredito`\n`/enviarcrypto`\n`/removerinvestimento`\n"
                "`/resetar_economia`\n`/resetar_investimentos`\n`/resetar_valores`\n"
                "`/fecharconta`\n`/investimentos_admin`\n`/consultar saldo`\n"
                "`/consultar conta`\n`/gerente conta`\n"
                "`/canal transacoes`\n`/canal contas`\n"
                "`/selecionar cargo`\n`/selecionar canal`\n"
                "`/selecionar categoria`\n"
                "`/acoes criar`\n`/crypto criar`"
            ),
            inline=False,
        )
        return embed

    def _register_commands(self) -> None:
        consultar_group = app_commands.Group(
            name="consultar",
            description="Comandos administrativos de consulta.",
        )
        fundo_group = app_commands.Group(
            name="fundo",
            description="Comandos de fundos de investimento.",
        )
        canal_group = app_commands.Group(
            name="canal",
            description="Configuracoes de canais do bot.",
        )
        gerente_group = app_commands.Group(
            name="gerente",
            description="Configuracoes administrativas do gerente.",
        )
        definir_group = app_commands.Group(
            name="definir",
            description="Comandos de configuracao pessoal.",
        )
        acoes_group = app_commands.Group(
            name="acoes",
            description="Bolsa de valores Safra.",
        )
        crypto_group = app_commands.Group(
            name="crypto",
            description="Mercado de criptomoedas Safra.",
        )
        mercado_group = app_commands.Group(
            name="mercado",
            description="Historico e dados do mercado Safra.",
        )
        ranking_group = app_commands.Group(
            name="ranking",
            description="Rankings financeiros do Banco Safra.",
        )
        selecionar_group = app_commands.Group(
            name="selecionar",
            description="Seleciona cargos, canais e categorias usados pelo Banco Safra.",
        )

        @self.tree.command(name="depositar", description="Deposita dinheiro que esta em maos.")
        async def depositar(
            interaction: discord.Interaction,
            valor: app_commands.Range[float, 0.01, None],
        ) -> None:
            async def action(inner: discord.Interaction) -> None:
                amount = round(float(valor), 2)
                result = self.economy.deposit(inner.user.id, amount)
                log_transaction(
                    self.settings.log_path,
                    f"DEPOSITO | user={inner.user.id} | valor={amount:.2f}",
                )
                embed = self._build_action_embed(
                    title="\u2705 Deposito aprovado",
                    color=0x137D3E,
                    lines=[
                        f"\U0001F4B0 Valor depositado: **{format_currency(amount)}**",
                        f"\U0001F4B5 Em maos agora: **{format_currency(result.wallet or 0)}**",
                        f"\U0001F3E6 No banco agora: **{format_currency(result.balance or 0)}**",
                    ],
                )
                await self._reply_embed(inner, embed)
                await self.send_transaction_log(
                    title="✅ Deposito registrado",
                    lines=[
                        f"Usuario: {inner.user.mention}",
                        f"Valor depositado: **{format_currency(amount)}**",
                        f"No banco agora: **{format_currency(result.balance or 0)}**",
                    ],
                    color=0x137D3E,
                )
                await self._notify_low_balance_if_needed(inner.user.id)

            await self._run_protected(interaction, area="saldo", callback=action)

        @self.tree.command(name="sacar", description="Saca dinheiro do banco para sua mao.")
        async def sacar(
            interaction: discord.Interaction,
            valor: app_commands.Range[float, 0.01, None],
        ) -> None:
            async def action(inner: discord.Interaction) -> None:
                amount = round(float(valor), 2)
                result = self.economy.withdraw(inner.user.id, amount)
                log_transaction(
                    self.settings.log_path,
                    f"SAQUE | user={inner.user.id} | valor={amount:.2f}",
                )
                embed = self._build_action_embed(
                    title="\U0001F4B8 Saque aprovado",
                    color=0xC97C00,
                    lines=[
                        f"\U0001F3E7 Valor sacado: **{format_currency(amount)}**",
                        f"\U0001F4B5 Em maos agora: **{format_currency(result.wallet or 0)}**",
                        f"\U0001F3E6 No banco agora: **{format_currency(result.balance or 0)}**",
                    ],
                )
                await self._reply_embed(inner, embed)
                await self.send_transaction_log(
                    title="💸 Saque registrado",
                    lines=[
                        f"Usuario: {inner.user.mention}",
                        f"Valor sacado: **{format_currency(amount)}**",
                        f"Em maos agora: **{format_currency(result.wallet or 0)}**",
                    ],
                    color=0xC97C00,
                )

            await self._run_protected(interaction, area="saldo", callback=action)

        @self.tree.command(
            name="pagar",
            description="Transfere dinheiro que esta em maos para outro usuario.",
        )
        async def pagar(
            interaction: discord.Interaction,
            usuario: discord.Member,
            valor: app_commands.Range[float, 0.01, None],
        ) -> None:
            async def action(inner: discord.Interaction) -> None:
                amount = round(float(valor), 2)
                result = self.economy.pay(inner.user.id, usuario.id, amount)
                log_transaction(
                    self.settings.log_path,
                    (
                        f"PAGAMENTO | de={inner.user.id} | para={usuario.id} "
                        f"| valor={amount:.2f}"
                    ),
                )
                embed = self._build_action_embed(
                    title="\U0001F91D Transferencia concluida",
                    color=0x0B4EA2,
                    lines=[
                        f"\U0001F464 Destinatario: {usuario.mention}",
                        f"\U0001F4B8 Valor enviado: **{format_currency(amount)}**",
                        f"\U0001F4B5 Em maos agora: **{format_currency(result.wallet or 0)}**",
                    ],
                )
                await self._reply_embed(inner, embed)
                await self.send_transaction_log(
                    title="🤝 Transferencia registrada",
                    lines=[
                        f"Remetente: {inner.user.mention}",
                        f"Destino: {usuario.mention}",
                        f"Valor: **{format_currency(amount)}**",
                    ],
                )
                await enviar_notificacao(
                    self,
                    inner.user.id,
                    "envio",
                    f"💸 Seu pagamento de {format_currency(amount)} para {usuario.mention} foi confirmado.",
                )
                await enviar_notificacao(
                    self,
                    usuario.id,
                    "recebimento",
                    f"💰 Voce recebeu {format_currency(amount)} de {inner.user.mention}.",
                )

            await self._run_protected(interaction, area="saldo", callback=action)

        @self.tree.command(name="saldo", description="Mostra dinheiro em maos, banco e total.")
        async def saldo(interaction: discord.Interaction) -> None:
            async def action(inner: discord.Interaction) -> None:
                wallet = self.economy.get_wallet(inner.user.id)
                balance = self.economy.get_balance(inner.user.id)
                total = self.economy.get_total_balance(inner.user.id)
                embed = self._build_account_embed(
                    title="\U0001F4CA Saldo atual",
                    member=inner.user,
                    wallet=wallet,
                    balance=balance,
                    total=total,
                    color=0x0B4EA2,
                )
                await self._reply_embed(inner, embed)

            await self._run_protected(interaction, area="saldo", callback=action)

        @self.tree.command(name="credito", description="Mostra o seu credito atual.")
        async def credito(interaction: discord.Interaction) -> None:
            async def action(inner: discord.Interaction) -> None:
                credit = self.economy.get_credit(inner.user.id)
                embed = make_bank_embed(
                    "\U0001F4B3 Credito atual",
                    "Limite consultado com sucesso.",
                    color=0x8C6B00,
                )
                embed.add_field(
                    name="\U0001F464 Cliente",
                    value=inner.user.mention,
                    inline=False,
                )
                embed.add_field(
                    name="\U0001F4B3 Credito disponivel",
                    value=f"**{format_currency(credit)}**",
                    inline=False,
                )
                await self._reply_embed(inner, embed)

            await self._run_protected(interaction, area="saldo", callback=action)

        @self.tree.command(
            name="criar_conta",
            description="Abre o questionario de abertura da sua conta RP.",
        )
        async def criar_conta(interaction: discord.Interaction) -> None:
            await interaction.response.send_modal(AccountCreationModal(self))

        @self.tree.command(
            name="conta",
            description="Abre o painel completo da sua conta RP.",
        )
        async def conta(interaction: discord.Interaction) -> None:
            profile = self.database.get_account_profile(interaction.user.id)
            if profile is None:
                await self._reply_text(
                    interaction,
                    title="Conta nao encontrada",
                    description=(
                        "Voce ainda nao possui conta cadastrada. "
                        "Use `/criar_conta` para iniciar o cadastro."
                    ),
                    color=0xB22222,
                    ephemeral=True,
                )
                return

            if str(profile["status"]) == "pendente":
                await self._reply_text(
                    interaction,
                    title="Conta em analise",
                    description=(
                        "Sua solicitacao de abertura de conta ainda esta aguardando "
                        "aprovacao dos administradores."
                    ),
                    color=0xC97C00,
                    ephemeral=True,
                )
                return

            if str(profile["status"]) == "recusada":
                await self._reply_text(
                    interaction,
                    title="Conta recusada",
                    description=(
                        "Sua solicitacao anterior foi recusada. "
                        "Use `/criar_conta` para enviar uma nova solicitacao."
                    ),
                    color=0xB22222,
                    ephemeral=True,
                )
                return

            async def action(inner: discord.Interaction) -> None:
                wallet = self.economy.get_wallet(inner.user.id)
                balance = self.economy.get_balance(inner.user.id)
                total = self.economy.get_total_balance(inner.user.id)
                credit = self.economy.get_credit(inner.user.id)
                embed = self._build_profile_embed(
                    title="🏦 Painel da conta",
                    member=inner.user,
                    profile=profile,
                    wallet=wallet,
                    balance=balance,
                    total=total,
                    credit=credit,
                    color=0x0B4EA2,
                )
                await self._reply_embed(inner, embed, ephemeral=True)

            await self._run_protected(interaction, area="saldo", callback=action)

        @self.tree.command(
            name="notificacoes",
            description="Gerencia suas notificacoes inteligentes por DM.",
        )
        async def notificacoes(interaction: discord.Interaction) -> None:
            async def action(inner: discord.Interaction) -> None:
                await self._reply_embed(
                    inner,
                    self.build_notifications_embed(inner.user.id),
                    view=NotificationsView(self, inner.user.id),
                )

            await self._run_protected(interaction, area="saldo", callback=action)

        @self.tree.command(
            name="investir",
            description="Abre a central de investimentos do Banco Safra.",
        )
        async def investir(interaction: discord.Interaction) -> None:
            async def action(inner: discord.Interaction) -> None:
                await self._reply_embed(
                    inner,
                    self.build_investment_hub_embed(),
                    view=InvestmentHubView(self, inner.user.id),
                )

            await self._run_protected(interaction, area="investimentos", callback=action)

        @self.tree.command(
            name="resgatar",
            description="Resgata um CDB vencido pelo ID.",
        )
        async def resgatar(
            interaction: discord.Interaction,
            investimento_id: int | None = None,
        ) -> None:
            async def action(inner: discord.Interaction) -> None:
                investment = self.investments.redeem_cdb(inner.user.id, investimento_id)
                profit = round(
                    float(investment["valor_atual"]) - float(investment["valor_inicial"]),
                    2,
                )
                embed = make_bank_embed(
                    "\U0001F4E6 Resgate concluido",
                    "Seu CDB foi encerrado e o valor voltou para o saldo do banco.",
                    color=0x1E8E5A,
                )
                embed.add_field(name="ID", value=f"`{investment['id']}`", inline=True)
                embed.add_field(
                    name="Recebido",
                    value=f"**{format_currency(float(investment['valor_atual']))}**",
                    inline=True,
                )
                embed.add_field(
                    name="Lucro",
                    value=f"**{format_currency(profit)}**",
                    inline=True,
                )
                embed.add_field(
                    name="Saldo atual no banco",
                    value=f"**{format_currency(self.economy.get_balance(inner.user.id))}**",
                    inline=False,
                )
                await self._reply_embed(inner, embed)
                if profit >= 0:
                    await enviar_notificacao(
                        self,
                        inner.user.id,
                        "lucro_investimento",
                        f"📈 Seu CDB #{investment['id']} rendeu {format_currency(profit)}.",
                    )
                await self.send_transaction_log(
                    title="📦 Resgate de CDB",
                    lines=[
                        f"Usuario: {inner.user.mention}",
                        f"Investimento ID: `{investment['id']}`",
                        f"Valor recebido: **{format_currency(float(investment['valor_atual']))}**",
                        f"Lucro: **{format_currency(profit)}**",
                    ],
                    color=0x1E8E5A,
                )

            await self._run_protected(interaction, area="investimentos", callback=action)

        @self.tree.command(
            name="investimentos",
            description="Lista seus investimentos ativos com IDs e status.",
        )
        async def investimentos(interaction: discord.Interaction) -> None:
            async def action(inner: discord.Interaction) -> None:
                embed = self._build_investments_overview_embed(inner.user.id)
                await self._reply_embed(inner, embed)

            await self._run_protected(interaction, area="investimentos", callback=action)

        @self.tree.command(
            name="investimentos_admin",
            description="Admin: consulta os investimentos ativos de outro usuário.",
        )
        @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
        @app_commands.allowed_installs(guilds=True, users=False)
        @app_commands.check(self.is_admin_authorized)
        async def investimentos_admin(
            interaction: discord.Interaction,
            usuario: discord.Member,
        ) -> None:
            await self._defer_if_needed(interaction, ephemeral=True)
            embed = self._build_investments_overview_embed(usuario.id)
            embed.title = f"📊 Investimentos de {usuario.display_name}"
            embed.description = f"Resumo dos investimentos ativos de {usuario.mention}."
            await self._reply_embed(interaction, embed, ephemeral=True)

        @fundo_group.command(
            name="investir",
            description="Aplica em um fundo de investimento.",
        )
        @app_commands.choices(subtipo=FUND_CHOICES)
        async def fundo_investir(
            interaction: discord.Interaction,
            subtipo: app_commands.Choice[str],
            valor: app_commands.Range[float, 0.01, None],
        ) -> None:
            async def action(inner: discord.Interaction) -> None:
                amount = round(float(valor), 2)
                investment = self.investments.create_fund(
                    inner.user.id,
                    amount,
                    subtipo.value,
                )
                embed = make_bank_embed(
                    "\U0001FA99 Fundo contratado",
                    "Seu dinheiro entrou no fundo e passara por variacoes automaticas.",
                    color=0x8C6B00,
                )
                embed.add_field(name="ID", value=f"`{investment['id']}`", inline=True)
                embed.add_field(name="Perfil", value=subtipo.name, inline=True)
                embed.add_field(
                    name="Valor aplicado",
                    value=f"**{format_currency(float(investment['valor_inicial']))}**",
                    inline=True,
                )
                await self._reply_embed(inner, embed)
                await self.send_transaction_log(
                    title="🪙 Fundo contratado",
                    lines=[
                        f"Usuario: {inner.user.mention}",
                        f"Perfil: **{subtipo.name}**",
                        f"Valor aplicado: **{format_currency(float(investment['valor_inicial']))}**",
                        f"ID do fundo: `{investment['id']}`",
                    ],
                    color=0x8C6B00,
                )
                await self._notify_low_balance_if_needed(inner.user.id)

            await self._run_protected(interaction, area="investimentos", callback=action)

        @fundo_group.command(
            name="sacar",
            description="Saca um fundo pelo ID.",
        )
        async def fundo_sacar(
            interaction: discord.Interaction,
            investimento_id: int | None = None,
        ) -> None:
            async def action(inner: discord.Interaction) -> None:
                fund = self.investments.redeem_fund(inner.user.id, investimento_id)
                delta, percent = self.investments.describe_fund_performance(fund)
                sign = "+" if delta >= 0 else "-"
                embed = make_bank_embed(
                    "\U0001F4E4 Fundo encerrado",
                    "O valor do fundo foi devolvido ao saldo do banco.",
                    color=0x8C6B00,
                )
                embed.add_field(name="ID", value=f"`{fund['id']}`", inline=True)
                embed.add_field(
                    name="Valor resgatado",
                    value=f"**{format_currency(float(fund['valor_atual']))}**",
                    inline=True,
                )
                embed.add_field(
                    name="Resultado",
                    value=f"**{sign}{format_currency(abs(delta))} ({sign}{abs(percent):.2f}%)**",
                    inline=False,
                )
                await self._reply_embed(inner, embed)
                if delta >= 0:
                    await enviar_notificacao(
                        self,
                        inner.user.id,
                        "lucro_investimento",
                        f"📈 Seu fundo #{fund['id']} gerou lucro de {format_currency(delta)}.",
                    )
                else:
                    await enviar_notificacao(
                        self,
                        inner.user.id,
                        "perda_investimento",
                        f"📉 Seu fundo #{fund['id']} registrou perda de {format_currency(abs(delta))}.",
                    )
                await self.send_transaction_log(
                    title="📤 Fundo encerrado",
                    lines=[
                        f"Usuario: {inner.user.mention}",
                        f"Fundo ID: `{fund['id']}`",
                        f"Valor resgatado: **{format_currency(float(fund['valor_atual']))}**",
                        f"Resultado: **{sign}{format_currency(abs(delta))} ({sign}{abs(percent):.2f}%)**",
                    ],
                    color=0x8C6B00,
                )

            await self._run_protected(interaction, area="investimentos", callback=action)

        @fundo_group.command(
            name="status",
            description="Mostra o status dos seus fundos ativos.",
        )
        async def fundo_status(interaction: discord.Interaction) -> None:
            async def action(inner: discord.Interaction) -> None:
                embed = self._build_fund_status_embed(inner.user.id)
                await self._reply_embed(inner, embed)

            await self._run_protected(interaction, area="investimentos", callback=action)

        @acoes_group.command(name="mercado", description="Mostra a bolsa de valores Safra.")
        async def acoes_mercado(interaction: discord.Interaction) -> None:
            async def action(inner: discord.Interaction) -> None:
                await self._reply_embed(
                    inner,
                    self.build_market_embed("stock"),
                    view=MarketTradeView(self, inner.user.id, tipo="stock", action="buy"),
                )

            await self._run_protected(interaction, area="investimentos", callback=action)

        @acoes_group.command(name="comprar", description="Compra acoes de uma empresa.")
        async def acoes_comprar(interaction: discord.Interaction) -> None:
            async def action(inner: discord.Interaction) -> None:
                await self._reply_embed(
                    inner,
                    make_bank_embed("📈 Comprar acoes", "Selecione a empresa e informe a quantidade.", color=0x1E8E5A),
                    view=MarketTradeView(self, inner.user.id, tipo="stock", action="buy"),
                )

            await self._run_protected(interaction, area="investimentos", callback=action)

        @acoes_group.command(name="vender", description="Vende acoes da sua carteira.")
        async def acoes_vender(interaction: discord.Interaction) -> None:
            async def action(inner: discord.Interaction) -> None:
                await self._reply_embed(
                    inner,
                    make_bank_embed("📉 Vender acoes", "Selecione a empresa e informe a quantidade.", color=0xB22222),
                    view=MarketTradeView(self, inner.user.id, tipo="stock", action="sell"),
                )

            await self._run_protected(interaction, area="investimentos", callback=action)

        @acoes_group.command(name="carteira", description="Mostra sua carteira de acoes.")
        async def acoes_carteira(interaction: discord.Interaction) -> None:
            async def action(inner: discord.Interaction) -> None:
                await self._reply_embed(inner, self.build_wallet_embed(inner.user.id, "stock"))

            await self._run_protected(interaction, area="investimentos", callback=action)

        @acoes_group.command(name="detalhes", description="Mostra detalhes e grafico de um ativo.")
        async def acoes_detalhes(interaction: discord.Interaction, codigo: str) -> None:
            async def action(inner: discord.Interaction) -> None:
                await self._reply_embed(inner, self.build_asset_details_embed(codigo))

            await self._run_protected(interaction, area="investimentos", callback=action)

        @crypto_group.command(name="mercado", description="Mostra o mercado de criptomoedas.")
        async def crypto_mercado(interaction: discord.Interaction) -> None:
            async def action(inner: discord.Interaction) -> None:
                await self._reply_embed(
                    inner,
                    self.build_market_embed("crypto"),
                    view=MarketTradeView(self, inner.user.id, tipo="crypto", action="buy"),
                )

            await self._run_protected(interaction, area="investimentos", callback=action)

        @crypto_group.command(name="comprar", description="Compra criptomoedas usando saldo bancario.")
        async def crypto_comprar(interaction: discord.Interaction) -> None:
            async def action(inner: discord.Interaction) -> None:
                await self._reply_embed(
                    inner,
                    make_bank_embed("🪙 Comprar crypto", "Selecione a moeda e informe o valor em reais.", color=0x1E8E5A),
                    view=MarketTradeView(self, inner.user.id, tipo="crypto", action="buy"),
                )

            await self._run_protected(interaction, area="investimentos", callback=action)

        @crypto_group.command(name="vender", description="Vende criptomoedas da sua carteira.")
        async def crypto_vender(interaction: discord.Interaction) -> None:
            async def action(inner: discord.Interaction) -> None:
                await self._reply_embed(
                    inner,
                    make_bank_embed("💱 Vender crypto", "Selecione a moeda e informe a quantidade.", color=0xB22222),
                    view=MarketTradeView(self, inner.user.id, tipo="crypto", action="sell"),
                )

            await self._run_protected(interaction, area="investimentos", callback=action)

        @crypto_group.command(name="carteira", description="Mostra sua carteira de criptomoedas.")
        async def crypto_carteira(interaction: discord.Interaction) -> None:
            async def action(inner: discord.Interaction) -> None:
                await self._reply_embed(inner, self.build_wallet_embed(inner.user.id, "crypto"))

            await self._run_protected(interaction, area="investimentos", callback=action)

        @self.tree.command(name="carteira", description="Mostra sua carteira completa de investimentos.")
        async def carteira(interaction: discord.Interaction) -> None:
            async def action(inner: discord.Interaction) -> None:
                await self._reply_embed(inner, self.build_full_portfolio_embed(inner.user.id))

            await self._run_protected(interaction, area="investimentos", callback=action)

        @mercado_group.command(name="historico", description="Mostra historico de precos e eventos.")
        async def mercado_historico(
            interaction: discord.Interaction,
            codigo: str | None = None,
        ) -> None:
            async def action(inner: discord.Interaction) -> None:
                await self._reply_embed(inner, self.build_market_history_embed(codigo))

            await self._run_protected(interaction, area="investimentos", callback=action)

        @ranking_group.command(name="investidores", description="Mostra o ranking de investidores.")
        async def ranking_investidores(interaction: discord.Interaction) -> None:
            await self._reply_embed(interaction, self.build_investor_ranking_embed())

        @selecionar_group.command(
            name="cargo",
            description="Admin: seleciona cargos para funcoes especiais do bot.",
        )
        @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
        @app_commands.allowed_installs(guilds=True, users=False)
        @app_commands.check(self.is_admin_authorized)
        @app_commands.choices(funcao=ROLE_FUNCTION_CHOICES)
        async def selecionar_cargo(
            interaction: discord.Interaction,
            funcao: app_commands.Choice[str],
            cargo: discord.Role,
        ) -> None:
            await self._defer_if_needed(interaction, ephemeral=True)
            self.database.set_bot_setting(funcao.value, str(cargo.id))
            labels = {
                "admin_role_id": "comandos administrativos",
                "vip_role_id": "VIP / descontos",
                "cliente_role_id": "cargo Cliente",
                "cliente_vip_role_id": "cargo Cliente VIP",
                "cliente_empresarial_role_id": "cargo Cliente Empresarial",
            }
            label = labels.get(funcao.value, funcao.name)
            await self._reply_text(
                interaction,
                title="Cargo selecionado",
                description=f"O cargo {cargo.mention} agora esta vinculado a **{label}**.",
                color=0x1E8E5A,
                ephemeral=True,
            )

        @selecionar_group.command(
            name="canal",
            description="Admin: seleciona canais para funcoes especiais do bot.",
        )
        @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
        @app_commands.allowed_installs(guilds=True, users=False)
        @app_commands.check(self.is_admin_authorized)
        @app_commands.choices(funcao=CHANNEL_FUNCTION_CHOICES)
        async def selecionar_canal(
            interaction: discord.Interaction,
            funcao: app_commands.Choice[str],
            canal: discord.TextChannel,
        ) -> None:
            await self._defer_if_needed(interaction, ephemeral=True)
            self.database.set_bot_setting(funcao.value, str(canal.id))
            await self._reply_text(
                interaction,
                title="Canal selecionado",
                description=f"O canal {canal.mention} foi vinculado a **{funcao.name}**.",
                color=0x1E8E5A,
                ephemeral=True,
            )

        @selecionar_group.command(
            name="categoria",
            description="Admin: seleciona categorias para funcoes especiais do bot.",
        )
        @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
        @app_commands.allowed_installs(guilds=True, users=False)
        @app_commands.check(self.is_admin_authorized)
        @app_commands.choices(funcao=CATEGORY_FUNCTION_CHOICES)
        async def selecionar_categoria(
            interaction: discord.Interaction,
            funcao: app_commands.Choice[str],
            categoria: discord.CategoryChannel,
        ) -> None:
            await self._defer_if_needed(interaction, ephemeral=True)
            self.database.set_bot_setting(funcao.value, str(categoria.id))
            await self._reply_text(
                interaction,
                title="Categoria selecionada",
                description=(
                    f"A categoria **{categoria.name}** foi vinculada a **{funcao.name}**. "
                    "Os canais privados das contas serao criados nela."
                ),
                color=0x1E8E5A,
                ephemeral=True,
            )

        @acoes_group.command(
            name="criar",
            description="Admin: cria uma nova acao na bolsa Safra.",
        )
        @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
        @app_commands.allowed_installs(guilds=True, users=False)
        @app_commands.check(self.is_admin_authorized)
        @app_commands.choices(volatilidade=VOLATILITY_CHOICES)
        async def acoes_criar(
            interaction: discord.Interaction,
            codigo: str,
            nome: str,
            preco_inicial: app_commands.Range[float, 0.01, None],
            quantidade_emitida: app_commands.Range[float, 1, None],
            volatilidade: app_commands.Choice[str],
            dividendos_percentual: app_commands.Range[float, 0, 20] = 0,
        ) -> None:
            await self._defer_if_needed(interaction, ephemeral=True)
            asset = self.market.create_asset(
                tipo="stock",
                code=codigo,
                name=nome,
                price=round(float(preco_inicial), 2),
                supply=round(float(quantidade_emitida), 2),
                volatility=volatilidade.value,
                dividend_rate=round(float(dividendos_percentual) / 100, 4),
            )
            embed = make_bank_embed(
                "📈 Acao criada",
                "A nova empresa foi registrada na Bolsa Safra.",
                color=0x1E8E5A,
            )
            embed.add_field(name="Empresa", value=f"**{asset['name']} ({asset['code']})**", inline=False)
            embed.add_field(name="Preco inicial", value=f"**{format_currency(float(asset['price']))}**", inline=True)
            embed.add_field(name="Emitidas", value=f"`{float(asset['supply']):,.0f}`", inline=True)
            embed.add_field(name="Dividendos", value=f"**{float(asset['dividend_rate']) * 100:.2f}%**", inline=True)
            await self._reply_embed(interaction, embed, ephemeral=True)
            await self.send_transaction_log(
                title="📈 Nova acao criada",
                lines=[
                    f"Admin: {interaction.user.mention}",
                    f"Ativo: **{asset['name']} ({asset['code']})**",
                    f"Preco inicial: **{format_currency(float(asset['price']))}**",
                ],
                color=0x1E8E5A,
            )

        @acoes_group.command(
            name="excluir",
            description="Admin: exclui uma acao existente da bolsa Safra.",
        )
        @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
        @app_commands.allowed_installs(guilds=True, users=False)
        @app_commands.check(self.is_admin_authorized)
        async def acoes_excluir(
            interaction: discord.Interaction,
            codigo: str,
        ) -> None:
            await self._defer_if_needed(interaction, ephemeral=True)
            asset = self.market.delete_asset(codigo, "stock")
            embed = make_bank_embed(
                "🗑️ Ação excluída",
                f"A ação **{asset['name']} ({asset['code']})** foi removida do mercado.",
                color=0xB22222,
            )
            await self._reply_embed(interaction, embed, ephemeral=True)
            await self.send_transaction_log(
                title="🗑️ Ação excluída",
                lines=[
                    f"Admin: {interaction.user.mention}",
                    f"Ativo removido: **{asset['name']} ({asset['code']})**",
                ],
                color=0xB22222,
            )

        @acoes_group.command(
            name="reset",
            description="Admin: reseta a quantidade de uma ação para usuario ou cargo (zera).",
        )
        @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
        @app_commands.allowed_installs(guilds=True, users=False)
        @app_commands.check(self.is_admin_authorized)
        async def acoes_reset(
            interaction: discord.Interaction,
            codigo: str,
            usuario: discord.Member | None = None,
            cargo: discord.Role | None = None,
        ) -> None:
            targets = await self._resolve_target_members(interaction, usuario, cargo)
            if targets is None:
                return

            total_removed = 0.0
            for member in targets:
                result = self.market.reset_user_asset(member.id, "stock", codigo)
                total_removed += float(result.get("removed", 0.0))
                await enviar_notificacao(
                    self,
                    member.id,
                    "perda_investimento",
                    f"📉 Seus {codigo.upper()} foram zerados por uma ação administrativa.",
                )

            target_label = cargo.mention if cargo is not None else usuario.mention if usuario is not None else "nenhum"
            embed = self._build_action_embed(
                title="🔄 Reset de ação",
                color=0xB45F06,
                lines=[
                    f"🎯 Alvo: {target_label}",
                    f"🧾 Ativo: **{codigo.upper()}**",
                    f"➖ Quantidade total removida: **{total_removed:.8f}**",
                ],
            )
            await self._reply_embed(interaction, embed, ephemeral=True)
            await self.send_transaction_log(
                title="🔄 Reset de ação",
                lines=[
                    f"Admin: {interaction.user.mention}",
                    f"Alvo: {target_label}",
                    f"Ativo: **{codigo.upper()}**",
                    f"Quantidade total removida: **{total_removed:.8f}**",
                ],
                color=0xB45F06,
            )

        @acoes_group.command(
            name="add",
            description="Admin: adiciona ações para um usuario ou cargo (administrativo).",
        )
        @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
        @app_commands.allowed_installs(guilds=True, users=False)
        @app_commands.check(self.is_admin_authorized)
        async def acoes_add(
            interaction: discord.Interaction,
            codigo: str,
            quantidade: app_commands.Range[float, 0.00000001, None],
            usuario: discord.Member | None = None,
            cargo: discord.Role | None = None,
        ) -> None:
            targets = await self._resolve_target_members(interaction, usuario, cargo)
            if targets is None:
                return

            for member in targets:
                result = self.market.add_asset_to_user(member.id, "stock", codigo, float(quantidade))
                await enviar_notificacao(
                    self,
                    member.id,
                    "bonus",
                    f"🎁 Você recebeu {quantidade} de {codigo.upper()} por ajuste administrativo.",
                )

            target_label = cargo.mention if cargo is not None else usuario.mention if usuario is not None else "nenhum"
            embed = self._build_action_embed(
                title="➕ Ações adicionadas",
                color=0x137D3E,
                lines=[
                    f"🎯 Alvo: {target_label}",
                    f"🧾 Ativo: **{codigo.upper()}**",
                    f"➕ Quantidade por membro: **{float(quantidade):.8f}**",
                ],
            )
            await self._reply_embed(interaction, embed, ephemeral=True)
            await self.send_transaction_log(
                title="➕ Ações adicionadas (admin)",
                lines=[
                    f"Admin: {interaction.user.mention}",
                    f"Alvo: {target_label}",
                    f"Ativo: **{codigo.upper()}**",
                    f"Quantidade por membro: **{float(quantidade):.8f}**",
                ],
                color=0x137D3E,
            )

        @crypto_group.command(
            name="criar",
            description="Admin: cria uma nova criptomoeda no mercado Safra.",
        )
        @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
        @app_commands.allowed_installs(guilds=True, users=False)
        @app_commands.check(self.is_admin_authorized)
        @app_commands.choices(volatilidade=VOLATILITY_CHOICES)
        async def crypto_criar(
            interaction: discord.Interaction,
            codigo: str,
            nome: str,
            preco_inicial: app_commands.Range[float, 0.01, None],
            supply: app_commands.Range[float, 1, None],
            volatilidade: app_commands.Choice[str],
        ) -> None:
            await self._defer_if_needed(interaction, ephemeral=True)
            asset = self.market.create_asset(
                tipo="crypto",
                code=codigo,
                name=nome,
                price=round(float(preco_inicial), 2),
                supply=round(float(supply), 2),
                volatility=volatilidade.value,
            )
            embed = make_bank_embed(
                "🪙 Criptomoeda criada",
                "A nova moeda foi registrada no Mercado Crypto Safra.",
                color=0x8C6B00,
            )
            embed.add_field(name="Moeda", value=f"**{asset['name']} ({asset['code']})**", inline=False)
            embed.add_field(name="Preco inicial", value=f"**{format_currency(float(asset['price']))}**", inline=True)
            embed.add_field(name="Supply", value=f"`{float(asset['supply']):,.0f}`", inline=True)
            embed.add_field(name="Volatilidade", value=str(asset["volatility"]).title(), inline=True)
            await self._reply_embed(interaction, embed, ephemeral=True)
            await self.send_transaction_log(
                title="🪙 Nova criptomoeda criada",
                lines=[
                    f"Admin: {interaction.user.mention}",
                    f"Ativo: **{asset['name']} ({asset['code']})**",
                    f"Preco inicial: **{format_currency(float(asset['price']))}**",
                ],
                color=0x8C6B00,
            )

        @crypto_group.command(
            name="excluir",
            description="Admin: exclui uma crypto existente do mercado Safra.",
        )
        @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
        @app_commands.allowed_installs(guilds=True, users=False)
        @app_commands.check(self.is_admin_authorized)
        async def crypto_excluir(
            interaction: discord.Interaction,
            codigo: str,
        ) -> None:
            await self._defer_if_needed(interaction, ephemeral=True)
            asset = self.market.delete_asset(codigo, "crypto")
            embed = make_bank_embed(
                "🗑️ Crypto excluída",
                f"A moeda **{asset['name']} ({asset['code']})** foi removida do mercado.",
                color=0xB22222,
            )
            await self._reply_embed(interaction, embed, ephemeral=True)
            await self.send_transaction_log(
                title="🗑️ Crypto excluída",
                lines=[
                    f"Admin: {interaction.user.mention}",
                    f"Ativo removido: **{asset['name']} ({asset['code']})**",
                ],
                color=0xB22222,
            )

        @crypto_group.command(
            name="reset",
            description="Admin: reseta a quantidade de uma crypto para usuario ou cargo (zera).",
        )
        @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
        @app_commands.allowed_installs(guilds=True, users=False)
        @app_commands.check(self.is_admin_authorized)
        async def crypto_reset(
            interaction: discord.Interaction,
            codigo: str,
            usuario: discord.Member | None = None,
            cargo: discord.Role | None = None,
        ) -> None:
            targets = await self._resolve_target_members(interaction, usuario, cargo)
            if targets is None:
                return

            total_removed = 0.0
            for member in targets:
                result = self.market.reset_user_asset(member.id, "crypto", codigo)
                total_removed += float(result.get("removed", 0.0))
                await enviar_notificacao(
                    self,
                    member.id,
                    "perda_investimento",
                    f"📉 Seus {codigo.upper()} foram zerados por uma ação administrativa.",
                )

            target_label = cargo.mention if cargo is not None else usuario.mention if usuario is not None else "nenhum"
            embed = self._build_action_embed(
                title="🔄 Reset de crypto",
                color=0xB45F06,
                lines=[
                    f"🎯 Alvo: {target_label}",
                    f"🧾 Ativo: **{codigo.upper()}**",
                    f"➖ Quantidade total removida: **{total_removed:.8f}**",
                ],
            )
            await self._reply_embed(interaction, embed, ephemeral=True)
            await self.send_transaction_log(
                title="🔄 Reset de crypto",
                lines=[
                    f"Admin: {interaction.user.mention}",
                    f"Alvo: {target_label}",
                    f"Ativo: **{codigo.upper()}**",
                    f"Quantidade total removida: **{total_removed:.8f}**",
                ],
                color=0xB45F06,
            )

        @crypto_group.command(
            name="add",
            description="Admin: adiciona cryptos para um usuario ou cargo (administrativo).",
        )
        @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
        @app_commands.allowed_installs(guilds=True, users=False)
        @app_commands.check(self.is_admin_authorized)
        async def crypto_add(
            interaction: discord.Interaction,
            codigo: str,
            quantidade: app_commands.Range[float, 0.00000001, None],
            usuario: discord.Member | None = None,
            cargo: discord.Role | None = None,
        ) -> None:
            targets = await self._resolve_target_members(interaction, usuario, cargo)
            if targets is None:
                return

            for member in targets:
                result = self.market.add_asset_to_user(member.id, "crypto", codigo, float(quantidade))
                await enviar_notificacao(
                    self,
                    member.id,
                    "bonus",
                    f"🎁 Você recebeu {quantidade} de {codigo.upper()} por ajuste administrativo.",
                )

            target_label = cargo.mention if cargo is not None else usuario.mention if usuario is not None else "nenhum"
            embed = self._build_action_embed(
                title="➕ Cryptos adicionadas",
                color=0x137D3E,
                lines=[
                    f"🎯 Alvo: {target_label}",
                    f"🧾 Ativo: **{codigo.upper()}**",
                    f"➕ Quantidade por membro: **{float(quantidade):.8f}**",
                ],
            )
            await self._reply_embed(interaction, embed, ephemeral=True)
            await self.send_transaction_log(
                title="➕ Cryptos adicionadas (admin)",
                lines=[
                    f"Admin: {interaction.user.mention}",
                    f"Alvo: {target_label}",
                    f"Ativo: **{codigo.upper()}**",
                    f"Quantidade por membro: **{float(quantidade):.8f}**",
                ],
                color=0x137D3E,
            )

        @mercado_group.command(
            name="ajustar",
            description="Admin: ajusta manualmente o preco de um ativo (controle).",
        )
        @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
        @app_commands.allowed_installs(guilds=True, users=False)
        @app_commands.check(self.is_admin_authorized)
        @app_commands.choices(
            tipo=[
                app_commands.Choice(name="Ações", value="stock"),
                app_commands.Choice(name="Crypto", value="crypto"),
            ]
        )
        async def mercado_ajustar(
            interaction: discord.Interaction,
            tipo: app_commands.Choice[str],
            codigo: str,
            novo_preco: app_commands.Range[float, 0.01, None],
        ) -> None:
            await self._defer_if_needed(interaction, ephemeral=True)
            asset = self.market.adjust_price(tipo.value, codigo, float(novo_preco))
            embed = make_bank_embed(
                "⚙️ Preço ajustado",
                f"O preço de **{asset['name']} ({asset['code']})** foi ajustado para **{format_currency(float(asset['price']))}**.",
                color=0x0B4EA2,
            )
            await self._reply_embed(interaction, embed, ephemeral=True)
            await self.send_transaction_log(
                title="⚙️ Ajuste manual de preço",
                lines=[
                    f"Admin: {interaction.user.mention}",
                    f"Ativo: **{asset['name']} ({asset['code']})**",
                    f"Novo preço: **{format_currency(float(asset['price']))}**",
                ],
                color=0x0B4EA2,
            )

        @self.tree.command(
            name="addmoney",
            description="Admin: adiciona dinheiro em maos para um usuario ou cargo.",
        )
        @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
        @app_commands.allowed_installs(guilds=True, users=False)
        @app_commands.check(self.is_admin_authorized)
        async def addmoney(
            interaction: discord.Interaction,
            valor: app_commands.Range[float, 0.01, None],
            usuario: discord.Member | None = None,
            cargo: discord.Role | None = None,
        ) -> None:
            targets = await self._resolve_target_members(interaction, usuario, cargo)
            if targets is None:
                return

            amount = round(float(valor), 2)
            target_label = cargo.mention if cargo is not None else usuario.mention if usuario is not None else "nenhum"
            for member in targets:
                self.economy.add_money(member.id, amount)
                await enviar_notificacao(
                    self,
                    member.id,
                    "bonus",
                    f"🎁 Voce recebeu um bonus de {format_currency(amount)}.",
                )

            log_transaction(
                self.settings.log_path,
                (
                    f"ADMIN_ADDMONEY | admin={interaction.user.id} | target_type={'role' if cargo is not None else 'user'} "
                    f"| target_id={cargo.id if cargo is not None else usuario.id if usuario is not None else 0} "
                    f"| valor={amount:.2f} | membros={len(targets)}"
                ),
            )
            embed = self._build_action_embed(
                title="🛠️ Dinheiro em maos ajustado",
                color=0x137D3E,
                lines=[
                    f"🎯 Alvo: {target_label}",
                    f"👥 Membros afetados: **{len(targets)}**",
                    f"➕ Valor por membro: **{format_currency(amount)}**",
                ],
            )
            await self._reply_embed(interaction, embed, ephemeral=True)
            await self.send_transaction_log(
                title="🛠️ AddMoney aplicado",
                lines=[
                    f"Admin: {interaction.user.mention}",
                    f"Alvo: {target_label}",
                    f"Membros afetados: **{len(targets)}**",
                    f"Valor por membro: **{format_currency(amount)}**",
                ],
                color=0x137D3E,
            )

        @self.tree.command(
            name="removemoney",
            description="Admin: remove dinheiro de um usuario em maos ou no banco.",
        )
        @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
        @app_commands.allowed_installs(guilds=True, users=False)
        @app_commands.check(self.is_admin_authorized)
        @app_commands.choices(
            origem=[
                app_commands.Choice(name="Em mãos", value="wallet"),
                app_commands.Choice(name="No banco", value="balance"),
            ]
        )
        async def removemoney(
            interaction: discord.Interaction,
            usuario: discord.Member | None = None,
            cargo: discord.Role | None = None,
            valor: app_commands.Range[float, 0.01, None] = 0.0,
            origem: app_commands.Choice[str] | None = None,
        ) -> None:
            targets = await self._resolve_target_members(interaction, usuario, cargo)
            if targets is None:
                return

            amount = round(float(valor), 2)
            origem_value = origem.value if origem is not None else "wallet"
            target_label = cargo.mention if cargo is not None else usuario.mention if usuario is not None else "nenhum"
            results = []
            for member in targets:
                result = self.economy.remove_money(member.id, amount, source=origem_value)
                results.append(result)

            log_transaction(
                self.settings.log_path,
                (
                    f"ADMIN_REMOVEMONEY | admin={interaction.user.id} | target_type={'role' if cargo is not None else 'user'} "
                    f"| target_id={cargo.id if cargo is not None else usuario.id if usuario is not None else 0} "
                    f"| valor={amount:.2f} | origem={origem_value} | membros={len(targets)}"
                ),
            )
            updated_line = (
                f"💰 Novo saldo em maos: **{format_currency(results[0].wallet or 0)}**"
                if origem_value == "wallet"
                else f"🏦 Novo saldo no banco: **{format_currency(results[0].balance or 0)}**"
            )
            embed = self._build_action_embed(
                title="🧾 Dinheiro reduzido",
                color=0xB45F06,
                lines=[
                    f"🎯 Alvo: {target_label}",
                    f"👥 Membros afetados: **{len(targets)}**",
                    f"➖ Valor removido por membro: **{format_currency(amount)}**",
                    f"📍 Origem: **{origem.name if origem is not None else 'Em mãos'}**",
                    updated_line,
                ],
            )
            await self._reply_embed(interaction, embed, ephemeral=True)
            await self.send_transaction_log(
                title="🧾 RemoveMoney aplicado",
                lines=[
                    f"Admin: {interaction.user.mention}",
                    f"Alvo: {target_label}",
                    f"Origem: **{origem.name}**",
                    f"Membros afetados: **{len(targets)}**",
                    f"Valor por membro: **{format_currency(amount)}**",
                ],
                color=0xB45F06,
            )

        @self.tree.command(
            name="resetar_economia",
            description="Admin: zera o dinheiro em maos, no banco e no credito de todos os usuarios.",
        )
        @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
        @app_commands.allowed_installs(guilds=True, users=False)
        @app_commands.check(self.is_admin_authorized)
        async def resetar_economia(interaction: discord.Interaction) -> None:
            await self._defer_if_needed(interaction, ephemeral=True)
            result = self.economy.reset_all_money()
            embed = self._build_action_embed(
                title="💸 Economia resetada",
                color=0xB45F06,
                lines=[
                    f"👥 Usuarios afetados: **{result['affected_users']}**",
                    "💰 Carteiras, saldos bancarios e creditos foram zerados.",
                ],
            )
            await self._reply_embed(interaction, embed, ephemeral=True)
            await self.send_transaction_log(
                title="💸 Economia resetada",
                lines=[
                    f"Admin: {interaction.user.mention}",
                    f"Usuarios afetados: **{result['affected_users']}**",
                ],
                color=0xB45F06,
            )

        @self.tree.command(
            name="resetar_investimentos",
            description="Admin: remove todos os investimentos, ações e cryptos de todos os usuarios.",
        )
        @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
        @app_commands.allowed_installs(guilds=True, users=False)
        @app_commands.check(self.is_admin_authorized)
        async def resetar_investimentos(interaction: discord.Interaction) -> None:
            await self._defer_if_needed(interaction, ephemeral=True)
            wallet_result = self.market.reset_all_user_positions()
            investment_result = self.investments.reset_all_investments()
            embed = self._build_action_embed(
                title="📉 Investimentos resetados",
                color=0xB45F06,
                lines=[
                    f"🧾 Carteiras de ações removidas: **{wallet_result['stock_wallets_removed']}**",
                    f"🪙 Carteiras de crypto removidas: **{wallet_result['crypto_wallets_removed']}**",
                    f"📦 Investimentos CDB/fundos removidos: **{investment_result['investments_removed']}**",
                    f"📝 Logs de risco removidos: **{investment_result['risk_logs_removed']}**",
                ],
            )
            await self._reply_embed(interaction, embed, ephemeral=True)
            await self.send_transaction_log(
                title="📉 Investimentos resetados",
                lines=[
                    f"Admin: {interaction.user.mention}",
                    f"Ações removidas: **{wallet_result['stock_wallets_removed']}**",
                    f"Cryptos removidas: **{wallet_result['crypto_wallets_removed']}**",
                    f"Investimentos removidos: **{investment_result['investments_removed']}**",
                ],
                color=0xB45F06,
            )

        @self.tree.command(
            name="resetar_valores",
            description="Admin: restaura os preços padrão de ações e criptos para os valores iniciais.",
        )
        @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
        @app_commands.allowed_installs(guilds=True, users=False)
        @app_commands.check(self.is_admin_authorized)
        async def resetar_valores(interaction: discord.Interaction) -> None:
            await self._defer_if_needed(interaction, ephemeral=True)
            result = self.market.reset_market_values()
            embed = self._build_action_embed(
                title="📈 Valores resetados",
                color=0x1E8E5A,
                lines=[
                    f"🧾 Ativos restaurados: **{result['assets_reset']}**",
                    "💱 Preços, variações diárias e valores de mercado voltaram aos padrões iniciais.",
                ],
            )
            await self._reply_embed(interaction, embed, ephemeral=True)
            await self.send_transaction_log(
                title="📈 Valores resetados",
                lines=[
                    f"Admin: {interaction.user.mention}",
                    f"Ativos restaurados: **{result['assets_reset']}**",
                ],
                color=0x1E8E5A,
            )

        @self.tree.command(
            name="enviarcrypto",
            description="Envia crypto para outro usuario.",
        )
        async def enviarcrypto(
            interaction: discord.Interaction,
            usuario: discord.Member,
            codigo: str,
            quantidade: app_commands.Range[float, 0.00000001, None],
        ) -> None:
            async def action(inner: discord.Interaction) -> None:
                amount = round(float(quantidade), 8)
                result = self.market.transfer_crypto(inner.user.id, usuario.id, codigo, amount)
                embed = self._build_action_embed(
                    title="🪙 Crypto enviada",
                    color=0x8C6B00,
                    lines=[
                        f"👤 Destinatario: {usuario.mention}",
                        f"🧾 Ativo: **{result['asset']['code']}**",
                        f"📦 Quantidade enviada: **{amount:.8f}**",
                        f"💼 Saldo do destinatario: **{result['remaining_receiver']:.8f}**",
                    ],
                )
                await self._reply_embed(inner, embed)
                await self.send_transaction_log(
                    title="🪙 Crypto enviada",
                    lines=[
                        f"Remetente: {inner.user.mention}",
                        f"Destino: {usuario.mention}",
                        f"Ativo: **{result['asset']['code']}**",
                        f"Quantidade: **{amount:.8f}**",
                    ],
                    color=0x8C6B00,
                )

            await self._run_protected(interaction, area="saldo", callback=action)

        @self.tree.command(
            name="removerinvestimento",
            description="Admin: remove quantidades de crypto ou acoes da carteira de um usuario.",
        )
        @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
        @app_commands.allowed_installs(guilds=True, users=False)
        @app_commands.check(self.is_admin_authorized)
        @app_commands.choices(
            tipo=[
                app_commands.Choice(name="Ações", value="stock"),
                app_commands.Choice(name="Crypto", value="crypto"),
            ]
        )
        async def removerinvestimento(
            interaction: discord.Interaction,
            usuario: discord.Member,
            tipo: app_commands.Choice[str],
            codigo: str,
            quantidade: app_commands.Range[float, 0.00000001, None],
        ) -> None:
            amount = round(float(quantidade), 8)
            result = self.market.remove_investment(usuario.id, tipo.value, codigo, amount)
            embed = self._build_action_embed(
                title="📉 Investimento removido",
                color=0xB45F06,
                lines=[
                    f"👤 Usuario: {usuario.mention}",
                    f"🧾 Tipo: **{tipo.name}**",
                    f"🪙 Ativo: **{result['asset']['code']}**",
                    f"➖ Quantidade removida: **{amount:.8f}**",
                    f"💼 Quantidade restante: **{result['remaining']:.8f}**",
                ],
            )
            await self._reply_embed(interaction, embed, ephemeral=True)
            await self.send_transaction_log(
                title="📉 Investimento removido",
                lines=[
                    f"Admin: {interaction.user.mention}",
                    f"Usuario: {usuario.mention}",
                    f"Ativo: **{result['asset']['code']}**",
                    f"Quantidade removida: **{amount:.8f}**",
                ],
                color=0xB45F06,
            )

        @self.tree.command(
            name="fecharconta",
            description="Admin: fecha a conta de um cliente com justificativa.",
        )
        @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
        @app_commands.allowed_installs(guilds=True, users=False)
        @app_commands.check(self.is_admin_authorized)
        async def fecharconta(
            interaction: discord.Interaction,
            usuario: discord.Member,
            justificativa: str,
        ) -> None:
            profile = self.database.get_account_profile(usuario.id)
            if profile is None:
                await self._reply_text(
                    interaction,
                    title="Conta nao encontrada",
                    description="Esse usuario nao possui conta cadastrada para ser fechada.",
                    color=0xB22222,
                    ephemeral=True,
                )
                return

            self.database.update_account_profile(
                usuario.id,
                status="fechada",
                motivo_fechamento=justificativa,
            )
            embed = self._build_action_embed(
                title="🏦 Conta fechada",
                color=0xB22222,
                lines=[
                    f"👤 Usuario: {usuario.mention}",
                    f"📝 Justificativa: **{justificativa}**",
                ],
            )
            await self._reply_embed(interaction, embed, ephemeral=True)
            await self.send_transaction_log(
                title="🏦 Conta fechada",
                lines=[
                    f"Admin: {interaction.user.mention}",
                    f"Usuario: {usuario.mention}",
                    f"Justificativa: **{justificativa}**",
                ],
                color=0xB22222,
            )

        @self.tree.command(
            name="addcredito",
            description="Admin: adiciona credito para um usuario ou cargo.",
        )
        @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
        @app_commands.allowed_installs(guilds=True, users=False)
        @app_commands.check(self.is_admin_authorized)
        async def addcredito(
            interaction: discord.Interaction,
            valor: app_commands.Range[float, 0.01, None],
            usuario: discord.Member | None = None,
            cargo: discord.Role | None = None,
        ) -> None:
            targets = await self._resolve_target_members(interaction, usuario, cargo)
            if targets is None:
                return

            amount = round(float(valor), 2)
            target_label = cargo.mention if cargo is not None else usuario.mention if usuario is not None else "nenhum"
            for member in targets:
                self.economy.add_credit(member.id, amount)
                await enviar_notificacao(
                    self,
                    member.id,
                    "credito_atualizado",
                    f"💳 Seu credito foi ajustado em {format_currency(amount)}.",
                )

            log_transaction(
                self.settings.log_path,
                (
                    f"ADMIN_ADDCREDITO | admin={interaction.user.id} | target_type={'role' if cargo is not None else 'user'} "
                    f"| target_id={cargo.id if cargo is not None else usuario.id if usuario is not None else 0} "
                    f"| valor={amount:.2f} | membros={len(targets)}"
                ),
            )
            embed = self._build_action_embed(
                title="🏦 Credito ajustado",
                color=0x8C6B00,
                lines=[
                    f"🎯 Alvo: {target_label}",
                    f"👥 Membros afetados: **{len(targets)}**",
                    f"➕ Credito por membro: **{format_currency(amount)}**",
                ],
            )
            await self._reply_embed(interaction, embed, ephemeral=True)
            await self.send_transaction_log(
                title="💳 Credito adicionado",
                lines=[
                    f"Admin: {interaction.user.mention}",
                    f"Alvo: {target_label}",
                    f"Membros afetados: **{len(targets)}**",
                    f"Valor por membro: **{format_currency(amount)}**",
                ],
                color=0x8C6B00,
            )

        @self.tree.command(
            name="removecredito",
            description="Admin: remove credito de um usuario ou cargo.",
        )
        @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
        @app_commands.allowed_installs(guilds=True, users=False)
        @app_commands.check(self.is_admin_authorized)
        async def removecredito(
            interaction: discord.Interaction,
            valor: app_commands.Range[float, 0.01, None],
            usuario: discord.Member | None = None,
            cargo: discord.Role | None = None,
        ) -> None:
            targets = await self._resolve_target_members(interaction, usuario, cargo)
            if targets is None:
                return

            amount = round(float(valor), 2)
            target_label = cargo.mention if cargo is not None else usuario.mention if usuario is not None else "nenhum"
            for member in targets:
                self.economy.remove_credit(member.id, amount)
                await enviar_notificacao(
                    self,
                    member.id,
                    "credito_atualizado",
                    f"💳 Seu credito foi ajustado em {format_currency(amount)}.",
                )

            log_transaction(
                self.settings.log_path,
                (
                    f"ADMIN_REMOVECREDITO | admin={interaction.user.id} | target_type={'role' if cargo is not None else 'user'} "
                    f"| target_id={cargo.id if cargo is not None else usuario.id if usuario is not None else 0} "
                    f"| valor={amount:.2f} | membros={len(targets)}"
                ),
            )
            embed = self._build_action_embed(
                title="📉 Credito reduzido",
                color=0x7A4A10,
                lines=[
                    f"🎯 Alvo: {target_label}",
                    f"👥 Membros afetados: **{len(targets)}**",
                    f"➖ Credito removido por membro: **{format_currency(amount)}**",
                ],
            )
            await self._reply_embed(interaction, embed, ephemeral=True)
            await self.send_transaction_log(
                title="📉 Credito removido",
                lines=[
                    f"Admin: {interaction.user.mention}",
                    f"Alvo: {target_label}",
                    f"Membros afetados: **{len(targets)}**",
                    f"Valor por membro: **{format_currency(amount)}**",
                ],
                color=0x7A4A10,
            )

        @self.tree.command(name="ajuda", description="Lista os comandos do Banco Safra BOT.")
        async def ajuda(interaction: discord.Interaction) -> None:
            await self._reply_embed(interaction, self._build_help_embed())

        @gerente_group.command(
            name="conta",
            description="Define ou consulta a conta gerente que recebe perdas e tarifas.",
        )
        @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
        @app_commands.allowed_installs(guilds=True, users=False)
        @app_commands.check(self.is_admin_authorized)
        async def gerente_conta(
            interaction: discord.Interaction,
            usuario: discord.Member | None = None,
        ) -> None:
            if usuario is not None:
                self.database.set_bot_setting("manager_user_id", str(usuario.id))
            manager_id = self.get_manager_id()
            if manager_id is None:
                description = "Nenhuma conta gerente foi configurada ainda."
            else:
                description = f"Conta gerente atual: <@{manager_id}>."
            await self._reply_text(
                interaction,
                title="Conta gerente",
                description=description,
                color=0x0B4EA2,
                ephemeral=True,
            )

        @canal_group.command(
            name="transacoes",
            description="Define ou consulta o canal de transacoes do bot.",
        )
        @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
        @app_commands.allowed_installs(guilds=True, users=False)
        @app_commands.check(self.is_admin_authorized)
        async def canal_transacoes(
            interaction: discord.Interaction,
            canal: discord.TextChannel | None = None,
        ) -> None:
            if canal is not None:
                self.database.set_bot_setting("transactions_channel_id", str(canal.id))
            channel_id = self.get_transactions_channel_id()
            if channel_id is None:
                description = (
                    "Nenhum canal de transacoes foi configurado ainda. "
                    "Use `/canal transacoes #canal`."
                )
            else:
                description = f"Canal de transacoes atual: <#{channel_id}>."
            await self._reply_text(
                interaction,
                title="Canal de transacoes",
                description=description,
                color=0x0B4EA2,
                ephemeral=True,
            )

        @canal_group.command(
            name="contas",
            description="Define ou consulta o canal de aprovacao de contas.",
        )
        @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
        @app_commands.allowed_installs(guilds=True, users=False)
        @app_commands.check(self.is_admin_authorized)
        async def canal_contas(
            interaction: discord.Interaction,
            canal: discord.TextChannel | None = None,
        ) -> None:
            if canal is not None:
                self.database.set_bot_setting("account_approval_channel_id", str(canal.id))

            channel_id = self.get_account_approval_channel_id()
            category_id = self.get_account_category_id()
            if channel_id is None:
                description = (
                    "Nenhum canal de aprovacao foi configurado ainda. "
                    "Use `/canal contas #canal` ou `/selecionar canal`."
                )
            else:
                description = (
                    f"Canal de aprovacao atual: <#{channel_id}>.\n"
                    f"Categoria das contas privadas: "
                    f"{f'<#{category_id}>' if category_id else 'nao configurada'}."
                )

            await self._reply_text(
                interaction,
                title="Canal de aprovacao de contas",
                description=description,
                color=0x0B4EA2,
                ephemeral=True,
            )

        @definir_group.command(
            name="senha",
            description="Define ou atualiza sua senha por area.",
        )
        @app_commands.choices(area=PASSWORD_AREA_CHOICES)
        async def definir_senha(
            interaction: discord.Interaction,
            area: app_commands.Choice[str],
            senha: str,
        ) -> None:
            self.database.set_user_password(
                interaction.user.id,
                area.value,
                self.hash_password(senha),
            )
            await self._reply_text(
                interaction,
                title="Senha definida",
                description=f"Sua senha da area **{area.name}** foi atualizada com sucesso.",
                color=0x1E8E5A,
                ephemeral=True,
            )

        @consultar_group.command(
            name="saldo",
            description="Admin: consulta o saldo de outro usuario.",
        )
        @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
        @app_commands.allowed_installs(guilds=True, users=False)
        @app_commands.check(self.is_admin_authorized)
        async def consultar_saldo(
            interaction: discord.Interaction,
            usuario: discord.Member,
        ) -> None:
            wallet = self.economy.get_wallet(usuario.id)
            balance = self.economy.get_balance(usuario.id)
            total = self.economy.get_total_balance(usuario.id)
            credit = self.economy.get_credit(usuario.id)
            embed = self._build_account_embed(
                title="\U0001F50E Consulta de saldo",
                member=usuario,
                wallet=wallet,
                balance=balance,
                total=total,
                credit=credit,
                color=0x1F3C88,
            )
            await self._reply_embed(interaction, embed, ephemeral=True)

        @consultar_group.command(
            name="conta",
            description="Admin: consulta o cadastro completo da conta de um usuario.",
        )
        @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
        @app_commands.allowed_installs(guilds=True, users=False)
        @app_commands.check(self.is_admin_authorized)
        async def consultar_conta(
            interaction: discord.Interaction,
            usuario: discord.Member,
        ) -> None:
            profile = self.database.get_account_profile(usuario.id)
            if profile is None:
                await self._reply_text(
                    interaction,
                    title="Conta nao encontrada",
                    description="Esse usuario ainda nao possui uma conta cadastrada.",
                    color=0xB22222,
                    ephemeral=True,
                )
                return

            wallet = self.economy.get_wallet(usuario.id)
            balance = self.economy.get_balance(usuario.id)
            total = self.economy.get_total_balance(usuario.id)
            credit = self.economy.get_credit(usuario.id)
            embed = self._build_profile_embed(
                title="🔎 Consulta de conta",
                member=usuario,
                profile=profile,
                wallet=wallet,
                balance=balance,
                total=total,
                credit=credit,
                color=0x1F3C88,
            )
            await self._reply_embed(interaction, embed, ephemeral=True)

        self.tree.add_command(consultar_group)
        self.tree.add_command(fundo_group)
        self.tree.add_command(canal_group)
        self.tree.add_command(gerente_group)
        self.tree.add_command(definir_group)
        self.tree.add_command(acoes_group)
        self.tree.add_command(crypto_group)
        self.tree.add_command(mercado_group)
        self.tree.add_command(ranking_group)
        self.tree.add_command(selecionar_group)


def create_bot() -> BancoSafraBot:
    _load_dotenv_file()
    settings = load_settings()
    database = Database(settings.database_path)
    return BancoSafraBot(settings=settings, database=database)


def _load_dotenv_file(env_path: str | None = None) -> None:
    candidates = []
    if env_path:
        candidate = Path(env_path)
        candidates.append(candidate if candidate.is_absolute() else Path.cwd() / candidate)
    candidates.extend([
        Path.cwd() / ".env",
        Path(__file__).resolve().parent.parent / ".env",
    ])

    env_file_path: Path | None = None
    for candidate in candidates:
        if candidate.exists():
            env_file_path = candidate
            break

    if env_file_path is None:
        return

    with env_file_path.open("r", encoding="utf-8") as env_file:
        for line in env_file:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            key = key.strip().removeprefix("export ")
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            os.environ.setdefault(key, value)
