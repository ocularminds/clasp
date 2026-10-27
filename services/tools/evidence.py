"""Truce evidence: a multimodal Bedrock call that only *reports*. AgentSafe decides.

Photos go to the configured vision model (Claude). Short videos go to a video-capable model (Amazon Nova,
BEDROCK_VIDEO_MODEL_ID): one video per request, inline up to 25 MB, no audio.

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
Image 1 is the courier's PICKUP (dispatch) photo of the parcel or item. The second input is the buyer's LIVE {medium}, which
must show a handwritten or displayed one-time code next to the item. The expected code is: {code}

Report strictly as JSON with exactly these keys and no prose outside the JSON:
{{"code_visible": bool, "code_matches": bool, "same_item_as_dispatch": bool, "damage_observed": bool,
  "confidence": number between 0 and 1, "notes": short string}}
Rules: code_matches is true only if the visible code equals {code} character for character.
If the live {medium} looks generated, edited, or is a recording of a screen, say so in notes and lower confidence."""
VIDEO_FORMATS = {"mp4", "mov", "mkv", "webm", "three_gp"}
MAX_INLINE_VIDEO = 24 * 1024 * 1024


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


def media_format(data: bytes) -> str:
    """Bedrock Converse format name, sniffed from the bytes (inbound media types are not trusted)."""
    if data[4:8] == b"ftyp":
        brand = data[8:12]
        return "mov" if brand == b"qt  " else "three_gp" if brand.startswith(b"3g") else "mp4"
    if data[:4] == b"\x1a\x45\xdf\xa3":
        return "webm" if b"webm" in data[:64] else "mkv"
    return _mime(data)


def _mime(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return "jpeg"


def analyze(expected_code: str, dispatch_photo: bytes | None, live_media: bytes, correlation_id: str = "") -> dict:
    fmt_ = media_format(live_media)
    is_video = fmt_ in VIDEO_FORMATS
    if settings.evidence_mode != "bedrock":
        verdict = _stub()
        log.info("evidence.stub", correlation_id=correlation_id or None, video=is_video, **verdict)
        return verdict
    if is_video and len(live_media) > MAX_INLINE_VIDEO:
        verdict = _coerce({"notes": "video too large to check; ask for a photo or a shorter video", "confidence": 0})
        log.info("evidence.too_large", correlation_id=correlation_id or None, bytes=len(live_media))
        return verdict
    medium = "video" if is_video else "photo"
    live_block = ({"video": {"format": fmt_, "source": {"bytes": live_media}}} if is_video
                  else {"image": {"format": fmt_, "source": {"bytes": live_media}}})
    content: list[dict[str, Any]] = [{"text": PROMPT.format(code=expected_code, medium=medium)}]
    if dispatch_photo:
        content.append({"text": "Image 1 (pickup photo):"})
        content.append({"image": {"format": _mime(dispatch_photo), "source": {"bytes": dispatch_photo}}})
    else:
        content.append({"text": "Image 1 is unavailable; set same_item_as_dispatch to false."})
    content.append({"text": f"Buyer's live {medium}:"})
    content.append(live_block)
    model = settings.video_model_id if is_video else settings.vision_model_id
    try:
        text = _converse(model, content, guard=not is_video)
    except Exception as e:  # noqa: BLE001 — some models refuse an image beside a video: retry with the video alone
        if not (is_video and dispatch_photo):
            raise
        log.warn("evidence.video_retry_without_reference", error=str(e)[:200], correlation_id=correlation_id or None)
        text = _converse(model, [{"text": PROMPT.format(code=expected_code, medium=medium)},
                                 {"text": "Image 1 is unavailable; set same_item_as_dispatch to false."},
                                 {"text": "Buyer's live video:"}, live_block], guard=False)
    start, end = text.find("{"), text.rfind("}")
    try:
        verdict = _coerce(json.loads(text[start:end + 1]))
    except (ValueError, json.JSONDecodeError):
        verdict = _coerce({"notes": f"model returned non-JSON: {text[:200]}", "confidence": 0})
    log.info("evidence.analyzed", correlation_id=correlation_id or None, video=is_video, model=model, **verdict)
    return verdict


def _converse(model: str, content: list[dict[str, Any]], guard: bool = True) -> str:
    """The PII guardrail screens text and images; video input is sent without it."""
    from ..common.aws import client

    kwargs: dict[str, Any] = {"modelId": model, "messages": [{"role": "user", "content": content}],
                              "inferenceConfig": {"maxTokens": 400, "temperature": 0}}
    if guard and settings.guardrail_id:
        kwargs["guardrailConfig"] = {"guardrailIdentifier": settings.guardrail_id, "guardrailVersion": settings.guardrail_version}
    resp = client("bedrock-runtime").converse(**kwargs)
    return "".join(c.get("text", "") for c in resp["output"]["message"]["content"])
