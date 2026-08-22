# Banco Safra BOT

Bot de Discord em Python com foco em economia para Roleplay, usando `discord.py`, slash commands, SQLite persistente, investimentos interativos e notificacoes inteligentes por DM.

## Estrutura do projeto

```text
BOT SAFRA/
|-- main.py
|-- logo.png
|-- requirements.txt
|-- .env.example
|-- README.md
`-- src/
    |-- __init__.py
    |-- bot.py
    |-- config.py
    |-- database.py
    |-- economy.py
    |-- market_service.py
    |-- market_ui.py
    |-- investment_ui.py
    |-- investments.py
    |-- notification_ui.py
    |-- notifications.py
    `-- utils.py
```

## Comandos principais

- `/depositar valor`
- `/sacar valor`
- `/pagar usuario valor`
- `/saldo`
- `/credito`
- `/notificacoes`
- `/criar_conta`
- `/conta`
- `/definir senha area senha`
- `/gerente conta usuario`
- `/investir`
- `/resgatar [investimento_id]`
- `/investimentos`
- `/fundo investir subtipo valor`
- `/fundo sacar [investimento_id]`
- `/fundo status`
- `/acoes mercado`
- `/acoes comprar`
- `/acoes vender`
- `/acoes carteira`
- `/acoes detalhes codigo`
- `/crypto mercado`
- `/crypto comprar`
- `/crypto vender`
- `/crypto carteira`
- `/mercado historico [codigo]`
- `/carteira`
- `/ranking investidores`
- `/addmoney usuario valor`
- `/removemoney usuario valor`
- `/addcredito usuario valor`
- `/removecredito usuario valor`
- `/consultar saldo usuario`
- `/ajuda`

## Sistema de investimentos

### Central `/investir`

Abre um painel interativo com botoes para:

- `CDB`
- `Risco`
- `Fundos`
- `Acoes`
- `Criptomoedas`
- `Minha Carteira`
- `Mercado ao vivo`

### CDB

- `3 dias` com retorno de `+5%`
- `7 dias` com retorno de `+12%`
- O valor sai do saldo bancario e fica travado ate o vencimento
- Resgate pelo comando `/resgatar`

### Investimento de risco

- `Baixo risco`
- `Medio risco`
- `Alto risco`
- Resultado imediato com lucro ou perda

### Fundos

- `Conservador`
- `Moderado`
- `Agressivo`
- Variacao automatica em background
- Consulta pelo `/fundo status`

### Bolsa de valores e criptomoedas

- Empresas iniciais: `SFRA`, `CTMO`, `CTCO`, `CTMK`, `CTGM`
- Criptomoedas iniciais: `BTC`, `ETH`, `SFRC`, `CTC`
- Acoes atualizam automaticamente a cada 30 minutos
- Criptomoedas atualizam automaticamente a cada 15 minutos
- Dividendos de acoes sao distribuidos automaticamente
- Carteira consolidada em `/carteira`
- Ranking em `/ranking investidores`

## Sistema de notificacoes

### Central `/notificacoes`

Painel com botões para:

- ativar notificacoes
- desativar notificacoes
- ver status atual

### Notificacoes automaticas

O bot envia DM quando houver:

- recebimento de dinheiro
- envio de dinheiro confirmado
- lucro em investimento
- perda em investimento
- investimento liberado para resgate
- credito atualizado
- saldo baixo
- bonus recebido
- alerta de divida vencendo
- alerta de divida vencida

## Seguranca e gerente

- Usuarios comuns precisam informar senha para acessar areas protegidas
- Areas atuais: `saldo` e `investimentos`
- Administradores tambem passam pelo fluxo de senha nas areas protegidas
- A senha e definida pelo comando `/definir senha`
- A conta gerente e configurada por administrador em `/gerente conta`
- Toda perda em investimentos de risco e perdas automaticas de fundos vai para a conta gerente configurada

## Banco de dados

O bot cria automaticamente:

- tabela `users`
- tabela `investments`
- tabela `notificacoes`
- tabela `notificacoes_log`
- tabela `debts`
- tabelas `market_assets`, `market_history`, `stock_wallet`, `crypto_wallet`
- tabelas `market_transactions`, `market_events`, `achievements`

## Requisitos

- Python `3.12` recomendado
- Bot criado no Discord Developer Portal
- Slash commands habilitados

## Como rodar no VSCode

1. Abra a pasta `BOT SAFRA` no VSCode.
2. Crie o arquivo `.env` com base no `.env.example`:

```env
DISCORD_TOKEN=seu_token_do_bot
BOT_PREFIX=!
```

3. Crie e ative um ambiente virtual com Python 3.12:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
```

4. Instale as dependencias:

```powershell
pip install -r requirements.txt
```

5. Rode o bot:

```powershell
python main.py
```

## Observacoes

- O banco SQLite sera criado automaticamente em `data/banco_safra.db`
- O log de transacoes fica em `data/transactions.log`
- Os comandos financeiros agora publicam o resultado no canal apos a validacao da senha
- As DMs de notificacao respeitam as preferencias do usuario
- O bot trata falha de DM sem quebrar a execucao
- Os fundos e alertas automaticos sao verificados em loop
- Usuarios sao criados automaticamente no primeiro uso
- Os comandos administrativos exigem permissao de administrador

## Exemplos de uso

```text
/depositar 1500
/notificacoes
/investir
/resgatar 4
/fundo investir conservador 10000
/fundo status
/acoes mercado
/acoes comprar
/crypto mercado
/carteira
/consultar saldo @Fulano
```
