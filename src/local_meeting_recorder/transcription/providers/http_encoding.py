"""Multipart/form-data encoding, stdlib only (no ``requests``)."""

from __future__ import annotations

import uuid
from pathlib import Path


def encode_multipart(fields: dict[str, str], file_field: str, file_path: Path) -> tuple[bytes, str]:
    """Build a ``multipart/form-data`` body with string ``fields`` plus one file field.
    Returns ``(body_bytes, content_type_header_value)``."""
    boundary = uuid.uuid4().hex
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n".encode()
        )
    parts.append(
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; '
            f'filename="{file_path.name}"\r\nContent-Type: audio/wav\r\n\r\n'
        ).encode()
    )
    parts.append(file_path.read_bytes())
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    body = b"".join(parts)
    return body, f"multipart/form-data; boundary={boundary}"
