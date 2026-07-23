from __future__ import annotations

from django import template

register = template.Library()


@register.filter
def mask_address(address: str) -> str:
    if len(address) <= 10:
        return address
    return f"{address[:6]}••••{address[-4:]}"
