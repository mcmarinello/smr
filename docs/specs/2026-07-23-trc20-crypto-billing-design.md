# Billing via cripto (TRC-20 USDT) — Design

Data: 2026-07-23
Status: Aprovado, aguardando plano de implementação

## Contexto

Segundo subsistema da série de SaaS-ificação do SMR (o primeiro foi a
[fundação multi-tenant](2026-07-23-multi-tenant-foundation-design.md), já
implementada). Este spec cobre pagamento de assinatura via USDT na rede
Tron (TRC-20), como alternativa ao cartão de crédito (Stripe, spec futuro).

Objetivo: cliente escolhe plano (mensal/anual), paga em USDT-TRC20 pra uma
wallet do SMR, confirma o pagamento (colando o hash da transação ou
enviando um print), o sistema valida direto na blockchain e libera o
acesso — sem intermediário, com a menor fricção possível pro cliente.

## Decisões de escopo

- **Custódia:** wallet própria do SMR (self-custody), não um gateway
  terceirizado. Só o endereço público fica no sistema — a chave privada
  nunca é necessária pra *receber* USDT-TRC20, só pra sacar depois (fora
  do SMR, numa wallet separada).
- **Identificação do pagamento:** sem truque de valor único por cobrança
  (descartado — clientes que pagam via corretora frequentemente arredondam
  o valor, quebrando o casamento). Identificação é 100% via hash da
  transação, que já é globalmente único e contém remetente/valor/data.
- **Confirmação do cliente:** hash obrigatório, em dois formatos possíveis
  — colar o texto do hash, ou enviar um print da transação (OCR extrai o
  hash da imagem). Sem poller/scanner automático rodando em background —
  a verificação acontece sob demanda, no momento em que o cliente
  submete o hash (síncrono, "tempo real" como pedido).
- **OCR:** Tesseract local (self-hosted, grátis), não serviço de nuvem —
  o alvo é um padrão bem definido (64 caracteres hex), não reconhecimento
  de texto livre, então a precisão local é suficiente.
- **Falha de OCR:** erro amigável pedindo pra colar o hash manualmente.
  Sem fila de revisão manual — se OCR falha, o cliente tem que digitar.
- **Preço:** valores fixos em settings (`TRC20_MONTHLY_PRICE_USDT=10.00`,
  `TRC20_ANNUAL_PRICE_USDT=100.00`), sem model de planos com histórico —
  YAGNI pra v1 com só duas opções.
- **Código promocional:** desconto percentual, reutilizável até um limite
  de usos configurável (ou ilimitado), com expiração opcional. Aplicado
  na criação do pedido pendente, reduzindo `expected_amount_usdt`.
- **Pedido pendente:** ao escolher o plano, cria-se um `CryptoPayment`
  (status `pending`, valor esperado já com desconto aplicado, expira em
  30 minutos por padrão) — evita ambiguidade de "esse hash é pra qual
  plano" e permite expirar cobranças não pagas.
- **Sub/superpagamento:** valor recebido ≥ esperado é aceito (sem
  reembolso de sobra). Valor recebido < esperado é rejeitado.
- **App:** tudo em `billing/`, junto do que já existe (`CustomerProfile`,
  `ExchangeCredential`, `Favorite`).

## Modelo de dados (`billing/models.py`)

