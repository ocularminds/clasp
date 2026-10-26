"""Hand a normalized inbound message to the agent and send its reply back on the same channel.

AGENT_MODE=remote → bedrock-agentcore InvokeAgentRuntime (runtimeSessionId = buyer sub, so one
thread spans WhatsApp, RCS and SMS). AGENT_MODE=local → services.agent.agent in-process.
AGENT_MODE=scripted → no model; used by tests to prove the plumbing."""
from __future__ import annotations

import json
import re
import uuid

from ..common import clock
from ..common.config import settings
from ..common.ids import correlation_id as new_corr
from ..common.log import Log
from ..common.media import get_media
from ..notify import router as notify
from .identity import auto_enrol, resolve_sub
from .normalize import Inbound

log = Log("ingress")


def session_id_for(sub: str) -> str:
    """AgentCore runtimeSessionId must be ≥ 33 chars; derive a stable one from the sub."""
    return f"clasp-{uuid.uuid5(uuid.NAMESPACE_URL, sub)}"


def fetch_whatsapp_media(inbound: Inbound) -> list[str]:
    """Pull WhatsApp media into our bucket with GetWhatsAppMessageMedia; returns media keys."""
    keys = []
    pn = settings.whatsapp_phone_number_id
    media = get_media()
    for m in inbound.media:
        if not m.get("id"):
            continue
        key = f"inbound/{inbound.sender.lstrip('+')}/{m['id']}"
        if pn and settings.media_bucket:
            from ..common.aws import client

            # S3File.key is a prefix; a presigned PUT preserves the exact key the agent reads.
            client("socialmessaging").get_whatsapp_message_media(
                mediaId=m["id"], originationPhoneNumberId=pn,
                destinationS3PresignedUrl=media.copy_from_presigned_target(key, m.get("mime") or "application/octet-stream"),
            )
        else:
            media.put(key, b"", m.get("mime") or "application/octet-stream")  # local placeholder
        keys.append(key)
    return keys


def fetch_url_media(inbound: Inbound) -> list[str]:
    keys = []
    for m in inbound.media:
        if m.get("s3_key"):
            # EUM already uploaded this RCS file using the dedicated media role.
            if m.get("s3_bucket") != settings.media_bucket or not str(m["s3_key"]).startswith("inbound/rcs/"):
                raise ValueError("RCS media must belong to the configured bucket and inbound/rcs/ prefix.")
            keys.append(m["s3_key"])
            continue
        url = m.get("url")
        if not url:
            continue
        key = f"inbound/{inbound.sender.lstrip('+')}/{abs(hash(url))}"
        try:
            import requests

            data = requests.get(url, timeout=15).content
        except Exception:  # noqa: BLE001
            data = b""
        get_media().put(key, data, m.get("mime") or "application/octet-stream")
        keys.append(key)
    return keys


def build_agent_payload(inbound: Inbound, sub: str, party: dict | None, media_keys: list[str], corr: str) -> dict:
    return {
        "channel": inbound.channel, "from": inbound.sender, "text": inbound.text, "media_keys": media_keys,
        "button": inbound.button, "actor_sub": sub, "actor_role": (party or {}).get("role", "buyer"),
        "actor_name": (party or {}).get("name"), "session_id": session_id_for(sub), "correlation_id": corr,
        "message_id": inbound.message_id, "received_at": clock.now(),
    }


def invoke_agent(payload: dict) -> dict:
    mode = settings.agent_mode
    if mode == "remote":
        from ..common.aws import client

        r = client("bedrock-agentcore").invoke_agent_runtime(
            agentRuntimeArn=settings.agent_runtime_arn, runtimeSessionId=payload["session_id"],
            runtimeUserId=payload["actor_sub"], contentType="application/json", accept="application/json",
            payload=json.dumps(payload).encode(),
        )
        body = r["response"].read() if hasattr(r["response"], "read") else r["response"]
        return json.loads(body)
    if mode == "local":
        from ..agent.agent import handle_turn

        return handle_turn(payload)
    from ..agent.scripted import handle_turn as scripted  # no LLM

    return scripted(payload)


def reply(inbound: Inbound, out: dict, corr: str) -> None:
    text = (out.get("text") or "").strip()
    if not text and not out.get("buttons"):
        return
    kwargs = {"channel": inbound.channel, "to": inbound.sender, "text": text or "Please choose:", "correlation_id": corr,
              "order_id": out.get("order_id")}
    if out.get("buttons"):
        kwargs["buttons"] = out["buttons"][:3]
    if out.get("media_url"):
        kwargs["media_url"] = out["media_url"]
    if inbound.channel == "sms" and kwargs.get("buttons"):  # SMS has no buttons: spell them out
        kwargs["text"] += "\n" + "\n".join(f"{i + 1}) {b['title']}" for i, b in enumerate(kwargs.pop("buttons")))
    notify.send(**kwargs)


def handle_inbound(inbound: Inbound) -> dict:
    corr = new_corr()
    ilog = log.bind(correlation_id=corr, channel=inbound.channel, sender=inbound.sender)
    sub, party = resolve_sub(inbound.sender)
    if not sub and inbound.channel in settings.auto_enrol_channels:
        try:
            sub, party = auto_enrol(inbound.sender, inbound.sender_name)
            ilog.info("ingress.auto_enrolled", actor_sub=sub, role=party.get("role"))
        except Exception as e:  # noqa: BLE001
            ilog.error("ingress.auto_enrol_failed", error=str(e))
    if not sub:
        ilog.warn("ingress.unknown_sender")
        notify.send(channel=inbound.channel, to=inbound.sender, correlation_id=corr,
                    text="Hi! This number isn't registered with Clasp yet. Ask the marketplace to add you, then message again.")
        return {"status": "unknown_sender"}
    media_keys = fetch_whatsapp_media(inbound) if inbound.channel == "whatsapp" else fetch_url_media(inbound)
    payload = build_agent_payload(inbound, sub, party, media_keys, corr)
    ilog.info("agent.turn.in", actor_sub=sub, text=inbound.text[:200], media=len(media_keys), button=inbound.button)
    out = invoke_agent(payload)
    ilog.info("agent.turn.out", actor_sub=sub, text=(out.get("text") or "")[:200], buttons=len(out.get("buttons") or []),
              tools=out.get("tools_called"))
    reply(inbound, out, corr)
    return {"status": "ok", "correlation_id": corr, **{k: out.get(k) for k in ("text", "buttons", "tools_called")}}
