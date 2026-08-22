from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from src.database import Database


@dataclass(frozen=True)
class InvestmentTypeOption:
    key: str
    label: str
    percent: float
    duration_days: int | None = None
    min_change: float | None = None
    max_change: float | None = None
    win_chance: float | None = None
    win_return: float | None = None
    loss_return: float | None = None


@dataclass
class InvestmentActionResult:
    title: str
    description: str
    color: int
    investment: sqlite3.Row | None = None
    payout: float | None = None
    delta: float | None = None


@dataclass
class FundUpdateResult:
    investment: sqlite3.Row
    delta: float


class InvestmentService:
    CDB_OPTIONS: dict[str, InvestmentTypeOption] = {
        "cdb_3": InvestmentTypeOption(
            key="cdb_3",
            label="CDB 3 dias",
            percent=0.05,
            duration_days=3,
        ),
        "cdb_7": InvestmentTypeOption(
            key="cdb_7",
            label="CDB 7 dias",
            percent=0.12,
            duration_days=7,
        ),
    }

    RISK_OPTIONS: dict[str, InvestmentTypeOption] = {
        "baixo": InvestmentTypeOption(
            key="baixo",
            label="Baixo risco",
            percent=0,
            win_chance=0.78,
            win_return=0.10,
            loss_return=-0.04,
        ),
        "medio": InvestmentTypeOption(
            key="medio",
            label="Medio risco",
            percent=0,
            win_chance=0.50,
            win_return=0.24,
            loss_return=-0.14,
        ),
        "alto": InvestmentTypeOption(
            key="alto",
            label="Alto risco",
            percent=0,
            win_chance=0.30,
            win_return=0.60,
            loss_return=-0.32,
        ),
    }

    FUND_OPTIONS: dict[str, InvestmentTypeOption] = {
        "conservador": InvestmentTypeOption(
            key="conservador",
            label="Fundo Conservador",
            percent=0,
            min_change=-0.01,
            max_change=0.02,
        ),
        "moderado": InvestmentTypeOption(
            key="moderado",
            label="Fundo Moderado",
            percent=0,
            min_change=-0.02,
            max_change=0.04,
        ),
        "agressivo": InvestmentTypeOption(
            key="agressivo",
            label="Fundo Agressivo",
            percent=0,
            min_change=-0.05,
            max_change=0.10,
        ),
    }

    def __init__(self, database: Database) -> None:
        self.database = database
        self._initialize_limits()

    def _initialize_limits(self) -> None:
        with self.database._get_connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS risk_investment_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    option_key TEXT NOT NULL,
                    amount REAL NOT NULL,
                    delta REAL NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.commit()

    def reset_all_investments(self) -> dict[str, int]:
        with self.database._get_connection() as connection:
            investments_count = int(
                connection.execute("SELECT COUNT(*) AS total FROM investments").fetchone()["total"]
            )
            risk_logs_count = int(
                connection.execute("SELECT COUNT(*) AS total FROM risk_investment_log").fetchone()["total"]
            )
            connection.execute("DELETE FROM investments")
            connection.execute("DELETE FROM risk_investment_log")
            connection.commit()
        return {
            "investments_removed": investments_count,
            "risk_logs_removed": risk_logs_count,
        }

    def create_cdb(self, user_id: int, amount: float, option_key: str) -> sqlite3.Row:
        option = self.CDB_OPTIONS[option_key]
        self._validate_bank_balance(user_id, amount)

        now = self._now()
        redeem_at = now + timedelta(days=option.duration_days or 0)
        final_value = round(amount * (1 + option.percent), 2)

        self.database.update_balance(user_id, -amount)
        investment_id = self.database.create_investment(
            user_id=user_id,
            tipo="cdb",
            subtipo=option.key,
            valor_inicial=amount,
            valor_atual=final_value,
            data_inicio=self._serialize(now),
            data_resgate=self._serialize(redeem_at),
            status="ativo",
            ultima_atualizacao=self._serialize(now),
        )
        investment = self.database.get_investment(investment_id)
        if investment is None:
            raise RuntimeError("Falha ao salvar o investimento CDB.")
        return investment

    def redeem_cdb(self, user_id: int, investment_id: int | None = None) -> sqlite3.Row:
        investment = self._resolve_user_investment(user_id, investment_id, tipo="cdb")
        if investment["status"] != "ativo":
            raise ValueError("Esse investimento ja foi resgatado.")

        redeem_at = self._deserialize(investment["data_resgate"])
        remaining = redeem_at - self._now()
        if remaining.total_seconds() > 0:
            raise ValueError(
                "Esse CDB ainda esta travado. Tempo restante: "
                f"{self.format_remaining_time(remaining)}."
            )

        payout = float(investment["valor_atual"])
        now = self._now()
        self.database.update_balance(user_id, payout)
        self.database.close_investment(
            int(investment["id"]),
            valor_atual=payout,
            status="resgatado",
            ultima_atualizacao=self._serialize(now),
        )
        updated = self.database.get_investment(int(investment["id"]))
        if updated is None:
            raise RuntimeError("Falha ao finalizar o resgate.")
        return updated

    def run_risk_investment(
        self, user_id: int, amount: float, option_key: str
    ) -> InvestmentActionResult:
        option = self.RISK_OPTIONS[option_key]
        self._validate_bank_balance(user_id, amount)
        used = self.count_recent_risk_investments(user_id)
        if used >= 3:
            raise ValueError(
                "Limite diario atingido. Voce pode fazer apenas 3 investimentos "
                "de risco a cada 24 horas."
            )

        win = random.random() <= float(option.win_chance)
        rate = float(option.win_return if win else option.loss_return)
        payout = round(amount * (1 + rate), 2)
        delta = round(payout - amount, 2)

        self.database.update_balance(user_id, delta)
        self._log_risk_investment(user_id, option_key, amount, delta)

        if delta >= 0:
            title = "Resultado positivo"
            color = 0x1E8E5A
            description = (
                f"{option.label}: operacao encerrada com lucro de {delta:.2f}."
            )
        else:
            title = "Resultado negativo"
            color = 0xB22222
            description = (
                f"{option.label}: operacao encerrou com perda de {abs(delta):.2f}."
            )

        return InvestmentActionResult(
            title=title,
            description=description,
            color=color,
            payout=payout,
            delta=delta,
        )

    def count_recent_risk_investments(self, user_id: int) -> int:
        since = self._serialize(self._now() - timedelta(hours=24))
        with self.database._get_connection() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS total
                FROM risk_investment_log
                WHERE user_id = ? AND created_at >= ?
                """,
                (user_id, since),
            ).fetchone()
            return int(row["total"] if row is not None else 0)

    def _log_risk_investment(
        self,
        user_id: int,
        option_key: str,
        amount: float,
        delta: float,
    ) -> None:
        with self.database._get_connection() as connection:
            connection.execute(
                """
                INSERT INTO risk_investment_log (
                    user_id, option_key, amount, delta, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, option_key, amount, delta, self._serialize(self._now())),
            )
            connection.commit()

    def create_fund(self, user_id: int, amount: float, option_key: str) -> sqlite3.Row:
        option = self.FUND_OPTIONS[option_key]
        self._validate_bank_balance(user_id, amount)

        now = self._now()
        self.database.update_balance(user_id, -amount)
        investment_id = self.database.create_investment(
            user_id=user_id,
            tipo="fundo",
            subtipo=option.key,
            valor_inicial=amount,
            valor_atual=amount,
            data_inicio=self._serialize(now),
            data_resgate=None,
            status="ativo",
            ultima_atualizacao=self._serialize(now),
        )
        investment = self.database.get_investment(investment_id)
        if investment is None:
            raise RuntimeError("Falha ao salvar o fundo.")
        return investment

    def redeem_fund(self, user_id: int, investment_id: int | None = None) -> sqlite3.Row:
        investment = self._resolve_user_investment(user_id, investment_id, tipo="fundo")
        if investment["status"] != "ativo":
            raise ValueError("Esse fundo ja foi encerrado.")

        payout = round(float(investment["valor_atual"]), 2)
        now = self._now()
        self.database.update_balance(user_id, payout)
        self.database.close_investment(
            int(investment["id"]),
            valor_atual=payout,
            status="resgatado",
            ultima_atualizacao=self._serialize(now),
        )
        updated = self.database.get_investment(int(investment["id"]))
        if updated is None:
            raise RuntimeError("Falha ao encerrar o fundo.")
        return updated

    def list_user_funds(self, user_id: int) -> list[sqlite3.Row]:
        investments = self.database.list_investments(
            user_id=user_id,
            tipo="fundo",
            status="ativo",
        )
        return investments

    def list_user_cdbs(self, user_id: int) -> list[sqlite3.Row]:
        return self.database.list_investments(
            user_id=user_id,
            tipo="cdb",
            status="ativo",
        )

    def get_all_active_investments(self, user_id: int) -> list[sqlite3.Row]:
        return self.database.list_investments(user_id=user_id, status="ativo")

    def update_funds(self) -> list[FundUpdateResult]:
        updated_rows: list[FundUpdateResult] = []
        now = self._now()

        for investment in self.database.list_active_funds():
            option = self.FUND_OPTIONS.get(str(investment["subtipo"]))
            if option is None:
                continue

            last_update = self._deserialize(investment["ultima_atualizacao"])
            if now - last_update < timedelta(hours=24):
                continue

            rate = random.uniform(
                float(option.min_change or 0),
                float(option.max_change or 0),
            )
            current_value = float(investment["valor_atual"])
            suggested_value = max(0.0, round(current_value * (1 + rate), 2))
            new_value = min(suggested_value, current_value + 1000.0)
            self.database.update_investment_value(
                int(investment["id"]),
                valor_atual=new_value,
                ultima_atualizacao=self._serialize(now),
            )
            updated = self.database.get_investment(int(investment["id"]))
            if updated is not None:
                delta = round(new_value - current_value, 2)
                updated_rows.append(FundUpdateResult(investment=updated, delta=delta))

        return updated_rows

    def check_matured_investments(self) -> list[sqlite3.Row]:
        now = self._now()
        matured: list[sqlite3.Row] = []
        for investment in self.database.list_investments(tipo="cdb", status="ativo"):
            if investment["data_resgate"] and self._deserialize(
                investment["data_resgate"]
            ) <= now:
                matured.append(investment)
        return matured

    def investment_remaining(self, investment: sqlite3.Row) -> timedelta | None:
        redeem_at = investment["data_resgate"]
        if not redeem_at:
            return None
        remaining = self._deserialize(redeem_at) - self._now()
        return max(remaining, timedelta(0))

    def describe_fund_performance(self, investment: sqlite3.Row) -> tuple[float, float]:
        current = float(investment["valor_atual"])
        initial = float(investment["valor_inicial"])
        delta = round(current - initial, 2)
        percent = 0.0 if initial == 0 else round((delta / initial) * 100, 2)
        return delta, percent

    @staticmethod
    def format_remaining_time(delta: timedelta) -> str:
        total_seconds = max(0, int(delta.total_seconds()))
        days, remainder = divmod(total_seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, _ = divmod(remainder, 60)

        parts: list[str] = []
        if days:
            parts.append(f"{days}d")
        if hours:
            parts.append(f"{hours}h")
        if minutes or not parts:
            parts.append(f"{minutes}min")
        return " ".join(parts)

    def _resolve_user_investment(
        self,
        user_id: int,
        investment_id: int | None,
        *,
        tipo: str,
    ) -> sqlite3.Row:
        if investment_id is not None:
            investment = self.database.get_user_investment(user_id, investment_id)
            if investment is None or investment["tipo"] != tipo:
                raise ValueError("Investimento nao encontrado para esse usuario.")
            return investment

        active = self.database.list_investments(
            user_id=user_id,
            tipo=tipo,
            status="ativo",
        )
        if not active:
            raise ValueError("Voce nao possui investimentos ativos desse tipo.")
        if len(active) > 1:
            raise ValueError(
                "Voce possui mais de um investimento ativo. Informe o ID desejado."
            )
        return active[0]

    def _validate_bank_balance(self, user_id: int, amount: float) -> None:
        if amount <= 0:
            raise ValueError("O valor precisa ser maior que zero.")
        if not self.database.has_balance(user_id, amount):
            raise ValueError("Saldo insuficiente no banco para investir esse valor.")

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def _serialize(value: datetime) -> str:
        return value.isoformat()

    @staticmethod
    def _deserialize(value: str) -> datetime:
        return datetime.fromisoformat(value)
