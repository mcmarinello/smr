# SMR — Smart Money Radar

## O que é
Plataforma de mapeamento, avaliação e acompanhamento de carteiras da Hyperliquid.
Score multi-componente dual (raw vs deleveraged) que separa skill de alavancagem.
Motor de copy trading em modo simulação (paper). Ponte com TMT construída mas desligada.

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
├── hyperliquid_client/        # cliente HTTP/WS da Info API pública
├── wallets/                   # Wallet, Fill, Position, WalletMetricsWindow, WalletScore
├── discovery/                 # tasks de mapeamento amplo
├── tracking/                  # tasks de acompanhamento de alvo
├── alerts/                    # AlertRule, Notification, Telegram
├── copytrading/               # CopyTradingProfile, SimulatedTrade (paper)
├── bridge/                    # contrato de API com TMT (desligado)
├── dashboard/                 # views agregadas, KPIs
├── accounts/                  # User customizado, roles
└── docs/                      # MkDocs
```

## Regras
- Código em inglês, UI em pt-BR
- Todo score recalculável a partir do fill bruto
- Nenhuma ordem real enviada (V1 = paper only)
- Ponte TMT nasce desligada (TMT_BRIDGE_ENABLED=False)
- Rate limit HL tratado como restrição rígida
- timestamps UTC, conversão PT-BR só na apresentação
- .env para credenciais, nunca commitado
