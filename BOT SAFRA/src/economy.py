from __future__ import annotations

from dataclasses import dataclass

from src.database import Database


@dataclass
class OperationResult:
    message: str
    wallet: float | None = None
    balance: float | None = None
    credit: float | None = None


class EconomyService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def deposit(self, user_id: int, amount: float) -> OperationResult:
        current_wallet = self.database.get_wallet(user_id)
        if current_wallet < amount:
            raise ValueError("Voce nao tem esse valor em maos para depositar.")

        new_wallet = self.database.update_wallet(user_id, -amount)
        new_balance = self.database.update_balance(user_id, amount)
        return OperationResult(
            message="Deposito realizado com sucesso.",
            wallet=new_wallet,
            balance=new_balance,
        )

    def withdraw(self, user_id: int, amount: float) -> OperationResult:
        current_balance = self.database.get_balance(user_id)
        if current_balance < amount:
            raise ValueError("Saldo insuficiente para realizar o saque.")

        new_balance = self.database.update_balance(user_id, -amount)
        new_wallet = self.database.update_wallet(user_id, amount)
        return OperationResult(
            message="Saque realizado com sucesso.",
            wallet=new_wallet,
            balance=new_balance,
        )

    def pay(self, sender_id: int, receiver_id: int, amount: float) -> OperationResult:
        if sender_id == receiver_id:
            raise ValueError("Voce nao pode transferir dinheiro para si mesmo.")

        current_wallet = self.database.get_wallet(sender_id)
        if current_wallet < amount:
            raise ValueError("Voce nao possui dinheiro suficiente em maos.")

        self.database.transfer_wallet(sender_id, receiver_id, amount)
        new_wallet = self.database.get_wallet(sender_id)
        return OperationResult(
            message="Transferencia realizada com sucesso.",
            wallet=new_wallet,
        )

    def add_money(self, user_id: int, amount: float) -> OperationResult:
        new_wallet = self.database.update_wallet(user_id, amount)
        return OperationResult(
            message="Saldo do usuario atualizado com sucesso.",
            wallet=new_wallet,
        )

    def remove_money(self, user_id: int, amount: float, source: str = "wallet") -> OperationResult:
        amount = round(float(amount), 2)
        source_value = getattr(source, "value", source)
        source_key = str(source_value).strip().lower()
        if source_key == "wallet":
            current_wallet = self.database.get_wallet(user_id)
            new_wallet = max(current_wallet - amount, 0)
            self.database.set_wallet(user_id, new_wallet)
            return OperationResult(
                message="Saldo do usuario reduzido com sucesso.",
                wallet=new_wallet,
            )
        if source_key == "balance":
            current_balance = self.database.get_balance(user_id)
            new_balance = max(current_balance - amount, 0)
            self.database.set_balance(user_id, new_balance)
            return OperationResult(
                message="Saldo bancario do usuario reduzido com sucesso.",
                balance=new_balance,
            )
        raise ValueError("Origem invalida para remover dinheiro.")

    def add_credit(self, user_id: int, amount: float) -> OperationResult:
        new_credit = self.database.update_credit(user_id, amount)
        return OperationResult(
            message="Credito do usuario atualizado com sucesso.",
            credit=new_credit,
        )

    def reset_all_money(self) -> dict[str, int]:
        with self.database._get_connection() as connection:
            total_users = int(
                connection.execute("SELECT COUNT(*) AS total FROM users").fetchone()["total"]
            )
            connection.execute("UPDATE users SET wallet = 0, balance = 0, credit = 0")
            connection.commit()
        return {"affected_users": total_users}

    def remove_credit(self, user_id: int, amount: float) -> OperationResult:
        current_credit = self.database.get_credit(user_id)
        new_credit = max(current_credit - amount, 0)
        self.database.set_credit(user_id, new_credit)
        return OperationResult(
            message="Credito do usuario reduzido com sucesso.",
            credit=new_credit,
        )

    def get_balance(self, user_id: int) -> float:
        return self.database.get_balance(user_id)

    def get_wallet(self, user_id: int) -> float:
        return self.database.get_wallet(user_id)

    def get_credit(self, user_id: int) -> float:
        return self.database.get_credit(user_id)

    def get_total_balance(self, user_id: int) -> float:
        return self.get_wallet(user_id) + self.get_balance(user_id)
