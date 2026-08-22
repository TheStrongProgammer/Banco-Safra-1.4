from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional


class Database:
    def __init__(self, db_path: str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _get_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._get_connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    wallet REAL NOT NULL DEFAULT 0,
                    balance REAL NOT NULL DEFAULT 0,
                    credit REAL NOT NULL DEFAULT 0
                )
                """
            )
            user_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(users)").fetchall()
            }
            if "wallet" not in user_columns:
                connection.execute(
                    "ALTER TABLE users ADD COLUMN wallet REAL NOT NULL DEFAULT 0"
                )

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS investments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    tipo TEXT NOT NULL,
                    subtipo TEXT,
                    valor_inicial REAL NOT NULL,
                    valor_atual REAL NOT NULL,
                    data_inicio TEXT NOT NULL,
                    data_resgate TEXT,
                    status TEXT NOT NULL DEFAULT 'ativo',
                    ultima_atualizacao TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
                """
            )

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS notificacoes (
                    user_id INTEGER PRIMARY KEY,
                    ativo INTEGER NOT NULL DEFAULT 1,
                    receber_dm INTEGER NOT NULL DEFAULT 1,
                    receber_alertas_divida INTEGER NOT NULL DEFAULT 1,
                    receber_investimentos INTEGER NOT NULL DEFAULT 1,
                    receber_transferencias INTEGER NOT NULL DEFAULT 1,
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
                """
            )

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS notificacoes_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    tipo TEXT NOT NULL,
                    chave TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS debts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    valor REAL NOT NULL,
                    vencimento TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'ativo',
                    ultimo_alerta TEXT,
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
                """
            )

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS bot_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS user_passwords (
                    user_id INTEGER NOT NULL,
                    area TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    PRIMARY KEY (user_id, area),
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
                """
            )

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS account_profiles (
                    user_id INTEGER PRIMARY KEY,
                    nome_completo TEXT NOT NULL,
                    senha_rp_hash TEXT NOT NULL,
                    deposito_inicial REAL NOT NULL,
                    discord_id TEXT NOT NULL,
                    tipo_conta TEXT NOT NULL,
                    telefone_rp TEXT,
                    profissao_rp TEXT,
                    created_at TEXT NOT NULL,
                    next_fee_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'ativa',
                    account_number TEXT,
                    channel_id TEXT,
                    approval_message_id TEXT,
                    motivo_fechamento TEXT,
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
                """
            )
            profile_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(account_profiles)").fetchall()
            }
            if "account_number" not in profile_columns:
                connection.execute(
                    "ALTER TABLE account_profiles ADD COLUMN account_number TEXT"
                )
            if "channel_id" not in profile_columns:
                connection.execute(
                    "ALTER TABLE account_profiles ADD COLUMN channel_id TEXT"
                )
            if "approval_message_id" not in profile_columns:
                connection.execute(
                    "ALTER TABLE account_profiles ADD COLUMN approval_message_id TEXT"
                )
            if "motivo_fechamento" not in profile_columns:
                connection.execute(
                    "ALTER TABLE account_profiles ADD COLUMN motivo_fechamento TEXT"
                )
            connection.commit()

    def ensure_user(self, user_id: int) -> None:
        with self._get_connection() as connection:
            connection.execute(
                """
                INSERT INTO users (user_id)
                VALUES (?)
                ON CONFLICT(user_id) DO NOTHING
                """,
                (user_id,),
            )
            connection.execute(
                """
                INSERT INTO notificacoes (user_id)
                VALUES (?)
                ON CONFLICT(user_id) DO NOTHING
                """,
                (user_id,),
            )
            connection.commit()

    def get_user_data(self, user_id: int) -> sqlite3.Row:
        self.ensure_user(user_id)
        with self._get_connection() as connection:
            row = connection.execute(
                "SELECT user_id, wallet, balance, credit FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError("Falha ao carregar usuario do banco de dados.")
            return row

    def update_balance(self, user_id: int, amount: float) -> float:
        self.ensure_user(user_id)
        with self._get_connection() as connection:
            connection.execute(
                "UPDATE users SET balance = balance + ? WHERE user_id = ?",
                (amount, user_id),
            )
            connection.commit()
        return float(self.get_user_data(user_id)["balance"])

    def update_wallet(self, user_id: int, amount: float) -> float:
        self.ensure_user(user_id)
        with self._get_connection() as connection:
            connection.execute(
                "UPDATE users SET wallet = wallet + ? WHERE user_id = ?",
                (amount, user_id),
            )
            connection.commit()
        return float(self.get_user_data(user_id)["wallet"])

    def update_credit(self, user_id: int, amount: float) -> float:
        self.ensure_user(user_id)
        with self._get_connection() as connection:
            connection.execute(
                "UPDATE users SET credit = credit + ? WHERE user_id = ?",
                (amount, user_id),
            )
            connection.commit()
        return float(self.get_user_data(user_id)["credit"])

    def transfer_wallet(self, sender_id: int, receiver_id: int, amount: float) -> None:
        self.ensure_user(sender_id)
        self.ensure_user(receiver_id)
        with self._get_connection() as connection:
            connection.execute(
                "UPDATE users SET wallet = wallet - ? WHERE user_id = ?",
                (amount, sender_id),
            )
            connection.execute(
                "UPDATE users SET wallet = wallet + ? WHERE user_id = ?",
                (amount, receiver_id),
            )
            connection.commit()

    def set_balance(self, user_id: int, value: float) -> float:
        self.ensure_user(user_id)
        with self._get_connection() as connection:
            connection.execute(
                "UPDATE users SET balance = ? WHERE user_id = ?",
                (value, user_id),
            )
            connection.commit()
        return float(self.get_user_data(user_id)["balance"])

    def set_wallet(self, user_id: int, value: float) -> float:
        self.ensure_user(user_id)
        with self._get_connection() as connection:
            connection.execute(
                "UPDATE users SET wallet = ? WHERE user_id = ?",
                (value, user_id),
            )
            connection.commit()
        return float(self.get_user_data(user_id)["wallet"])

    def set_credit(self, user_id: int, value: float) -> float:
        self.ensure_user(user_id)
        with self._get_connection() as connection:
            connection.execute(
                "UPDATE users SET credit = ? WHERE user_id = ?",
                (value, user_id),
            )
            connection.commit()
        return float(self.get_user_data(user_id)["credit"])

    def get_balance(self, user_id: int) -> float:
        return float(self.get_user_data(user_id)["balance"])

    def get_wallet(self, user_id: int) -> float:
        return float(self.get_user_data(user_id)["wallet"])

    def get_credit(self, user_id: int) -> float:
        return float(self.get_user_data(user_id)["credit"])

    def has_balance(self, user_id: int, amount: float) -> bool:
        return self.get_balance(user_id) >= amount

    def has_wallet(self, user_id: int, amount: float) -> bool:
        return self.get_wallet(user_id) >= amount

    def get_user_row(self, user_id: int) -> Optional[sqlite3.Row]:
        with self._get_connection() as connection:
            return connection.execute(
                "SELECT user_id, wallet, balance, credit FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()

    def create_investment(
        self,
        *,
        user_id: int,
        tipo: str,
        subtipo: str | None,
        valor_inicial: float,
        valor_atual: float,
        data_inicio: str,
        data_resgate: str | None,
        status: str,
        ultima_atualizacao: str,
    ) -> int:
        self.ensure_user(user_id)
        with self._get_connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO investments (
                    user_id, tipo, subtipo, valor_inicial, valor_atual,
                    data_inicio, data_resgate, status, ultima_atualizacao
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    tipo,
                    subtipo,
                    valor_inicial,
                    valor_atual,
                    data_inicio,
                    data_resgate,
                    status,
                    ultima_atualizacao,
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def get_investment(self, investment_id: int) -> sqlite3.Row | None:
        with self._get_connection() as connection:
            return connection.execute(
                "SELECT * FROM investments WHERE id = ?",
                (investment_id,),
            ).fetchone()

    def get_user_investment(self, user_id: int, investment_id: int) -> sqlite3.Row | None:
        with self._get_connection() as connection:
            return connection.execute(
                "SELECT * FROM investments WHERE id = ? AND user_id = ?",
                (investment_id, user_id),
            ).fetchone()

    def list_investments(
        self,
        *,
        user_id: int | None = None,
        tipo: str | None = None,
        status: str | None = None,
    ) -> list[sqlite3.Row]:
        query = "SELECT * FROM investments WHERE 1=1"
        params: list[object] = []
        if user_id is not None:
            query += " AND user_id = ?"
            params.append(user_id)
        if tipo is not None:
            query += " AND tipo = ?"
            params.append(tipo)
        if status is not None:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY data_inicio ASC"

        with self._get_connection() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
            return list(rows)

    def list_active_funds(self) -> list[sqlite3.Row]:
        return self.list_investments(tipo="fundo", status="ativo")

    def update_investment_value(
        self,
        investment_id: int,
        *,
        valor_atual: float,
        ultima_atualizacao: str,
    ) -> None:
        with self._get_connection() as connection:
            connection.execute(
                """
                UPDATE investments
                SET valor_atual = ?, ultima_atualizacao = ?
                WHERE id = ?
                """,
                (valor_atual, ultima_atualizacao, investment_id),
            )
            connection.commit()

    def close_investment(
        self,
        investment_id: int,
        *,
        valor_atual: float,
        status: str,
        ultima_atualizacao: str,
    ) -> None:
        with self._get_connection() as connection:
            connection.execute(
                """
                UPDATE investments
                SET valor_atual = ?, status = ?, ultima_atualizacao = ?
                WHERE id = ?
                """,
                (valor_atual, status, ultima_atualizacao, investment_id),
            )
            connection.commit()

    def get_notification_preferences(self, user_id: int) -> sqlite3.Row:
        self.ensure_user(user_id)
        with self._get_connection() as connection:
            row = connection.execute(
                """
                SELECT user_id, ativo, receber_dm, receber_alertas_divida,
                       receber_investimentos, receber_transferencias
                FROM notificacoes
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError("Falha ao carregar preferencia de notificacoes.")
            return row

    def update_notification_preferences(
        self,
        user_id: int,
        *,
        ativo: bool | None = None,
        receber_dm: bool | None = None,
        receber_alertas_divida: bool | None = None,
        receber_investimentos: bool | None = None,
        receber_transferencias: bool | None = None,
    ) -> sqlite3.Row:
        self.ensure_user(user_id)
        updates: list[str] = []
        params: list[object] = []
        fields = {
            "ativo": ativo,
            "receber_dm": receber_dm,
            "receber_alertas_divida": receber_alertas_divida,
            "receber_investimentos": receber_investimentos,
            "receber_transferencias": receber_transferencias,
        }
        for field, value in fields.items():
            if value is None:
                continue
            updates.append(f"{field} = ?")
            params.append(1 if value else 0)

        if updates:
            params.append(user_id)
            with self._get_connection() as connection:
                connection.execute(
                    f"UPDATE notificacoes SET {', '.join(updates)} WHERE user_id = ?",
                    tuple(params),
                )
                connection.commit()

        return self.get_notification_preferences(user_id)

    def create_notification_log(
        self,
        *,
        user_id: int,
        tipo: str,
        chave: str,
        created_at: str,
    ) -> None:
        with self._get_connection() as connection:
            connection.execute(
                """
                INSERT INTO notificacoes_log (user_id, tipo, chave, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, tipo, chave, created_at),
            )
            connection.commit()

    def get_notification_log(self, user_id: int, chave: str) -> sqlite3.Row | None:
        with self._get_connection() as connection:
            return connection.execute(
                """
                SELECT * FROM notificacoes_log
                WHERE user_id = ? AND chave = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (user_id, chave),
            ).fetchone()

    def create_debt(
        self,
        *,
        user_id: int,
        valor: float,
        vencimento: str,
        status: str = "ativo",
        ultimo_alerta: str | None = None,
    ) -> int:
        self.ensure_user(user_id)
        with self._get_connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO debts (user_id, valor, vencimento, status, ultimo_alerta)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, valor, vencimento, status, ultimo_alerta),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def list_active_debts(self) -> list[sqlite3.Row]:
        with self._get_connection() as connection:
            rows = connection.execute(
                "SELECT * FROM debts WHERE status = 'ativo' ORDER BY vencimento ASC"
            ).fetchall()
            return list(rows)

    def update_debt_alert(self, debt_id: int, alert_key: str) -> None:
        with self._get_connection() as connection:
            connection.execute(
                "UPDATE debts SET ultimo_alerta = ? WHERE id = ?",
                (alert_key, debt_id),
            )
            connection.commit()

    def create_account_profile(
        self,
        *,
        user_id: int,
        nome_completo: str,
        senha_rp_hash: str,
        deposito_inicial: float,
        discord_id: str,
        tipo_conta: str,
        telefone_rp: str,
        profissao_rp: str,
        created_at: str,
        next_fee_at: str,
        status: str = "ativa",
        account_number: str | None = None,
        channel_id: str | None = None,
        approval_message_id: str | None = None,
    ) -> None:
        self.ensure_user(user_id)
        with self._get_connection() as connection:
            connection.execute(
                """
                INSERT INTO account_profiles (
                    user_id, nome_completo, senha_rp_hash, deposito_inicial, discord_id,
                    tipo_conta, telefone_rp, profissao_rp, created_at, next_fee_at, status,
                    account_number, channel_id, approval_message_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    nome_completo,
                    senha_rp_hash,
                    deposito_inicial,
                    discord_id,
                    tipo_conta,
                    telefone_rp,
                    profissao_rp,
                    created_at,
                    next_fee_at,
                    status,
                    account_number,
                    channel_id,
                    approval_message_id,
                ),
            )
            connection.commit()

    def update_account_profile(
        self,
        user_id: int,
        *,
        status: str | None = None,
        account_number: str | None = None,
        channel_id: str | None = None,
        approval_message_id: str | None = None,
        next_fee_at: str | None = None,
        motivo_fechamento: str | None = None,
    ) -> None:
        updates: list[str] = []
        params: list[object] = []
        fields = {
            "status": status,
            "account_number": account_number,
            "channel_id": channel_id,
            "approval_message_id": approval_message_id,
            "next_fee_at": next_fee_at,
            "motivo_fechamento": motivo_fechamento,
        }
        for field, value in fields.items():
            if value is None:
                continue
            updates.append(f"{field} = ?")
            params.append(value)
        if not updates:
            return
        params.append(user_id)
        with self._get_connection() as connection:
            connection.execute(
                f"UPDATE account_profiles SET {', '.join(updates)} WHERE user_id = ?",
                tuple(params),
            )
            connection.commit()

    def delete_account_profile(self, user_id: int) -> None:
        with self._get_connection() as connection:
            connection.execute(
                "DELETE FROM account_profiles WHERE user_id = ?",
                (user_id,),
            )
            connection.commit()

    def generate_account_number(self) -> str:
        import random

        with self._get_connection() as connection:
            for _ in range(100):
                number = f"{random.randint(10000, 99999)}"
                exists = connection.execute(
                    "SELECT 1 FROM account_profiles WHERE account_number = ?",
                    (number,),
                ).fetchone()
                if exists is None:
                    return number
        raise RuntimeError("Nao foi possivel gerar um numero de conta unico.")

    def list_pending_account_profiles(self) -> list[sqlite3.Row]:
        with self._get_connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM account_profiles
                WHERE status = 'pendente'
                ORDER BY created_at ASC
                """
            ).fetchall()
            return list(rows)

    def get_account_profile(self, user_id: int) -> sqlite3.Row | None:
        with self._get_connection() as connection:
            return connection.execute(
                "SELECT * FROM account_profiles WHERE user_id = ?",
                (user_id,),
            ).fetchone()

    def list_due_account_fees(self, due_at: str) -> list[sqlite3.Row]:
        with self._get_connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM account_profiles
                WHERE status = 'ativa' AND next_fee_at <= ?
                ORDER BY next_fee_at ASC
                """,
                (due_at,),
            ).fetchall()
            return list(rows)

    def update_account_fee_date(self, user_id: int, next_fee_at: str) -> None:
        with self._get_connection() as connection:
            connection.execute(
                "UPDATE account_profiles SET next_fee_at = ? WHERE user_id = ?",
                (next_fee_at, user_id),
            )
            connection.commit()

    def list_account_profiles(self) -> list[sqlite3.Row]:
        with self._get_connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM account_profiles
                ORDER BY created_at ASC
                """
            ).fetchall()
            return list(rows)

    def set_bot_setting(self, key: str, value: str) -> None:
        with self._get_connection() as connection:
            connection.execute(
                """
                INSERT INTO bot_settings (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )
            connection.commit()

    def get_bot_setting(self, key: str) -> str | None:
        with self._get_connection() as connection:
            row = connection.execute(
                "SELECT value FROM bot_settings WHERE key = ?",
                (key,),
            ).fetchone()
            return None if row is None else str(row["value"])

    def set_user_password(self, user_id: int, area: str, password_hash: str) -> None:
        self.ensure_user(user_id)
        with self._get_connection() as connection:
            connection.execute(
                """
                INSERT INTO user_passwords (user_id, area, password_hash)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id, area)
                DO UPDATE SET password_hash = excluded.password_hash
                """,
                (user_id, area, password_hash),
            )
            connection.commit()

    def get_user_password(self, user_id: int, area: str) -> str | None:
        self.ensure_user(user_id)
        with self._get_connection() as connection:
            row = connection.execute(
                """
                SELECT password_hash
                FROM user_passwords
                WHERE user_id = ? AND area = ?
                """,
                (user_id, area),
            ).fetchone()
            return None if row is None else str(row["password_hash"])
