# SMR — Smart Money Radar

## O que é
Plataforma de mapeamento, avaliação e acompanhamento de carteiras da Hyperliquid.
Score multi-componente dual (raw vs deleveraged) que separa skill de alavancagem.
Motor de copy trading com **modo paper (padrão)** e **modo live (opt-in via HL_LIVE_EXECUTION=True)**.
Ponte com TMT construída mas desligada.

## Stack
- Python 3.13+ / Django 6.0+ / PostgreSQL 16 + TimescaleDB
- Celery + Redis (filas: discovery, tracking, scoring, alerts)
- Hyperliquid Public API (REST + WebSocket)
- pandas / numpy / websockets
- Docker Swarm + Traefik (deploy na VPS Contabo)

## Fonte única de verdade
`@PRD_SMR.md` — citar em todo prompt de execução de sprint.

## Estrutura do projeto
```
smr/
├── manage.py
├── requirements.txt
├── .env
├── smr/                     # settings, urls, celery.py
├── wallet_engine/            # motor puro Python - score, deleveraging, discovery, copy sim
├── hyperliquid_client/        # cliente HTTP/WS + UserFillsSubscriber (whale detection)
├── wallets/                   # Wallet, Fill, Position, WalletMetricsWindow, WalletScore
├── discovery/                 # tasks de mapeamento amplo
├── tracking/                  # tasks de acompanhamento de alvo
├── alerts/                    # AlertRule, Notification, Telegram
├── copytrading/               # CopyTradingProfile, SimulatedTrade, executor (dry+live), risk_manager
├── bridge/                    # contrato de API com TMT (desligado)
├── dashboard/                 # views agregadas, KPIs, whale_copy_status
├── accounts/                  # User customizado, roles
└── docs/                      # MkDocs
```

## Regras
- Código em inglês, UI em pt-BR
- Todo score recalculável a partir do fill bruto
- Nenhuma ordem real enviada quando HL_LIVE_EXECUTION=False (padrão)
- Live execution requer ação explícita: HL_LIVE_EXECUTION=True + HL_PRIVATE_KEY
- Ponte TMT nasce desligada (TMT_BRIDGE_ENABLED=False)
- Rate limit HL tratado como restrição rígida
- timestamps UTC, conversão PT-BR só na apresentação
- .env para credenciais, nunca commitado
