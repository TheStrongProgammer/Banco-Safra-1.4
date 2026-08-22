from __future__ import annotations

import math
import random
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from src.database import Database


STOCK_SEEDS = [
    ("SFRA", "Safra Holding", 120.0, 1_000_000, "media", 0.02),
    ("CTMO", "Centaur Motors", 86.0, 750_000, "alta", 0.015),
    ("CTCO", "Centaur Construtora", 64.0, 620_000, "media", 0.018),
    ("CTMK", "Centaur Market", 42.0, 900_000, "baixa", 0.012),
    ("CTGM", "Centaur Games", 31.0, 500_000, "alta", 0.01),
]

CRYPTO_SEEDS = [
    ("BTC", "Bitcoin RP", 180_000.0, 21_000_000, "alta"),
    ("ETH", "Ethereum RP", 12_000.0, 120_000_000, "alta"),
    ("SFRC", "Safra Coin", 25.0, 50_000_000, "media"),
    ("CTC", "Centaur Coin", 9.5, 100_000_000, "alta"),
]

VOLATILITY = {
    "baixa": (-0.015, 0.05),
    "media": (-0.03, 0.08),
    "alta": (-0.06, 0.14),
    "crypto": (-0.09, 0.24),
}


@dataclass
class TradeResult:
    asset: sqlite3.Row
    quantity: float
    total: float
    wallet_quantity: float
    average_price: float
    profit: float = 0.0


