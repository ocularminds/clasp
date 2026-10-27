"""Mock courier: Clasp books a pickup when funds are held; the courier's HMAC-signed pickup scan, with a parcel
photo, records dispatch. The seller never has to message anyone.

COURIER_MODE=manual  the driver page (GET /courier/{order_id}/{shipment_id}) takes the parcel photo with the phone camera
COURIER_MODE=auto    the pickup is scanned as soon as the order waits for dispatch, using the listing photo"""
from __future__ import annotations

import html
import json
import time

from ..common.config import settings
from ..common.ids import new_id
from ..common.log import Log
from ..common.media import get_media
from ..common.stores import get_stores
from ..psp_mock.checkout import sign

log = Log("courier")

SIG_HEADER = "x-clasp-courier-signature"
MAX_PHOTO_BYTES = 4_500_000


def pickup_url(order_id: str, shipment_id: str) -> str:
    return f"{settings.api_base_url}/courier/{order_id}/{shipment_id}"


def pickup_event(order: dict, photo_key: str | None) -> dict:
    shipment = order.get("shipment") or {}
    return {"id": new_id("cev"), "type": "shipment.picked_up", "created": int(time.time()),
            "data": {"order_id": order["order_id"], "shipment_id": shipment.get("shipment_id"), "photo_key": photo_key,
                     "courier": shipment.get("courier", "mock")}}


def deliver_webhook(payload: dict) -> tuple[int, dict]:
    from .webhook import handle_webhook

    body = json.dumps(payload).encode()
    return handle_webhook(body, {SIG_HEADER: sign(settings.courier_webhook_secret, body)})


def _photo_key(order: dict) -> str:
    return f"courier/{order['order_id']}/{(order.get('shipment') or {}).get('shipment_id', 'pickup')}.jpg"


def scan_pickup(order: dict, photo: bytes | None, content_type: str = "image/jpeg") -> tuple[int, dict]:
    """The driver scanned the parcel: store the photo and send the signed pickup event."""
    key = None
    if photo:
        key = get_media().put(_photo_key(order), photo, content_type)
    return deliver_webhook(pickup_event(order, key))


def _listing_photo(order: dict) -> bytes | None:
    item = get_stores().get_item(order["item_id"]) or {}
    media = get_media()
    if item.get("image_key") and media.exists(item["image_key"]):
        return media.get(item["image_key"])
    if item.get("image_url") and settings.media_bucket:
        try:
            import requests

            r = requests.get(item["image_url"], timeout=10)
            if r.ok and r.content[:3] == b"\xff\xd8\xff":
                return r.content
        except Exception as e:  # noqa: BLE001 — a pickup without a photo is still a pickup
            log.warn("courier.listing_photo_failed", order_id=order["order_id"], error=str(e))
    return None


def auto_pickup(order_id: str) -> tuple[int, dict]:
    order = get_stores().get_order(order_id)
    if not order or (order.get("shipment") or {}).get("status") != "booked":
        return 409, {"error": "no booked shipment"}
    status, body = scan_pickup(order, _listing_photo(order))
    log.info("courier.auto_pickup", order_id=order_id, status=status, correlation_id=order.get("correlation_id") or None)
    return status, body


# --------------------------------------------------------------------------- driver page
_SCRIPT = """
<script>
const f=document.getElementById('photo'),b=document.getElementById('go'),m=document.getElementById('msg');
f.onchange=()=>{b.disabled=!f.files.length};
b.onclick=async()=>{b.disabled=true;m.textContent='Uploading…';
 const img=await createImageBitmap(f.files[0]);const s=Math.min(1,1600/Math.max(img.width,img.height));
 const c=document.createElement('canvas');c.width=img.width*s;c.height=img.height*s;c.getContext('2d').drawImage(img,0,0,c.width,c.height);
 const data=c.toDataURL('image/jpeg',0.85).split(',')[1];
 const r=await fetch(location.pathname,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({photo_b64:data})});
 document.open();document.write(await r.text());document.close();};
</script>"""


def render_page(order: dict | None, shipment_id: str, done: bool = False, error: str | None = None) -> str:
    shipment = (order or {}).get("shipment") or {}
    if not order or shipment.get("shipment_id") != shipment_id:
        return _page("Pickup not found", "<p>This pickup link is invalid or has expired.</p>")
    code = html.escape(order["order_id"][-6:])
    item = html.escape(order.get("item_name", "item"))
    if done or shipment.get("status") != "booked":
        return _page("Pickup scanned", f"<p class='ok'>✔ Order {code} ({item}) is on its way.</p>"
                                       "<p>Clasp has sent the buyer the parcel photo and their delivery code.</p>")
    err = f"<p class='err'>{html.escape(error)}</p>" if error else ""
    return _page("Courier pickup", f"""
      <p>Order <b>{code}</b> · {item}{' (replacement)' if shipment.get('replacement') else ''}</p>
      <p class='muted'>Take one photo of the parcel at pickup. It becomes the reference photo if the buyer reports a problem.</p>
      {err}
      <label>Parcel photo <input id='photo' type='file' accept='image/*' capture='environment'></label>
      <button id='go' disabled>Scan pickup</button><p id='msg' class='muted'></p>
      <p class='muted'>Simulated courier. Scanning sends a signed pickup event to Clasp.</p>{_SCRIPT}""")


def _page(title: str, body: str) -> str:
    return f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>{html.escape(title)}</title><style>
body{{font-family:-apple-system,system-ui,sans-serif;max-width:420px;margin:40px auto;padding:0 16px;color:#1b1b1f;background:#f6f6f8}}
h1{{font-size:20px}} label{{display:block;margin:14px 0;font-size:14px}} input{{display:block;margin-top:8px}}
button{{width:100%;padding:14px;border:0;border-radius:10px;background:#111;color:#fff;font-size:16px;margin-top:12px}}
button:disabled{{background:#999}} .muted{{color:#666;font-size:13px}} .ok{{color:#0a7d3e;font-size:18px}} .err{{color:#b00020}}
</style></head><body><h1>🚚 {html.escape(title)}</h1>{body}</body></html>"""
