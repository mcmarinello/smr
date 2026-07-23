from __future__ import annotations

from decimal import Decimal

import httpx
from django.conf import settings
from tenacity import retry, stop_after_attempt, wait_exponential

USDT_DECIMALS = 6


class TronVerificationError(Exception):
    """Message is a ready-to-display pt-BR string explaining the failure."""


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8), reraise=True)
def _fetch_transaction_events(tx_hash: str) -> list[dict]:
    headers = {"TRON-PRO-API-KEY": settings.TRONGRID_API_KEY} if settings.TRONGRID_API_KEY else {}
    response = httpx.get(
        f"{settings.TRONGRID_API_URL}/v1/transactions/{tx_hash}/events",
        headers=headers,
        timeout=10,
    )
    response.raise_for_status()
    return response.json().get("data", [])


def verify_transaction(tx_hash: str, expected_amount: Decimal) -> Decimal:
    """Returns the actual amount received (USDT), or raises TronVerificationError."""
    try:
        events = _fetch_transaction_events(tx_hash)
    except httpx.HTTPError as exc:
        raise TronVerificationError(
            "Não foi possível consultar a blockchain agora. Tente novamente em alguns instantes."
        ) from exc

    for event in events:
        if event.get("event_name") != "Transfer":
            continue
        if event.get("contract_address") != settings.TRC20_USDT_CONTRACT_ADDRESS:
            continue
        result = event.get("result", {})
        if result.get("to") != settings.TRC20_WALLET_ADDRESS:
            continue

        amount = Decimal(result["value"]) / Decimal(10**USDT_DECIMALS)
        if amount < expected_amount:
            raise TronVerificationError(
                f"Valor recebido ({amount} USDT) é menor que o esperado ({expected_amount} USDT)."
            )
        return amount

    raise TronVerificationError(
        "Transação não encontrada ou ainda não confirmada. Tente novamente em alguns segundos."
    )
