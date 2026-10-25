"""Channel router: whatsapp | rcs (pool, SMS fallback) | sms | email.

AWS mode uses the operations confirmed from the boto3 service models:
  socialmessaging.SendWhatsAppMessage(originationPhoneNumberId, metaApiVersion, message=<Meta JSON blob>)
  pinpoint-sms-voice-v2.SendRcsMessage(..., RcsMessageContent, FallbackConfiguration={Channel: SMS})
  pinpoint-sms-voice-v2.SendTextMessage(DestinationPhoneNumber, OriginationIdentity, MessageBody, MessageType)
  sesv2.SendEmail(FromEmailAddress, Destination, Content.Simple)
Console / memory modes print or record, so the demo runs with no phones.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..common.config import repo_root, settings
from ..common.ids import new_id
from ..common.log import Log

log = Log("notify")

CHANNELS = ("whatsapp", "rcs", "sms", "email")


@dataclass
class Message:
    channel: str
    to: str
    text: str
    buttons: list[dict] | None = None           # whatsapp reply buttons [{id,title}] (max 3) / rcs suggestions
    media_url: str | None = None                # https URL (presigned) for an image
    rich_card: dict | None = None               # rcs: {title, description, media_url, suggestions:[{text,postback}]}
    subject: str | None = None                  # email
    html: str | None = None                     # email
    attachments: list[dict] = field(default_factory=list)  # email: [{filename, content_type, data(bytes)}]
    fallback_text: str | None = None            # rcs → sms fallback body
    correlation_id: str = ""
    order_id: str | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["attachments"] = [{k: (v if k != "data" else f"<{len(v)} bytes>") for k, v in a.items()} for a in self.attachments]
        return d


class Notifier:
    def send(self, msg: Message) -> str:  # returns provider message id
        raise NotImplementedError


class MemoryNotifier(Notifier):
    def __init__(self):
        self.sent: list[Message] = []

    def send(self, msg):
        self.sent.append(msg)
        return new_id("mem")

    def for_channel(self, channel: str) -> list[Message]:
        return [m for m in self.sent if m.channel == channel]

    def last(self, channel: str | None = None) -> Message | None:
        pool = self.for_channel(channel) if channel else self.sent
        return pool[-1] if pool else None


class ConsoleNotifier(Notifier):
    """Pretty-prints to the terminal and appends to demo/outbox.jsonl (simulate.py reads it)."""

    ICON = {"whatsapp": "💬 WhatsApp", "rcs": "📱 RCS", "sms": "✉️  SMS", "email": "📧 Email"}

    def __init__(self, outbox: Path | None = None, echo: bool = True):
        self.outbox = outbox or repo_root() / "demo" / "outbox.jsonl"
        self.echo = echo

    def send(self, msg):
        mid = new_id("con")
        self.outbox.parent.mkdir(parents=True, exist_ok=True)
        with open(self.outbox, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"message_id": mid, **msg.to_dict()}, default=str) + "\n")
        if self.echo:
            head = f"{self.ICON.get(msg.channel, msg.channel)} → {msg.to}"
            if msg.subject:
                head += f"  [{msg.subject}]"
            print(f"\n┌─ {head}")
            if msg.rich_card:
                print(f"│ ▣ {msg.rich_card.get('title')}: {msg.rich_card.get('description')}")
            for line in msg.text.splitlines():
                print(f"│ {line}")
            if msg.media_url:
                print(f"│ 🖼  {msg.media_url}")
            if msg.buttons:
                print("│ " + "  ".join(f"[{b.get('title') or b.get('text')}]" for b in msg.buttons))
            print("└─")
        return mid


class AwsNotifier(Notifier):
    def __init__(self):
        from ..common.aws import client

        self._client = client

    # -- WhatsApp -------------------------------------------------------------
    def _whatsapp_payload(self, msg: Message) -> dict:
        to = msg.to.lstrip("+")
        if msg.buttons:
            return {
                "messaging_product": "whatsapp", "recipient_type": "individual", "to": to, "type": "interactive",
                "interactive": {
                    "type": "button", "body": {"text": msg.text[:1024]},
                    "action": {"buttons": [
                        {"type": "reply", "reply": {"id": str(b["id"])[:256], "title": str(b["title"])[:20]}}
                        for b in msg.buttons[:3]
                    ]},
                },
            }
        if msg.media_url:
            return {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to, "type": "image",
                    "image": {"link": msg.media_url, "caption": msg.text[:1024]}}
        return {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to, "type": "text",
                "text": {"preview_url": False, "body": msg.text[:4096]}}

    def _send_whatsapp(self, msg: Message) -> str:
        pn = settings.whatsapp_phone_number_id
        if not pn:
            raise RuntimeError("WHATSAPP_PHONE_NUMBER_ID is not set (TODO(human): link the WABA in the EUM Social console)")
        r = self._client("socialmessaging").send_whats_app_message(
            originationPhoneNumberId=pn, metaApiVersion=settings.meta_api_version,
            message=json.dumps(self._whatsapp_payload(msg)).encode("utf-8"),
        )
        return r["messageId"]

    # -- RCS via pool, SMS fallback ------------------------------------------
    def _send_rcs(self, msg: Message) -> str:
        pool = settings.rcs_pool_id
        if not pool:
            raise RuntimeError("RCS_POOL_ID is not set (TODO(human): create the RCS agent + SMS number pool)")
        if msg.rich_card:
            card = msg.rich_card
            content: dict[str, Any] = {"RichCard": {
                "CardOrientation": "VERTICAL",
                "CardContent": {
                    "Title": card.get("title", "")[:200], "Description": card.get("description", msg.text)[:2000],
                    **({"Media": {"FileUrl": card["media_url"], "Height": "MEDIUM"}} if card.get("media_url") else {}),
                    **({"Suggestions": [{"Reply": {"Text": s["text"][:25], "PostbackData": s.get("postback", s["text"])[:2048]}}
                                        for s in card.get("suggestions", [])[:4]]} if card.get("suggestions") else {}),
                },
            }}
        else:
            content = {"TextMessage": {"Body": msg.text[:3000]}}
        rcs_content: dict[str, Any] = {"Content": content}
        if msg.buttons and not msg.rich_card:
            rcs_content["Suggestions"] = [{"Reply": {"Text": str(b["title"])[:25], "PostbackData": str(b["id"])[:2048]}}
                                          for b in msg.buttons[:11]]
        r = self._client("pinpoint-sms-voice-v2").send_rcs_message(
            DestinationPhoneNumber=msg.to, OriginationIdentity=pool, RcsMessageContent=rcs_content,
            MessageTrafficType="TRANSACTIONAL",
            FallbackConfiguration={"Channel": "SMS", "MessageBody": (msg.fallback_text or msg.text)[:1600]},
            Context={"correlation_id": msg.correlation_id[:200]} if msg.correlation_id else {},
        )
        return r["MessageId"]

    # -- SMS ------------------------------------------------------------------
    def _send_sms(self, msg: Message) -> str:
        origin = settings.sms_origination_identity
        if not origin:
            raise RuntimeError("SMS_ORIGINATION_IDENTITY is not set (TODO(human): request an SMS number)")
        r = self._client("pinpoint-sms-voice-v2").send_text_message(
            DestinationPhoneNumber=msg.to, OriginationIdentity=origin, MessageBody=msg.text[:1600],
            MessageType="TRANSACTIONAL",
            Context={"correlation_id": msg.correlation_id[:200]} if msg.correlation_id else {},
        )
        return r["MessageId"]

    # -- Email ----------------------------------------------------------------
    def _send_email(self, msg: Message) -> str:
        body: dict[str, Any] = {"Text": {"Data": msg.text, "Charset": "UTF-8"}}
        if msg.html:
            body["Html"] = {"Data": msg.html, "Charset": "UTF-8"}
        simple: dict[str, Any] = {"Subject": {"Data": msg.subject or "Clasp", "Charset": "UTF-8"}, "Body": body}
        if msg.attachments:
            simple["Attachments"] = [{"RawContent": a["data"], "ContentDisposition": "ATTACHMENT",
                                      "FileName": a["filename"], "ContentType": a.get("content_type", "application/octet-stream")}
                                     for a in msg.attachments]
        r = self._client("sesv2").send_email(
            FromEmailAddress=settings.ses_from_email, Destination={"ToAddresses": [msg.to]},
            Content={"Simple": simple},
            EmailTags=[{"Name": "correlation_id", "Value": msg.correlation_id[:256]}] if msg.correlation_id else [],
        )
        return r["MessageId"]

    def send(self, msg):
        fn = {"whatsapp": self._send_whatsapp, "rcs": self._send_rcs, "sms": self._send_sms, "email": self._send_email}[msg.channel]
        mid = fn(msg)
        log.info("notify.sent", channel=msg.channel, to=msg.to, message_id=mid, correlation_id=msg.correlation_id or None,
                 order_id=msg.order_id)
        return mid


_notifier: Notifier | None = None


def get_notifier() -> Notifier:
    global _notifier
    if _notifier is None:
        mode = settings.notify_mode
        _notifier = AwsNotifier() if mode == "aws" else MemoryNotifier() if mode == "memory" else ConsoleNotifier()
    return _notifier


def set_notifier(n: Notifier | None) -> None:
    global _notifier
    _notifier = n


def send(**kwargs) -> str:
    msg = Message(**kwargs)
    if msg.channel not in CHANNELS:
        raise ValueError(f"unknown channel {msg.channel}")
    return get_notifier().send(msg)
