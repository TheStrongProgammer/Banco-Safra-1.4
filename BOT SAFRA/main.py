import discord

from src.bot import create_bot


def main() -> None:
    try:
        bot = create_bot()
        bot.run_from_env()
    except (discord.LoginFailure, RuntimeError) as exc:
        raise SystemExit(
            f"Falha ao autenticar com o Discord: {exc}\n"
            "Verifique o token em .env ou gere um novo token de bot no Discord Developer Portal."
        ) from exc


if __name__ == "__main__":
    main()
