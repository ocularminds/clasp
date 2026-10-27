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
from ..common.stores import get_stores
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


def collect_notices(sub: str, role: str, limit: int = 8) -> list[dict]:
    """What the workflow told this person outside the chat since their last message (dispatch notice, SMS code sent,
    approvals). Codes are never in these memos. Marked seen once handed to the agent, so the turn prompt — and with
    it AgentCore Memory — carries one continuous conversation across WhatsApp, SMS, RCS and email."""
    stores = get_stores()
    try:
        orders = stores.orders_for_seller(sub) if role == "seller" else stores.orders_for_buyer(sub)
    except Exception as e:  # noqa: BLE001 — context is a nicety; never block the turn
        log.warn("ingress.notices_failed", error=str(e))
        return []
    out: list[dict] = []
    for o in orders[:10]:
        notices = list(o.get("notices") or [])
        fresh = [n for n in notices if n.get("role") == role and not n.get("seen")]
        if not fresh:
            continue
        out += [{"order": o["order_id"][-6:], "channel": n.get("channel"), "memo": n.get("memo"), "ts": n.get("ts", 0)} for n in fresh]
        ids = {n.get("id") for n in fresh}
        stores.update_order(o["order_id"], {"notices": [{**n, "seen": True} if n.get("id") in ids else n for n in notices]})
    return sorted(out, key=lambda n: n["ts"])[-limit:]


def build_agent_payload(inbound: Inbound, sub: str, party: dict | None, media_keys: list[str], corr: str) -> dict:
    role = (party or {}).get("role", "buyer")
    return {
        "channel": inbound.channel, "from": inbound.sender, "text": inbound.text, "media_keys": media_keys,
        "button": inbound.button, "actor_sub": sub, "actor_role": role,
        "actor_name": (party or {}).get("name"), "session_id": session_id_for(sub), "correlation_id": corr,
        "message_id": inbound.message_id, "received_at": clock.now(), "notices": collect_notices(sub, role),
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
                    text="Hi, this is Clasp. This number isn't registered with us yet. Please ask the marketplace to add it, "
                         "then message again.")
        return {"status": "unknown_sender"}
    media_keys = fetch_whatsapp_media(inbound) if inbound.channel == "whatsapp" else fetch_url_media(inbound)
    payload = build_agent_payload(inbound, sub, party, media_keys, corr)
    ilog.info("agent.turn.in", actor_sub=sub, text=inbound.text[:200], media=len(media_keys), button=inbound.button)
    out = invoke_agent(payload)
    ilog.info("agent.turn.out", actor_sub=sub, text=(out.get("text") or "")[:200], buttons=len(out.get("buttons") or []),
              tools=out.get("tools_called"))
    reply(inbound, out, corr)
    return {"status": "ok", "correlation_id": corr, **{k: out.get(k) for k in ("text", "buttons", "tools_called")}}
