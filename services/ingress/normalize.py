"""Turn an SNS record from any channel into one shape:

  {channel, from, to, text, media: [{id|url, mime}], button: {id, title}|None, message_id, ts, raw}

Channels and their payloads:
* WhatsApp (End User Messaging Social → SNS): {"context": {...}, "whatsAppWebhookEntry": "<Meta webhook entry JSON>"}
* SMS / RCS two-way: {"originationNumber", "destinationNumber", "messageBody", ...}.
  An RCS destination is an agent ID. Suggestions and media metadata arrive as JSON in messageBody.
  See https://docs.aws.amazon.com/sms-voice/latest/userguide/rcs-inbound-media.html.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Inbound:
    channel: str                       # whatsapp | sms | rcs
    sender: str                        # E.164
    to: str | None
    text: str
    media: list[dict] = field(default_factory=list)
    button: dict | None = None         # {id, title}
    sender_name: str | None = None     # WhatsApp profile name when the webhook carries contacts[]
    message_id: str | None = None
    ts: int | None = None
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("raw", None)
        return d


def _e164(n: str | None) -> str | None:
    if not n:
        return None
    n = str(n).strip()
    return n if n.startswith("+") else "+" + n


def _loads(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _record_body(record: dict) -> Any:
    return _loads(record.get("Sns", {}).get("Message") or record.get("Message") or record.get("body") or record)


def record_shape(record: dict) -> Any:
    """Bounded field/type diagnostics, without message text, phone numbers, or signed URLs."""
    def shape(value: Any, depth: int = 3) -> Any:
        value = _loads(value)
        if depth > 0 and isinstance(value, dict):
            return {str(k)[:80]: shape(v, depth - 1) for k, v in list(value.items())[:30]}
        if depth > 0 and isinstance(value, list):
            return [shape(value[0], depth - 1)] if value else []
        return type(value).__name__
    return shape(_record_body(record))


def parse_sns_record(record: dict) -> list[Inbound]:
    """One SNS record may carry several messages (WhatsApp batches). Returns [] for non-message events."""
    body = _record_body(record)
    if not isinstance(body, dict):
        return []
    if "whatsAppWebhookEntry" in body:
        return _parse_whatsapp(body)
    if "originationNumber" in body or "messageBody" in body:
        return [_parse_sms_like(body)]
    return []


def _parse_whatsapp(body: dict) -> list[Inbound]:
    entry = _loads(body.get("whatsAppWebhookEntry")) or {}
    out: list[Inbound] = []
    for change in entry.get("changes", []):
        value = change.get("value") or {}
        if change.get("field") != "messages" and "messages" not in value:
            continue
        to = _e164((value.get("metadata") or {}).get("display_phone_number"))
        names = {str(c.get("wa_id")): ((c.get("profile") or {}).get("name") or None)
                 for c in (value.get("contacts") or []) if isinstance(c, dict)}
        for msg in value.get("messages") or []:
            kind = msg.get("type")
            text, media, button = "", [], None
            if kind == "text":
                text = (msg.get("text") or {}).get("body", "")
            elif kind in {"image", "video", "document", "audio"}:
                m = msg.get(kind) or {}
                media.append({"id": m.get("id"), "mime": m.get("mime_type"), "kind": kind})
                text = m.get("caption", "") or ""
            elif kind == "interactive":
                inter = msg.get("interactive") or {}
                reply = inter.get("button_reply") or inter.get("list_reply") or {}
                button = {"id": reply.get("id"), "title": reply.get("title")}
                text = reply.get("title", "")
            elif kind == "button":  # template quick replies
                b = msg.get("button") or {}
                button = {"id": b.get("payload"), "title": b.get("text")}
                text = b.get("text", "")
            else:
                text = f"[{kind} message]"
            try:
                ts = int(msg.get("timestamp")) if msg.get("timestamp") else None
            except (TypeError, ValueError):
                ts = None
            out.append(Inbound(channel="whatsapp", sender=_e164(msg.get("from")) or "", to=to, text=text, media=media,
                               button=button, sender_name=names.get(str(msg.get("from"))), message_id=msg.get("id"), ts=ts,
                               raw={"context": body.get("context"), "message": msg}))
    return out


def _parse_sms_like(body: dict) -> Inbound:
    text = body.get("messageBody") or body.get("text") or ""
    destination = str(body.get("destinationNumber") or "")
    is_rcs = destination.startswith("rcs-") or (body.get("messageType") or body.get("channel") or "").upper() == "RCS"
    content = _loads(text) if is_rcs else None
    media = []
    button = None
    if isinstance(content, dict) and (content.get("type") == "SUGGESTION" or "postbackData" in content or "mediaStatus" in content):
        text = content.get("text") or ""
        if content.get("postbackData"):
            button = {"id": content["postbackData"], "title": text}
        if content.get("mediaStatus") == "SUCCESS" and content.get("s3Bucket") and content.get("s3Key"):
            media.append({"s3_bucket": content["s3Bucket"], "s3_key": content["s3Key"], "mime": content.get("mimeType")})
        elif content.get("mediaStatus"):
            text = "[RCS media upload failed; ask the sender to resend the photo.]"
    for key in ("mediaUrls", "mediaUrl", "fileUrl", "media"):
        v = body.get(key)
        if isinstance(v, str):
            media.append({"url": v, "mime": body.get("mimeType")})
        elif isinstance(v, list):
            media.extend({"url": u if isinstance(u, str) else u.get("url"), "mime": (u.get("mimeType") if isinstance(u, dict) else None)}
                         for u in v)
    postback = body.get("postbackData") or (body.get("suggestionResponse") or {}).get("postbackData")
    if postback:
        button = {"id": postback, "title": (body.get("suggestionResponse") or {}).get("text") or text}
    channel = "rcs" if is_rcs or body.get("rcs") or postback or media else "sms"
    to = destination if destination.startswith("rcs-") else _e164(destination)
    return Inbound(channel=channel, sender=_e164(body.get("originationNumber")) or "", to=to,
                   text=text, media=media, button=button, message_id=body.get("inboundMessageId") or body.get("messageId"),
                   ts=None, raw=body)
