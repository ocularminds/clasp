"""Compact JWS (ES256) helpers with no third-party dependency.
KMS returns DER-encoded ECDSA signatures; JWS wants raw r||s."""
from __future__ import annotations

import base64
import json
from typing import Callable


class TokenError(ValueError):
    pass


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64u_decode(s: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
    except Exception as e:  # noqa: BLE001
        raise TokenError("bad base64url") from e


def _read_len(buf: bytes, i: int) -> tuple[int, int]:
    first = buf[i]
    i += 1
    if first < 0x80:
        return first, i
    n = first & 0x7F
    return int.from_bytes(buf[i:i + n], "big"), i + n


def der_to_raw(der: bytes, size: int = 32) -> bytes:
    if not der or der[0] != 0x30:
        raise TokenError("not a DER sequence")
    _, i = _read_len(der, 1)
    ints = []
    for _ in range(2):
        if der[i] != 0x02:
            raise TokenError("expected DER integer")
        length, i = _read_len(der, i + 1)
        val = der[i:i + length].lstrip(b"\x00")
        if len(val) > size:
            raise TokenError("integer too large")
        ints.append(val.rjust(size, b"\x00"))
        i += length
    return b"".join(ints)


def raw_to_der(raw: bytes, size: int = 32) -> bytes:
    if len(raw) != 2 * size:
        raise TokenError("bad raw signature length")

    def enc_int(b: bytes) -> bytes:
        b = b.lstrip(b"\x00") or b"\x00"
        if b[0] & 0x80:
            b = b"\x00" + b
        return b"\x02" + bytes([len(b)]) + b

    body = enc_int(raw[:size]) + enc_int(raw[size:])
    return b"\x30" + bytes([len(body)]) + body


def encode(header: dict, claims: dict, sign: Callable[[bytes], bytes]) -> str:
    h = b64u(json.dumps(header, separators=(",", ":"), sort_keys=True).encode())
    p = b64u(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
    signing_input = f"{h}.{p}".encode("ascii")
    return f"{h}.{p}.{b64u(sign(signing_input))}"


def split(token: str) -> tuple[dict, dict, bytes, bytes]:
    """Return (header, claims, signing_input, raw_signature). Does NOT verify."""
    if not isinstance(token, str) or token.count(".") != 2:
        raise TokenError("malformed compact JWS")
    h, p, s = token.split(".")
    try:
        header = json.loads(b64u_decode(h))
        claims = json.loads(b64u_decode(p))
    except (ValueError, TypeError) as e:
        raise TokenError("undecodable header/claims") from e
    if not isinstance(header, dict) or not isinstance(claims, dict):
        raise TokenError("header/claims must be objects")
    return header, claims, f"{h}.{p}".encode("ascii"), b64u_decode(s)