```python
class PromoCode(BaseModel):
    code = models.CharField(max_length=32, unique=True)
    discount_percent = models.PositiveIntegerField(validators=[MaxValueValidator(100)])
    max_uses = models.PositiveIntegerField(null=True, blank=True)  # None = ilimitado
    uses_count = models.PositiveIntegerField(default=0)
    valid_until = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    def is_valid(self) -> bool:
        if not self.is_active:
            return False
        if self.valid_until and timezone.now() > self.valid_until:
            return False
        if self.max_uses is not None and self.uses_count >= self.max_uses:
            return False
        return True


class CryptoPayment(BaseModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        CONFIRMED = "confirmed", "Confirmed"
        EXPIRED = "expired", "Expired"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="crypto_payments")
    plan_interval = models.CharField(max_length=20, choices=CustomerProfile.Interval.choices)
    expected_amount_usdt = models.DecimalField(max_digits=12, decimal_places=6)
    promo_code = models.ForeignKey(PromoCode, null=True, blank=True, on_delete=models.SET_NULL)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    tx_hash = models.CharField(max_length=64, unique=True, null=True, blank=True)
    expires_at = models.DateTimeField()
    confirmed_at = models.DateTimeField(null=True, blank=True)
```

`tx_hash` com `unique=True` garante no nível do banco que a mesma
transação nunca ativa duas assinaturas (Postgres permite múltiplos `NULL`
na mesma coluna única, então pedidos ainda não pagos não colidem entre si).

## Fluxo de pagamento

1. **`SubscribeChoosePlanView`** (GET/POST): cliente escolhe mensal/anual
   e opcionalmente informa um código promocional. No POST: valida o promo
   code (`PromoCode.objects.get(code=..., is_valid())`), calcula
   `expected_amount_usdt` com o desconto aplicado, cria
   `CryptoPayment(status=PENDING, expires_at=now()+30min)`, redireciona
   pra tela de pagamento.
2. **`CryptoPaymentDetailView`**: mostra o endereço da wallet TRC-20 do
   SMR (`settings.TRC20_WALLET_ADDRESS`), o valor exato esperado, e o
   formulário de confirmação (campo de hash em texto **ou** upload de
   print).