class MarketService:
    def __init__(self, database: Database) -> None:
        self.database = database
        self.initialize()

    def initialize(self) -> None:
        with self.database._get_connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS market_assets (
                    code TEXT PRIMARY KEY,
                    tipo TEXT NOT NULL,
                    name TEXT NOT NULL,
                    price REAL NOT NULL,
                    previous_price REAL NOT NULL,
                    daily_change REAL NOT NULL DEFAULT 0,
                    supply REAL NOT NULL,
                    volatility TEXT NOT NULL,
                    dividend_rate REAL NOT NULL DEFAULT 0,
                    volume REAL NOT NULL DEFAULT 0,
                    market_cap REAL NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS market_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT NOT NULL,
                    tipo TEXT NOT NULL,
                    price REAL NOT NULL,
                    change_percent REAL NOT NULL,
                    event_name TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS stock_wallet (
                    user_id INTEGER NOT NULL,
                    code TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    average_price REAL NOT NULL,
                    PRIMARY KEY (user_id, code)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS crypto_wallet (
                    user_id INTEGER NOT NULL,
                    code TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    average_price REAL NOT NULL,
                    PRIMARY KEY (user_id, code)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS market_transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    tipo TEXT NOT NULL,
                    action TEXT NOT NULL,
                    code TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    unit_price REAL NOT NULL,
                    total REAL NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS market_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    impact REAL NOT NULL,
                    target TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS achievements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    code TEXT NOT NULL,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(user_id, code)
                )
                """
            )
            self._seed_assets(connection)
            connection.commit()

    def _seed_assets(self, connection: sqlite3.Connection) -> None:
        now = self._now()
        for code, name, price, supply, volatility, dividend_rate in STOCK_SEEDS:
            connection.execute(
                """
                INSERT INTO market_assets (
                    code, tipo, name, price, previous_price, supply, volatility,
                    dividend_rate, volume, market_cap, updated_at
                ) VALUES (?, 'stock', ?, ?, ?, ?, ?, ?, 0, ?, ?)
                ON CONFLICT(code) DO NOTHING
                """,
                (code, name, price, price, supply, volatility, dividend_rate, price * supply, now),
            )
            self._insert_history(connection, code, "stock", price, 0, None, now)

        for code, name, price, supply, volatility in CRYPTO_SEEDS:
            connection.execute(
                """
                INSERT INTO market_assets (
                    code, tipo, name, price, previous_price, supply, volatility,
                    dividend_rate, volume, market_cap, updated_at
                ) VALUES (?, 'crypto', ?, ?, ?, ?, ?, 0, 0, ?, ?)
                ON CONFLICT(code) DO NOTHING
                """,
                (code, name, price, price, supply, volatility, price * supply, now),
            )
            self._insert_history(connection, code, "crypto", price, 0, None, now)

    def get_asset(self, code: str, tipo: str | None = None) -> sqlite3.Row | None:
        query = "SELECT * FROM market_assets WHERE code = ?"
        params: list[object] = [code.upper()]
        if tipo is not None:
            query += " AND tipo = ?"
            params.append(tipo)
        with self.database._get_connection() as connection:
            return connection.execute(query, tuple(params)).fetchone()

    def list_assets(self, tipo: str | None = None) -> list[sqlite3.Row]:
        query = "SELECT * FROM market_assets"
        params: list[object] = []
        if tipo is not None:
            query += " WHERE tipo = ?"
            params.append(tipo)
        query += " ORDER BY tipo ASC, code ASC"
        with self.database._get_connection() as connection:
            return list(connection.execute(query, tuple(params)).fetchall())

    def buy_stock(self, user_id: int, code: str, quantity: int, discount_rate: float = 0.0) -> TradeResult:
        if not isinstance(quantity, int) or quantity <= 0:
            raise ValueError("A quantidade deve ser maior que zero.")
        return self._buy(user_id, "stock", code, float(quantity), discount_rate=discount_rate)

    def sell_stock(self, user_id: int, code: str, quantity: int) -> TradeResult:
        if not isinstance(quantity, int) or quantity <= 0:
            raise ValueError("A quantidade deve ser maior que zero.")
        return self._sell(user_id, "stock", code, float(quantity))

    def buy_crypto(self, user_id: int, code: str, amount: float, discount_rate: float = 0.0) -> TradeResult:
        if not self._valid_positive(amount):
            raise ValueError("O valor deve ser maior que zero.")
        asset = self._require_asset(code, "crypto")
        quantity = round(amount / float(asset["price"]), 8)
        return self._buy(user_id, "crypto", code, quantity, discount_rate=discount_rate)

    def sell_crypto(self, user_id: int, code: str, quantity: float) -> TradeResult:
        if not self._valid_positive(quantity):
            raise ValueError("A quantidade deve ser maior que zero.")
        return self._sell(user_id, "crypto", code, quantity)

    def transfer_crypto(self, sender_id: int, receiver_id: int, code: str, quantity: float) -> dict[str, object]:
        if sender_id == receiver_id:
            raise ValueError("Voce nao pode enviar crypto para si mesmo.")
        if not self._valid_positive(quantity):
            raise ValueError("A quantidade deve ser maior que zero.")

        asset = self._require_asset(code, "crypto")
        table = self._wallet_table("crypto")
        now = self._now()
        with self.database._get_connection() as connection:
            sender_wallet = self._get_wallet_row(connection, table, sender_id, asset["code"])
            if sender_wallet is None or float(sender_wallet["quantity"]) < quantity:
                raise ValueError("Voce nao possui quantidade suficiente dessa crypto para enviar.")

            sender_quantity = float(sender_wallet["quantity"])
            sender_average = float(sender_wallet["average_price"])
            remaining_sender = round(sender_quantity - quantity, 8)

            receiver_wallet = self._get_wallet_row(connection, table, receiver_id, asset["code"])
            receiver_quantity = float(receiver_wallet["quantity"]) if receiver_wallet else 0.0
            receiver_average = float(receiver_wallet["average_price"]) if receiver_wallet else 0.0
            new_receiver_quantity = receiver_quantity + quantity
            new_receiver_average = (
                ((receiver_quantity * receiver_average) + (quantity * sender_average))
                / new_receiver_quantity
                if new_receiver_quantity > 0
                else 0.0
            )

            if remaining_sender <= 0:
                connection.execute(
                    f"DELETE FROM {table} WHERE user_id = ? AND code = ?",
                    (sender_id, asset["code"]),
                )
            else:
                connection.execute(
                    f"UPDATE {table} SET quantity = ? WHERE user_id = ? AND code = ?",
                    (remaining_sender, sender_id, asset["code"]),
                )

            connection.execute(
                f"""
                INSERT INTO {table} (user_id, code, quantity, average_price)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, code) DO UPDATE SET
                    quantity = excluded.quantity,
                    average_price = excluded.average_price
                """,
                (
                    receiver_id,
                    asset["code"],
                    new_receiver_quantity,
                    new_receiver_average,
                ),
            )
            self._log_transaction(
                connection,
                sender_id,
                "crypto",
                "transfer_out",
                asset["code"],
                quantity,
                float(asset["price"]),
                0.0,
                now,
            )
            self._log_transaction(
                connection,
                receiver_id,
                "crypto",
                "transfer_in",
                asset["code"],
                quantity,
                float(asset["price"]),
                0.0,
                now,
            )
            connection.commit()

        return {
            "asset": asset,
            "quantity": quantity,
            "remaining_sender": remaining_sender,
            "remaining_receiver": new_receiver_quantity,
        }

    def remove_investment(self, user_id: int, tipo: str, code: str, quantity: float) -> dict[str, object]:
        tipo_key = tipo.strip().lower()
        if tipo_key not in {"stock", "crypto"}:
            raise ValueError("Tipo de investimento invalido.")
        if not self._valid_positive(quantity):
            raise ValueError("A quantidade deve ser maior que zero.")

        asset = self._require_asset(code, tipo_key)
        table = self._wallet_table(tipo_key)
        with self.database._get_connection() as connection:
            wallet = self._get_wallet_row(connection, table, user_id, asset["code"])
            if wallet is None or float(wallet["quantity"]) < quantity:
                raise ValueError("O usuario nao possui quantidade suficiente desse ativo.")

            current_quantity = float(wallet["quantity"])
            remaining = round(current_quantity - quantity, 8)
            if remaining <= 0:
                connection.execute(
                    f"DELETE FROM {table} WHERE user_id = ? AND code = ?",
                    (user_id, asset["code"]),
                )
            else:
                connection.execute(
                    f"UPDATE {table} SET quantity = ? WHERE user_id = ? AND code = ?",
                    (remaining, user_id, asset["code"]),
                )
            self._log_transaction(
                connection,
                user_id,
                tipo_key,
                "remove",
                asset["code"],
                quantity,
                float(asset["price"]),
                0.0,
                self._now(),
            )
            connection.commit()

        return {
            "asset": asset,
            "quantity": quantity,
            "remaining": remaining,
            "tipo": tipo_key,
        }

    def _buy(
        self,
        user_id: int,
        tipo: str,
        code: str,
        quantity: float,
        *,
        discount_rate: float = 0.0,
    ) -> TradeResult:
        if not self._valid_positive(quantity):
            raise ValueError("A quantidade deve ser maior que zero.")
        discount_rate = min(max(float(discount_rate), 0.0), 0.5)
        asset = self._require_asset(code, tipo)
        table = self._wallet_table(tipo)
        total = round(float(asset["price"]) * quantity * (1 - discount_rate), 2)
        if total <= 0:
            raise ValueError("Valor total invalido para compra.")

        now = self._now()
        with self.database._get_connection() as connection:
            updated_balance = connection.execute(
                """
                UPDATE users
                SET balance = balance - ?
                WHERE user_id = ? AND balance >= ?
                """,
                (total, user_id, total),
            )
            if updated_balance.rowcount != 1:
                raise ValueError("Saldo bancario insuficiente para realizar a compra.")

            wallet = self._get_wallet_row(connection, table, user_id, asset["code"])
            old_quantity = float(wallet["quantity"]) if wallet else 0.0
            old_average = float(wallet["average_price"]) if wallet else 0.0
            new_quantity = old_quantity + quantity
            new_average = ((old_quantity * old_average) + total) / new_quantity

            connection.execute(
                f"""
                INSERT INTO {table} (user_id, code, quantity, average_price)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, code) DO UPDATE SET
                    quantity = excluded.quantity,
                    average_price = excluded.average_price
                """,
                (user_id, asset["code"], new_quantity, new_average),
            )
            self._log_transaction(connection, user_id, tipo, "buy", asset["code"], quantity, float(asset["price"]), total, now)
            self._grant_achievements(connection, user_id, new_quantity, total)
            connection.commit()

        return TradeResult(asset, quantity, total, new_quantity, new_average)

    def _sell(self, user_id: int, tipo: str, code: str, quantity: float) -> TradeResult:
        if not self._valid_positive(quantity):
            raise ValueError("A quantidade deve ser maior que zero.")
        asset = self._require_asset(code, tipo)
        table = self._wallet_table(tipo)
        now = self._now()
        with self.database._get_connection() as connection:
            wallet = self._get_wallet_row(connection, table, user_id, asset["code"])
            if wallet is None or float(wallet["quantity"]) < quantity:
                raise ValueError("Voce nao possui quantidade suficiente desse ativo.")

            current_quantity = float(wallet["quantity"])
            average = float(wallet["average_price"])
            remaining = round(current_quantity - quantity, 8)
            total = round(float(asset["price"]) * quantity, 2)
            profit = round((float(asset["price"]) - average) * quantity, 2)

            connection.execute(
                "UPDATE users SET balance = balance + ? WHERE user_id = ?",
                (total, user_id),
            )
            if remaining <= 0:
                connection.execute(
                    f"DELETE FROM {table} WHERE user_id = ? AND code = ?",
                    (user_id, asset["code"]),
                )
            else:
                connection.execute(
                    f"UPDATE {table} SET quantity = ? WHERE user_id = ? AND code = ?",
                    (remaining, user_id, asset["code"]),
                )
            self._log_transaction(connection, user_id, tipo, "sell", asset["code"], quantity, float(asset["price"]), total, now)
            connection.commit()

        return TradeResult(asset, quantity, total, remaining, average, profit)

    def create_asset(
        self,
        *,
        tipo: str,
        code: str,
        name: str,
        price: float,
        supply: float,
        volatility: str,
        dividend_rate: float = 0.0,
    ) -> sqlite3.Row:
        tipo = tipo.strip().lower()
        code = code.strip().upper()
        name = name.strip()
        volatility = volatility.strip().lower()

        if tipo not in {"stock", "crypto"}:
            raise ValueError("Tipo de ativo invalido.")
        if not code.isalnum() or not 2 <= len(code) <= 8:
            raise ValueError("Codigo deve ter entre 2 e 8 letras/numeros.")
        if len(name) < 3:
            raise ValueError("Nome do ativo deve ter pelo menos 3 caracteres.")
        if not self._valid_positive(price) or not self._valid_positive(supply):
            raise ValueError("Preco e quantidade emitida devem ser positivos.")
        if volatility not in VOLATILITY or volatility == "crypto":
            raise ValueError("Volatilidade deve ser baixa, media ou alta.")
        if not math.isfinite(dividend_rate) or dividend_rate < 0 or dividend_rate > 0.2:
            raise ValueError("Dividendos devem ficar entre 0 e 20%.")

        now = self._now()
        with self.database._get_connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO market_assets (
                        code, tipo, name, price, previous_price, daily_change, supply,
                        volatility, dividend_rate, volume, market_cap, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, 0, ?, ?)
                    """,
                    (
                        code,
                        tipo,
                        name,
                        round(price, 2),
                        round(price, 2),
                        round(supply, 2),
                        volatility,
                        round(dividend_rate, 4) if tipo == "stock" else 0,
                        round(price * supply, 2),
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("Ja existe um ativo com esse codigo.") from exc
            self._insert_history(connection, code, tipo, round(price, 2), 0, "Ativo criado", now)
            connection.commit()

        created = self.get_asset(code, tipo)
        if created is None:
            raise RuntimeError("Falha ao criar o ativo.")
        return created

    def delete_asset(self, code: str, tipo: str) -> sqlite3.Row:
        tipo = tipo.strip().lower()
        asset = self.get_asset(code, tipo)
        if asset is None:
            raise ValueError("Ativo nao encontrado nesse mercado.")
        with self.database._get_connection() as connection:
            # remove wallets entries
            table = self._wallet_table(tipo)
            connection.execute(f"DELETE FROM {table} WHERE code = ?", (asset["code"],))
            # remove history and asset
            connection.execute("DELETE FROM market_history WHERE code = ?", (asset["code"],))
            connection.execute("DELETE FROM market_assets WHERE code = ? AND tipo = ?", (asset["code"], tipo))
            connection.commit()
        return asset

    def add_asset_to_user(self, user_id: int, tipo: str, code: str, quantity: float) -> dict[str, object]:
        tipo_key = tipo.strip().lower()
        if tipo_key not in {"stock", "crypto"}:
            raise ValueError("Tipo de investimento invalido.")
        if not self._valid_positive(quantity):
            raise ValueError("A quantidade deve ser maior que zero.")

        asset = self._require_asset(code, tipo_key)
        table = self._wallet_table(tipo_key)
        now = self._now()
        with self.database._get_connection() as connection:
            wallet = self._get_wallet_row(connection, table, user_id, asset["code"])
            old_quantity = float(wallet["quantity"]) if wallet else 0.0
            old_average = float(wallet["average_price"]) if wallet else 0.0
            new_quantity = round(old_quantity + float(quantity), 8)
            # set average price to current asset price for admin grants
            new_average = float(asset["price"]) if new_quantity > 0 else 0.0
            connection.execute(
                f"""
                INSERT INTO {table} (user_id, code, quantity, average_price)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, code) DO UPDATE SET
                    quantity = excluded.quantity,
                    average_price = excluded.average_price
                """,
                (user_id, asset["code"], new_quantity, new_average),
            )
            self._log_transaction(connection, user_id, tipo_key, "admin_add", asset["code"], float(quantity), float(asset["price"]), 0.0, now)
            connection.commit()

        return {"asset": asset, "added": float(quantity), "new_quantity": new_quantity}

    def reset_all_user_positions(self) -> dict[str, int]:
        with self.database._get_connection() as connection:
            stock_count = int(
                connection.execute("SELECT COUNT(*) AS total FROM stock_wallet").fetchone()["total"]
            )
            crypto_count = int(
                connection.execute("SELECT COUNT(*) AS total FROM crypto_wallet").fetchone()["total"]
            )
            connection.execute("DELETE FROM stock_wallet")
            connection.execute("DELETE FROM crypto_wallet")
            connection.commit()
        return {"stock_wallets_removed": stock_count, "crypto_wallets_removed": crypto_count}

    def reset_market_values(self) -> dict[str, int]:
        defaults = {
            ("stock", code): float(price)
            for code, _, price, _, _, _ in STOCK_SEEDS
        }
        defaults.update(
            {
                ("crypto", code): float(price)
                for code, _, price, _, _ in CRYPTO_SEEDS
            }
        )
        now = self._now()
        with self.database._get_connection() as connection:
            assets = connection.execute("SELECT code, tipo, supply FROM market_assets").fetchall()
            reset_count = 0
            for asset in assets:
                key = (str(asset["tipo"]), str(asset["code"]))
                if key not in defaults:
                    continue
                price = defaults[key]
                connection.execute(
                    """
                    UPDATE market_assets
                    SET previous_price = ?, price = ?, daily_change = 0, market_cap = ?, volume = 0, updated_at = ?
                    WHERE code = ? AND tipo = ?
                    """,
                    (price, price, round(price * float(asset["supply"]), 2), now, asset["code"], asset["tipo"]),
                )
                self._insert_history(connection, str(asset["code"]), str(asset["tipo"]), price, 0.0, "Reset de valores", now)
                reset_count += 1
            connection.commit()
        return {"assets_reset": reset_count}

    def reset_user_asset(self, user_id: int, tipo: str, code: str) -> dict[str, object]:
        tipo_key = tipo.strip().lower()
        if tipo_key not in {"stock", "crypto"}:
            raise ValueError("Tipo de investimento invalido.")
        asset = self._require_asset(code, tipo_key)
        table = self._wallet_table(tipo_key)
        now = self._now()
        with self.database._get_connection() as connection:
            wallet = self._get_wallet_row(connection, table, user_id, asset["code"])
            if wallet is None:
                return {"asset": asset, "removed": 0.0, "remaining": 0.0}
            removed = float(wallet["quantity"])
            connection.execute(f"DELETE FROM {table} WHERE user_id = ? AND code = ?", (user_id, asset["code"]))
            self._log_transaction(connection, user_id, tipo_key, "reset", asset["code"], removed, float(asset["price"]), 0.0, now)
            connection.commit()
        return {"asset": asset, "removed": removed, "remaining": 0.0}

    def adjust_price(self, tipo: str, code: str, new_price: float) -> sqlite3.Row:
        tipo_key = tipo.strip().lower()
        if tipo_key not in {"stock", "crypto"}:
            raise ValueError("Tipo de ativo invalido.")
        if not self._valid_positive(new_price):
            raise ValueError("Preco deve ser maior que zero.")
        asset = self._require_asset(code, tipo_key)
        now = self._now()
        previous = float(asset["price"])
        change_percent = round(((float(new_price) - previous) / previous) * 100, 2) if previous > 0 else 0.0
        with self.database._get_connection() as connection:
            connection.execute(
                """
                UPDATE market_assets
                SET previous_price = ?, price = ?, daily_change = ?, market_cap = ?, updated_at = ?
                WHERE code = ?
                """,
                (previous, round(float(new_price), 2), change_percent, round(float(new_price) * float(asset["supply"]), 2), now, asset["code"]),
            )
            self._insert_history(connection, asset["code"], tipo_key, round(float(new_price), 2), change_percent, "Ajuste manual de preco", now)
            connection.commit()

        return self.get_asset(asset["code"], tipo_key)

    def list_wallet(self, user_id: int, tipo: str) -> list[sqlite3.Row]:
        table = self._wallet_table(tipo)
        with self.database._get_connection() as connection:
            return list(
                connection.execute(
                    f"""
                    SELECT w.user_id, w.code, w.quantity, w.average_price,
                           a.name, a.price, a.daily_change, a.tipo
                    FROM {table} w
                    JOIN market_assets a ON a.code = w.code
                    WHERE w.user_id = ?
                    ORDER BY a.name ASC
                    """,
                    (user_id,),
                ).fetchall()
            )

    def portfolio_summary(self, user_id: int) -> dict[str, float]:
        invested = current = 0.0
        for tipo in ("stock", "crypto"):
            for row in self.list_wallet(user_id, tipo):
                quantity = float(row["quantity"])
                invested += quantity * float(row["average_price"])
                current += quantity * float(row["price"])
        return {
            "invested": round(invested, 2),
            "current": round(current, 2),
            "profit": round(current - invested, 2),
        }

    def ranking_investors(self, limit: int = 10) -> list[sqlite3.Row]:
        with self.database._get_connection() as connection:
            return list(
                connection.execute(
                    """
                    SELECT user_id, SUM(current_value) AS total_value
                    FROM (
                        SELECT w.user_id, w.quantity * a.price AS current_value
                        FROM stock_wallet w JOIN market_assets a ON a.code = w.code
                        UNION ALL
                        SELECT w.user_id, w.quantity * a.price AS current_value
                        FROM crypto_wallet w JOIN market_assets a ON a.code = w.code
                    )
                    GROUP BY user_id
                    ORDER BY total_value DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            )

    def market_history(self, code: str | None = None, limit: int = 12) -> list[sqlite3.Row]:
        query = "SELECT * FROM market_history"
        params: list[object] = []
        if code:
            query += " WHERE code = ?"
            params.append(code.upper())
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self.database._get_connection() as connection:
            return list(connection.execute(query, tuple(params)).fetchall())

    def market_transactions(self, limit: int = 10) -> list[sqlite3.Row]:
        with self.database._get_connection() as connection:
            return list(
                connection.execute(
                    "SELECT * FROM market_transactions ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            )

    @staticmethod
    def _cap_price_change(previous_price: float, suggested_price: float) -> float:
        if suggested_price > previous_price:
            return min(suggested_price, previous_price + 1000.0)
        return suggested_price

    def update_market(self, tipo: str) -> list[sqlite3.Row]:
        event = self._random_event(tipo)
        updated: list[sqlite3.Row] = []
        now = self._now()
        with self.database._get_connection() as connection:
            assets = connection.execute(
                "SELECT * FROM market_assets WHERE tipo = ?",
                (tipo,),
            ).fetchall()
            pressure_map = self._trade_pressure_map(connection, tipo)
            max_pressure = max(pressure_map.values(), default=1.0) or 1.0

            for asset in assets:
                code = str(asset["code"])
                low, high = VOLATILITY["crypto"] if tipo == "crypto" else VOLATILITY[str(asset["volatility"])]
                span = max(high - low, 0.01)
                std = span / 4
                base_change = random.gauss(0.0, std)

                pressure = pressure_map.get(code, 0.0)
                normalized_pressure = pressure / max_pressure if max_pressure > 0 else 0.0
                demand_bias = normalized_pressure * 0.06
                if pressure <= 0:
                    demand_bias -= 0.015

                previous = float(asset["price"])
                reference = self._reference_price(connection, code, previous)
                mean_reversion = ((reference - previous) / max(reference, 0.01)) * 0.03
                drift = 0.007 if tipo == "stock" else 0.012

                event_impact = float(event["impact"])
                if normalized_pressure < 0.15:
                    event_impact *= 0.65
                if tipo == "crypto" and event_impact > 0:
                    event_impact *= 1.15
                elif tipo == "crypto" and event_impact < 0:
                    event_impact *= 0.80

                change = base_change + demand_bias + mean_reversion + event_impact + drift
                change = max(min(change, high + 0.03), low - 0.03)

                suggested_price = max(round(previous * (1 + change), 2), 0.01)
                new_price = self._cap_price_change(previous, suggested_price)
                new_price = max(round(new_price, 2), 0.01)
                daily_change = round(((new_price - previous) / previous) * 100, 2)
                market_cap = round(new_price * float(asset["supply"]), 2)
                traded_volume = abs(pressure) * float(asset["supply"]) * 0.0005
                volume = round(float(asset["volume"]) + traded_volume + abs(change) * 100, 2)
                connection.execute(
                    """
                    UPDATE market_assets
                    SET previous_price = ?, price = ?, daily_change = ?,
                        market_cap = ?, volume = ?, updated_at = ?
                    WHERE code = ?
                    """,
                    (previous, new_price, daily_change, market_cap, volume, now, code),
                )
                self._insert_history(connection, code, tipo, new_price, daily_change, event["name"], now)

            connection.execute(
                """
                INSERT INTO market_events (name, impact, target, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (event["name"], event["impact"], tipo, now),
            )
            connection.commit()
            updated = self.list_assets(tipo)
        return updated

    @staticmethod
    def _reference_price(
        connection: sqlite3.Connection,
        code: str,
        fallback: float,
    ) -> float:
        row = connection.execute(
            """
            SELECT AVG(price) AS avg_price
            FROM market_history
            WHERE code = ?
              AND datetime(created_at) >= datetime('now', '-7 days')
            """,
            (code,),
        ).fetchone()
        if row is None or row["avg_price"] is None:
            return fallback
        return float(row["avg_price"])

    @staticmethod
    def _trade_pressure_map(connection: sqlite3.Connection, tipo: str) -> dict[str, float]:
        rows = connection.execute(
            """
            SELECT code,
                   SUM(CASE WHEN action = 'buy' THEN total ELSE 0 END) AS buy_total,
                   SUM(CASE WHEN action = 'sell' THEN total ELSE 0 END) AS sell_total
            FROM market_transactions
            WHERE tipo = ?
              AND datetime(created_at) >= datetime('now', '-48 hours')
            GROUP BY code
            """,
            (tipo,),
        ).fetchall()
        pressure: dict[str, float] = {}
        for row in rows:
            buy_total = float(row["buy_total"] or 0)
            sell_total = float(row["sell_total"] or 0)
            pressure[str(row["code"])] = buy_total - sell_total
        return pressure

    def distribute_dividends(self) -> list[dict[str, object]]:
        results: list[dict[str, object]] = []
        now = self._now()
        with self.database._get_connection() as connection:
            rows = connection.execute(
                """
                SELECT w.user_id, w.code, w.quantity, a.name, a.price, a.dividend_rate
                FROM stock_wallet w
                JOIN market_assets a ON a.code = w.code
                WHERE a.tipo = 'stock' AND a.dividend_rate > 0
                """
            ).fetchall()
            for row in rows:
                amount = round(float(row["quantity"]) * float(row["price"]) * float(row["dividend_rate"]), 2)
                if amount <= 0:
                    continue
                connection.execute(
                    "UPDATE users SET balance = balance + ? WHERE user_id = ?",
                    (amount, int(row["user_id"])),
                )
                self._log_transaction(connection, int(row["user_id"]), "stock", "dividend", str(row["code"]), float(row["quantity"]), float(row["price"]), amount, now)
                results.append({"user_id": int(row["user_id"]), "code": row["code"], "name": row["name"], "amount": amount})
            connection.commit()
        return results

    def _require_asset(self, code: str, tipo: str) -> sqlite3.Row:
        asset = self.get_asset(code.upper(), tipo)
        if asset is None:
            raise ValueError("Ativo nao encontrado nesse mercado.")
        return asset

    @staticmethod
    def _wallet_table(tipo: str) -> str:
        if tipo == "stock":
            return "stock_wallet"
        if tipo == "crypto":
            return "crypto_wallet"
        raise ValueError("Tipo de mercado invalido.")

    @staticmethod
    def _get_wallet_row(connection: sqlite3.Connection, table: str, user_id: int, code: str) -> sqlite3.Row | None:
        return connection.execute(
            f"SELECT * FROM {table} WHERE user_id = ? AND code = ?",
            (user_id, code),
        ).fetchone()

    @staticmethod
    def _insert_history(
        connection: sqlite3.Connection,
        code: str,
        tipo: str,
        price: float,
        change_percent: float,
        event_name: str | None,
        created_at: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO market_history (code, tipo, price, change_percent, event_name, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (code, tipo, price, change_percent, event_name, created_at),
        )

    @staticmethod
    def _log_transaction(
        connection: sqlite3.Connection,
        user_id: int,
        tipo: str,
        action: str,
        code: str,
        quantity: float,
        unit_price: float,
        total: float,
        created_at: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO market_transactions (
                user_id, tipo, action, code, quantity, unit_price, total, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, tipo, action, code, quantity, unit_price, total, created_at),
        )

    @staticmethod
    def _random_event(tipo: str) -> dict[str, object]:
        events = [
            ("Mercado estavel", 0.0),
            ("Bull Market", 0.03),
            ("Bear Market", -0.015),
            ("Incentivo Governamental", 0.02),
            ("Crise Economica", -0.03),
            ("Super Alta", 0.05),
            ("Crash Financeiro", -0.06),
        ]
        weights = [50, 18, 5, 14, 3, 7, 3]
        if tipo == "crypto":
            weights = [45, 20, 4, 15, 2, 10, 4]
        name, impact = random.choices(events, weights=weights, k=1)[0]
        if tipo == "crypto":
            impact *= 1.25
        return {"name": name, "impact": float(impact)}

    @staticmethod
    def _grant_achievements(
        connection: sqlite3.Connection,
        user_id: int,
        new_quantity: float,
        total: float,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        achievements = [("primeiro_investimento", "Primeiro Investimento")]
        if new_quantity >= 10:
            achievements.append(("acionista", "Acionista"))
        if total >= 100_000:
            achievements.append(("investidor_experiente", "Investidor Experiente"))
        if total >= 1_000_000:
            achievements.append(("magnata", "Magnata"))
        for code, title in achievements:
            connection.execute(
                """
                INSERT INTO achievements (user_id, code, title, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, code) DO NOTHING
                """,
                (user_id, code, title, now),
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _valid_positive(value: float) -> bool:
        return math.isfinite(float(value)) and float(value) > 0
