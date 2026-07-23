# Multi-tenant Foundation — Design

Data: 2026-07-23
Status: Aprovado, aguardando plano de implementação

## Contexto

O SMR hoje é single-tenant do ponto de vista de negócio: o modelo `accounts.User`
existe só para diferenciar papéis internos da equipe que opera a plataforma
(`admin` / `operator` / `viewer`). Não há conceito de cliente pagante, assinatura,
favoritos por usuário ou credenciais de corretora por usuário.

Este spec é o primeiro de uma série de subsistemas necessários para transformar o
SMR em um produto SaaS multi-cliente:

1. **Fundação multi-tenant** (este spec) — modelo de cliente, assinatura, favoritos,
   placeholder de credenciais de corretora, cadastro/login, verificação de e-mail
   (estruturada mas desligada), gating de acesso por assinatura.
2. Casca multi-DEX (independente, spec futuro)
3. Score dinâmico de atividade (independente, spec futuro)
4. Billing via Stripe (depende deste spec)
5. Billing via cripto TRC-20 (depende deste spec)
6. Freemium / paywall UX (depende deste spec, consome billing)

Escopo deste spec: só o item 1. Execução de copy trading multi-corretora,
pagamento real (Stripe/TRC-20) e a UX de paywall completa (blur, popups de upsell)
ficam para specs subsequentes.

## Decisões de escopo (resumo das perguntas respondidas)

- **Modelo de tenant:** uma conta = uma pessoa física (B2C simples). Sem conceito
  de organização/equipe compartilhando assinatura.
- **Relação com o `accounts.User` existente:** mesmo model, novo valor de `role`
  (`customer`). Um único sistema de auth/login para staff interno e clientes.
- **Favoritos:** não alteram o que o motor de tracking/discovery monitora — o
  universo de baleias rastreadas continua global. Favorito é só uma tabela de
  ligação `User`–`Wallet` para personalizar dashboard/alertas do cliente.
- **Credenciais de corretora (Binance/Bybit etc.):** só placeholder de schema
  neste spec (model + tela de cadastro). Lógica de conexão/execução real fica
  para o spec do motor de copy trading multi-corretora.
- **Estados de assinatura:** enum simples `free` / `active` / `expired`. Sem
  `trialing` nesta v1.
- **Verificação de e-mail:** estrutura completa (token, view, campo
  `email_verified`), mas desligada por padrão via flag `EMAIL_VERIFICATION_REQUIRED`
  (env var, default `False`), porque ainda não há provedor de e-mail configurado.
  Envio usa o `EMAIL_BACKEND` padrão do Django (console backend em dev/sem
  provedor configurado) — não bloqueia nada até o provedor existir e a flag ser
  ligada.
- **Estrutura de app:** novo app `billing/` contendo `CustomerProfile`,
  `ExchangeCredential` e `Favorite`. `accounts/` continua só com o `User` e roles
  internas.

## Modelo de dados (`billing/models.py`)

```python
class CustomerProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="customer_profile")

    class Status(models.TextChoices):
        FREE = "free", "Free"
        ACTIVE = "active", "Active"
        EXPIRED = "expired", "Expired"

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.FREE)

    class Interval(models.TextChoices):
        MONTHLY = "monthly", "Mensal"
        ANNUAL = "annual", "Anual"

    plan_interval = models.CharField(max_length=20, choices=Interval.choices, null=True, blank=True)
    current_period_end = models.DateTimeField(null=True, blank=True)
    email_verified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)


class ExchangeCredential(models.Model):
    # placeholder de schema — sem lógica de conexão neste spec
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="exchange_credentials")
    exchange = models.CharField(max_length=20)  # "binance", "bybit", ...
    api_key_encrypted = models.TextField()
    api_secret_encrypted = models.TextField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)


class Favorite(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="favorites")
    wallet = models.ForeignKey("wallets.Wallet", on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "wallet")
```

Chaves de corretora são criptografadas em repouso (texto plano nunca persistido).
Mecanismo exato (`cryptography.Fernet` com chave derivada, ou lib tipo
`django-fernet-fields`) é decisão de implementação, não deste spec.

`accounts.User.Role` ganha um quarto valor, `CUSTOMER`. `CustomerProfile` só
existe (é criado) para usuários com esse role.

## Cadastro, login e verificação de e-mail

- View pública de signup (`/signup/`): cria `User(role=CUSTOMER)` +
  `CustomerProfile(status=FREE, email_verified=False)` em uma transação atômica
  (sem signal — explícito, fácil de testar).
- Login continua usando `django.contrib.auth` normalmente, serve staff e
  customer — diferenciação é só pelo `role`.
- Verificação de e-mail reaproveita o padrão de "esqueci minha senha" do Django
  (`PasswordResetTokenGenerator`): token assinado com prazo de validade, sem
  tabela nova. View pública `/verify-email/<uidb64>/<token>/` marca
  `email_verified=True`.
- `send_verification_email(user)` é chamado no signup, mas seu efeito depende do
  `EMAIL_BACKEND` configurado. Sem provedor real, usa
  `django.core.mail.backends.console.EmailBackend` (só loga, não falha, não
  bloqueia o cadastro).
- Flag `EMAIL_VERIFICATION_REQUIRED` (env var, default `False`) controla se o
  gating de acesso (próxima seção) exige `email_verified=True`. Hoje `False`.

## Gating de acesso por assinatura

- `SubscriptionRequiredMixin` (class-based views) e decorator
  `@subscription_required` (function-based), aplicáveis às views do `dashboard`
  que exigem assinatura ativa.
- Staff (`role` em `admin`/`operator`/`viewer`) sempre passa — gating só vale
  para `role=CUSTOMER`.
- Se `EMAIL_VERIFICATION_REQUIRED=True` e `email_verified=False` → redireciona
  para tela de "verifique seu e-mail" (hoje inofensivo, flag desligada).
- Se `status != ACTIVE` → redireciona para tela de assinatura/upsell básica
  (a UX completa de paywall — blur, popups — é o spec de freemium, item 6 da
  lista acima).
- Expiração automática: task Celery periódica `billing.tasks.expire_subscriptions`
  (fila existente, rodando a cada hora) varre `CustomerProfile` com
  `status=ACTIVE` e `current_period_end < now()` e muda para `EXPIRED`. Isso
  garante que o bloqueio acontece mesmo sem o cliente logar.

## Testes

- Unit: transições de status de `CustomerProfile` (free→active→expired);
  `unique_together` de `Favorite`; criptografia/decriptografia de
  `ExchangeCredential` (garantir que texto plano nunca é persistido); geração e
  validação de token de verificação (válido, expirado, adulterado).
- Integration: signup end-to-end (cria User+Profile, dispara e-mail no console
  backend, verifica); `expire_subscriptions` task (fixtures com
  `current_period_end` passado/futuro); `SubscriptionRequiredMixin` (customer
  free bloqueado, customer active passa, staff sempre passa).

## Fora de escopo (specs futuros)

- Integração real com Stripe e TRC-20 (validação on-chain de hash de transação).
- UX completa de freemium/paywall (blur de conteúdo, popups de upsell contextual).
- Lógica de conexão/execução real das credenciais de corretora
  (`ExchangeCredential` fica só schema).
- Casca multi-DEX (Aster, Backpack) e score dinâmico de atividade — independentes
  deste spec, podem ser feitos em paralelo.