3. **`CryptoPaymentVerifyView`** (POST):
   - Se veio print: `billing.ocr.extract_tx_hash()` roda OCR na imagem
     procurando um padrão de 64 caracteres hex. Não achou → re-renderiza
     o formulário com erro amigável ("Não conseguimos ler o hash dessa
     imagem automaticamente. Desculpe pelo inconveniente — cole o hash da
     transação manualmente abaixo.") e o campo de texto continua
     disponível na mesma tela.
   - Com o hash em mãos (texto ou OCR): `billing.tron.verify_transaction()`
     consulta a TronGrid pra essa transação específica.
   - Confere: transação existe e está confirmada; é um transfer do
     contrato USDT-TRC20 (`settings.TRC20_USDT_CONTRACT_ADDRESS`);
     destinatário é `settings.TRC20_WALLET_ADDRESS`; valor recebido ≥
     `expected_amount_usdt`; hash ainda não usado em outro `CryptoPayment`
     (checagem explícita antes do `save()`, pra dar um erro amigável em
     vez de deixar estourar `IntegrityError`).
   - Tudo certo: marca `CryptoPayment.status=CONFIRMED`,
     `confirmed_at=now()`; ativa/atualiza `CustomerProfile`
     (`status=ACTIVE`, `plan_interval=...`,
     `current_period_end=now()+30d` ou `+365d`); incrementa
     `PromoCode.uses_count` se usado; redireciona pro dashboard com
     mensagem de sucesso.
   - Qualquer checagem falhar: mensagem de erro específica pt-BR
     ("transação não encontrada ou ainda não confirmada — tente
     novamente em alguns segundos", "valor recebido é menor que o
     esperado", "essa transação já foi usada em outra cobrança").

## Cliente Tron (`billing/tron.py`)

Segue o padrão já usado em `hyperliquid_client` (httpx + tenacity):

```python
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential
from decimal import Decimal
from django.conf import settings


class TronVerificationError(Exception):
    """Carrega uma mensagem pt-BR pronta pra exibir ao usuário."""


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
def _fetch_transaction_events(tx_hash: str) -> list[dict]:
    # TronGrid's /v1/transactions/{id}/events endpoint returns decoded
    # event logs (from/to/value already resolved as strings, not raw
    # ABI-encoded contract-call data) — avoids hand-rolling ABI decoding.
    response = httpx.get(
        f"{settings.TRONGRID_API_URL}/v1/transactions/{tx_hash}/events",
        headers={"TRON-PRO-API-KEY": settings.TRONGRID_API_KEY} if settings.TRONGRID_API_KEY else {},
        timeout=10,
    )
    response.raise_for_status()
    return response.json().get("data", [])


def verify_transaction(tx_hash: str, expected_amount: Decimal) -> Decimal:
    """Retorna o valor efetivamente recebido, ou levanta TronVerificationError."""
    events = _fetch_transaction_events(tx_hash)
    # Filtra o evento Transfer do contrato USDT-TRC20
    # (settings.TRC20_USDT_CONTRACT_ADDRESS) cujo `to` seja
    # settings.TRC20_WALLET_ADDRESS; decodifica `value` (USDT tem 6 casas
    # decimais, então amount = Decimal(value) / Decimal(10**6)). Nenhum
    # evento correspondente → TronVerificationError("transação não
    # encontrada ou ainda não confirmada").
    if amount < expected_amount:
        raise TronVerificationError(
            f"Valor recebido ({amount} USDT) é menor que o esperado ({expected_amount} USDT)."
        )
    return amount
```

## OCR (`billing/ocr.py`)

```python
import re
import pytesseract
from PIL import Image

_HASH_PATTERN = re.compile(r"\b[a-fA-F0-9]{64}\b")


def extract_tx_hash(image_file) -> str | None:
    text = pytesseract.image_to_string(Image.open(image_file))
    match = _HASH_PATTERN.search(text)
    return match.group(0) if match else None
```

Novas dependências: `pytesseract` + `Pillow` (Python), e o binário
`tesseract-ocr` instalado via `apt-get` no `Dockerfile` (imagem base
Debian/slim).

## Configuração (`.env` / `smr/settings.py`)

```
TRC20_WALLET_ADDRESS=
TRC20_USDT_CONTRACT_ADDRESS=TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t
TRONGRID_API_URL=https://api.trongrid.io
TRONGRID_API_KEY=
TRC20_MONTHLY_PRICE_USDT=10.00
TRC20_ANNUAL_PRICE_USDT=100.00
TRC20_PAYMENT_EXPIRY_MINUTES=30
```

`TRC20_WALLET_ADDRESS` é só o endereço público — a chave privada nunca
entra no sistema.

## Limpeza automática

`billing.tasks.expire_crypto_payments` (Celery, fila `billing`, a cada 5
minutos — janela de pagamento é curta, 30min por padrão, não faz sentido
checar de hora em hora como a expiração de assinatura): marca
`CryptoPayment` com `status=PENDING` e `expires_at < now()` como
`EXPIRED`.

## Testes

- Unit: `PromoCode.is_valid()` (inativo, expirado, limite de uso
  atingido, válido); cálculo de `expected_amount_usdt` com/sem desconto;
  `tron.verify_transaction()` com respostas HTTP mockadas (sucesso,
  destinatário errado, contrato errado, valor insuficiente, não
  confirmada); `ocr.extract_tx_hash()` com imagens de exemplo/mock do
  `pytesseract` (hash legível, hash ilegível, sem hash na imagem).
- Integration: fluxo completo (criar pedido pendente → submeter hash com
  TronGrid mockada → `CustomerProfile` ativado); hash inválido/não
  encontrado → erro, perfil inalterado; hash já usado → erro, segunda
  tentativa rejeitada; `expire_crypto_payments` (pendente expirado vs
  ainda dentro da janela vs já confirmado).

## Fora de escopo (specs futuros)

- Billing via Stripe (cartão).
- UX de freemium/paywall completa (essa spec só entrega o mecanismo de
  pagamento em si).
- Fila de revisão manual para prints com OCR ilegível (descartada por
  decisão explícita — erro + reenvio manual em vez disso).
- Reembolso de sobrepagamento.
