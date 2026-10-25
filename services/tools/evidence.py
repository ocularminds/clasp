"""Truce evidence: a multimodal Bedrock call that only *reports*. AgentSafe decides.

Output is strict JSON: {code_visible, code_matches, same_item_as_dispatch, damage_observed, confidence, notes}.
EVIDENCE_MODE=stub returns a canned verdict (override with EVIDENCE_STUB_JSON) so the demo runs offline.
"""
from __future__ import annotations

import json
import os
from typing import Any

from ..common.config import settings
from ..common.log import Log

log = Log("tools.evidence")

FIELDS = ("code_visible", "code_matches", "same_item_as_dispatch", "damage_observed", "confidence", "notes")
DEFAULT_STUB = {"code_visible": True, "code_matches": True, "same_item_as_dispatch": True, "damage_observed": True,
                "confidence": 0.91, "notes": "stub verdict (EVIDENCE_MODE=stub)"}

PROMPT = """You are an evidence examiner for a marketplace dispute. You do not decide anything; you report.
Image 1 is the seller's DISPATCH photo of the item. Image 2 is the buyer's LIVE photo, which must show a
handwritten or displayed one-time code next to the item. The expected code is: {code}

Report strictly as JSON with exactly these keys and no prose outside the JSON:
{{"code_visible": bool, "code_matches": bool, "same_item_as_dispatch": bool, "damage_observed": bool,
  "confidence": number between 0 and 1, "notes": short string}}
Rules: code_matches is true only if the visible code equals {code} character for character.
If the live photo looks generated, edited, or is a photo of a screen, say so in notes and lower confidence."""


def _coerce(raw: dict) -> dict:
    out = {k: raw.get(k) for k in FIELDS}
    for k in ("code_visible", "code_matches", "same_item_as_dispatch", "damage_observed"):
        out[k] = bool(out[k]) if isinstance(out[k], (bool, int)) else str(out[k]).strip().lower() == "true"
    try:
        out["confidence"] = max(0.0, min(1.0, float(out["confidence"])))
    except (TypeError, ValueError):
        out["confidence"] = 0.0
    out["notes"] = str(out.get("notes") or "")[:500]
    return out


def _stub() -> dict:
    raw = os.environ.get("EVIDENCE_STUB_JSON")
    return _coerce(json.loads(raw)) if raw else dict(DEFAULT_STUB)


def _mime(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return "jpeg"


def analyze(expected_code: str, dispatch_photo: bytes | None, live_photo: bytes, correlation_id: str = "") -> dict:
    if settings.evidence_mode != "bedrock":
        verdict = _stub()
        log.info("evidence.stub", correlation_id=correlation_id or None, **verdict)
        return verdict
    from ..common.aws import client

    content: list[dict[str, Any]] = [{"text": PROMPT.format(code=expected_code)}]
    if dispatch_photo:
        content.append({"text": "Image 1 (dispatch photo):"})
        content.append({"image": {"format": _mime(dispatch_photo), "source": {"bytes": dispatch_photo}}})
    else:
        content.append({"text": "Image 1 is unavailable; set same_item_as_dispatch to false."})
    content.append({"text": "Image 2 (buyer's live photo):"})
    content.append({"image": {"format": _mime(live_photo), "source": {"bytes": live_photo}}})
    kwargs: dict[str, Any] = {
        "modelId": settings.vision_model_id,
        "messages": [{"role": "user", "content": content}],
        "inferenceConfig": {"maxTokens": 400, "temperature": 0},
    }
    if settings.guardrail_id:
        kwargs["guardrailConfig"] = {"guardrailIdentifier": settings.guardrail_id, "guardrailVersion": settings.guardrail_version}
    resp = client("bedrock-runtime").converse(**kwargs)
    text = "".join(c.get("text", "") for c in resp["output"]["message"]["content"])
    start, end = text.find("{"), text.rfind("}")
    try:
        verdict = _coerce(json.loads(text[start:end + 1]))
    except (ValueError, json.JSONDecodeError):
        verdict = _coerce({"notes": f"model returned non-JSON: {text[:200]}", "confidence": 0})
    log.info("evidence.analyzed", correlation_id=correlation_id or None, **verdict)
    return verdict
