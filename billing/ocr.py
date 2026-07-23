from __future__ import annotations

import re

import pytesseract
from PIL import Image

_HASH_PATTERN = re.compile(r"\b[a-fA-F0-9]{64}\b")


def extract_tx_hash(image_file) -> str | None:
    text = pytesseract.image_to_string(Image.open(image_file))
    match = _HASH_PATTERN.search(text)
    return match.group(0) if match else None
