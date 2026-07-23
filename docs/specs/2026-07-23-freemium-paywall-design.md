# Freemium / Paywall UX — Design

Data: 2026-07-23
Status: Aprovado, aguardando plano de implementação

## Contexto

Terceiro subsistema da série de SaaS-ificação do SMR (os dois anteriores —
[fundação multi-tenant](2026-07-23-multi-tenant-foundation-design.md) e
[billing via cripto TRC-20](2026-07-23-trc20-crypto-billing-design.md) — já
estão implementados e em produção). Hoje NENHUMA view do dashboard usa o
gating de assinatura (`SubscriptionRequiredMixin`/`subscription_required`)
construído na fundação — todas exigem só login. Este spec fecha essa lacuna:
define a fronteira entre o que um cliente `free` vê e o que exige assinatura
`active`, e adiciona a experiência de upsell (mascaramento de dado + popup)
quando ele esbarra num limite.

Objetivo, nas palavras do pedido original: "o cara poder ver uma baleia
operar, mas não conseguir pesquisar qual a carteira" — dar um gosto do
produto (KPIs, ranking de score) sem entregar o dado que tem valor real
(identidade da wallet), com popups de assine nos pontos de fricção.

## Decisões de escopo

- **Fronteira free vs pago** (por view do `dashboard`):
  - **Free**: `dashboard_home` (KPIs + discovery status + alertas recentes)
    e `discovery_ranking` (ranking completo de score) — ambos com endereço
    de wallet **mascarado** e sem link navegável pro perfil.
  - **Pago**: `wallet_profile`, `watchlist`, `alerts_history`,
    `settings_page`, `whale_copy_status` (+ seu endpoint JSON
    `whale_copy_api_status`) — bloqueio total via o gating já existente,
    redirecionando pra `/assine/` se acessado direto por URL.
  - Staff (`admin`/`operator`/`viewer`) sempre vê tudo — mesmo bypass do
    gating já implementado.
- **Mascaramento**: endereço completo (`0x7a3f1234...b21c`) vira
  `0x7a3f••••b21c` (6 primeiros + 4 últimos caracteres) nas duas views
  free. O elemento mascarado não é mais um link de navegação — é um
  gatilho de popup.
- **Popup**: JS puro (vanilla, sem framework — não existe nenhum hoje no
  dashboard), adicionado ao `base.html`. Um modal escondido por padrão;
  delegação de evento de clique em qualquer elemento com classe
  `js-paywall-trigger` faz `preventDefault()` e exibe o modal ("Essa
  informação é para assinantes" + botão "Assinar agora" apontando pra
  `/assinar/`, já construído no spec de billing cripto).
- **Links da sidebar** pras páginas pagas continuam como estão — clicar
  neles ainda navega e cai no redirect servidor pra `/assine/` (já
  funciona via o gating existente). O popup é só pros pontos específicos
  de dado mascarado (endereço), não pra navegação geral.
- **Sem limite de linhas** no ranking — a lista inteira aparece, só o
  endereço é mascarado. Sem contador de dias restantes, comparação de
  planos, ou qualquer UI de upsell mais elaborada — só o popup simples.
- **Fora de escopo, lacuna conhecida**: alertas via Telegram/e-mail
  continuam revelando a wallet completa mesmo pra cliente `free` — a
  camada `alerts/` não tem noção de assinatura ainda. Não resolvido
  neste spec. Achado pela revisão final da branch: o título dos
  alertas exibidos na própria `dashboard_home` (view free) também
  mostra `address[:8]` (2 caracteres a mais que a máscara de 6), sem
  expor o endereço completo — aceito como parte do mesmo gap acima,
  registrado aqui por completude.

## Implementação

**`billing/access.py`** ganha uma nova função:

```python
def has_full_access(user) -> bool:
    """True for staff roles and active-subscription customers alike."""
    return access_redirect(user) is None
```

Reaproveita a mesma lógica de bypass de staff e verificação de assinatura
que já existe em `access_redirect` — não duplica a regra de negócio.

**`dashboard/templatetags/dashboard_extras.py`** (novo):

```python
from django import template

register = template.Library()


@register.filter
def mask_address(address: str) -> str:
    if len(address) <= 10:
        return address
    return f"{address[:6]}••••{address[-4:]}"
```

**`dashboard/views.py`**:
- `dashboard_home` e `discovery_ranking` passam a incluir
  `"has_full_access": has_full_access(request.user)` no contexto.
- `wallet_profile`, `watchlist`, `alerts_history`, `settings_page`,
  `whale_copy_status`, `whale_copy_api_status` trocam o decorator
  `@login_required` por `@subscription_required` (de `billing.decorators`,
  já existente — internamente também cobre o caso não-autenticado,
  redirecionando pro login).

**Templates** (`dashboard_home.html`, `discovery_ranking.html`): cada
ponto que hoje renderiza `<a href="{% url 'wallet_profile' addr %}">`
passa a ser:

```html
{% if has_full_access %}
  <a class="addr mono" href="{% url 'wallet_profile' s.wallet.address %}">{{ s.wallet.address|truncatechars:14 }}</a>
{% else %}
  <a href="#" class="addr mono js-paywall-trigger">{{ s.wallet.address|mask_address }}</a>
{% endif %}
```

**`base.html`**: modal HTML (escondido via `display:none`, mesmo tema
escuro `#0f1117`/`#1a1d27`/`#4f9eff` dos templates de `registration/`) +
um `<script>` vanilla:

```javascript
document.addEventListener("click", function (event) {
  var trigger = event.target.closest(".js-paywall-trigger");
  if (!trigger) return;
  event.preventDefault();
  document.getElementById("paywall-modal").style.display = "flex";
});
document.getElementById("paywall-modal-close").addEventListener("click", function () {
  document.getElementById("paywall-modal").style.display = "none";
});
```

## Testes

- Unit: `has_full_access()` (staff sempre `True`, customer free `False`,
  customer active `True`) — espelha os casos já cobertos por
  `AccessRedirectTest`, mas testando o wrapper booleano.
- Unit: `mask_address` filter (endereço normal, endereço curto demais pra
  mascarar, string vazia).
- Integration: `dashboard_home`/`discovery_ranking` — customer free vê
  `has_full_access=False` no contexto e o HTML renderizado contém a
  classe `js-paywall-trigger` (não o link real); customer active/staff
  vê o link real, sem a classe de gatilho.
- Integration: as 6 views pagas — customer free é redirecionado pra
  `/assine/`; customer active e staff acessam normalmente (reaproveita o
  padrão já usado em `SubscriptionGatingViewTest` da fundação
  multi-tenant).
