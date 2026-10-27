#!/usr/bin/env python
"""Export the architecture field and exact-size RCS registration images.

Install with `uv pip install --python .venv/bin/python -e '.[assets]'`.
The existing Decionis wordmark is preserved; no generated claims or imagery are used.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs"
INK = "#142d3a"
TEAL = "#00635f"
MUTED = "#506571"
PAPER = "#f6f8f7"
LINE = "#8da5ad"


def font(size: int, bold: bool = False):
    candidates = [
        Path("/System/Library/Fonts/Supplemental") / ("Arial Bold.ttf" if bold else "Arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu") / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"),
    ]
    for path in candidates:
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    raise RuntimeError("Install Arial or DejaVu Sans to reproduce the exports.")


def architecture() -> None:
    im = Image.new("RGB", (2400, 1580), PAPER)
    d = ImageDraw.Draw(im)
    d.text((70, 40), "Clasp", font=font(74, True), fill=INK)
    d.text((72, 130), "Nothing moves until both sides are kept whole.", font=font(34), fill=MUTED)
    d.rounded_rectangle((1680, 60, 2330, 128), radius=20, fill="#e0eeeb")
    d.text((1708, 78), "AWS us-east-1  |  Mock payments", font=font(29, True), fill=TEAL)

    def card(box, title, lines, *, color="#ffffff", border="#cad8dc", title_color=INK):
        x, y, x2, y2 = box
        d.rounded_rectangle(box, radius=20, fill=color, outline=border, width=2)
        d.text((x + 24, y + 20), title, font=font(29, True), fill=title_color)
        for i, text in enumerate(lines):
            d.text((x + 24, y + 70 + i * 36), text, font=font(24), fill=MUTED)

    def arrow(points, label=None, label_at=None, *, color=LINE, width=4):
        d.line(points, fill=color, width=width, joint="curve")
        x, y = points[-1]
        px, py = points[-2]
        import math
        a = math.atan2(y - py, x - px)
        d.polygon([(x, y), (x - 15 * math.cos(a - .45), y - 15 * math.sin(a - .45)),
                   (x - 15 * math.cos(a + .45), y - 15 * math.sin(a + .45))], fill=color)
        if label:
            lx, ly = label_at
            bb = d.textbbox((lx, ly), label, font=font(21))
            d.rectangle((bb[0] - 4, bb[1] - 4, bb[2] + 4, bb[3] + 4), fill=PAPER)
            d.text((lx, ly), label, font=font(21), fill=MUTED)

    card((70, 215, 580, 350), "Buyer / WhatsApp", ["AWS End User Messaging Social"])
    card((625, 215, 1175, 350), "Seller / RCS + SMS", ["One-way notices · no chat needed"])
    card((1220, 215, 1710, 350), "Delivery / SMS", ["One-time delivery code"])
    card((1755, 215, 2330, 350), "Seller → supervisor / SES", ["Signed approvals, receipts, evidence"])

    d.rounded_rectangle((50, 385, 2350, 1470), radius=28, outline="#cad8dc", width=2)
    d.text((82, 404), "CLASP APPLICATION", font=font(19, True), fill=MUTED)
    d.rounded_rectangle((1630, 570, 2320, 1335), radius=24, fill="#e8f2ee", outline="#8cbbb0", width=2)
    d.text((1660, 589), "EXECUTION CONTROL PLANE", font=font(21, True), fill=TEAL)

    arrow([(326, 350), (326, 455)])
    arrow([(870, 350), (870, 420), (390, 420), (390, 455)])
    card((90, 455, 535, 610), "Amazon SNS", ["WhatsApp inbound topic", "SMS / RCS inbound topic"])
    arrow([(312, 610), (312, 665)])
    card((90, 665, 535, 825), "Ingress Lambda", ["Normalize messages + media", "Bind sender to an identity"])
    card((90, 905, 535, 1065), "Amazon Cognito", ["Verified demo phone identities", "Party lookup in DynamoDB"])
    arrow([(312, 905), (312, 825)])

    arrow([(535, 728), (610, 728)])
    card((610, 455, 1100, 785), "AgentCore Runtime", ["Strands agent", "Claude Sonnet 4.5 on Bedrock", "Actor binding on every tool call", "AgentCore Memory", "Bedrock Guardrail: PII masking"])
    arrow([(855, 785), (855, 890)])
    card((610, 890, 1100, 1065), "AgentCore Gateway", ["MCP tools + Cognito JWT auth", "Credentials in Secrets Manager"])
    arrow([(1100, 971), (1175, 971)])
    card((1175, 890, 1575, 1065), "Tools Lambda", ["Stock, orders, evidence", "Money actions are proposals"])

    card((1175, 455, 1575, 660), "HTTP API + Lambda", ["Checkout + signed PSP webhook", "Signed courier pickup scan", "Approval confirm pages"])
    arrow([(1575, 550), (1600, 550), (1600, 710), (1660, 710)])
    card((1660, 640, 2280, 840), "Decionis AgentSafe", ["Enforcing gateway → policy Lambda", "Allow / deny / escalate on drift", "KMS-signed ES256 authorization"], border="#8cbbb0")
    arrow([(1575, 952), (1610, 952), (1610, 764), (1660, 764)])
    arrow([(1970, 840), (1970, 880)], color=TEAL)
    card((1660, 880, 2280, 1055), "Executor Lambda", ["Verify signature, scope, TTL, nonce", "Only role allowed to append the ledger"], border="#8cbbb0")

    arrow([(1375, 1065), (1375, 1135)])
    card((1175, 1135, 1575, 1345), "AWS Step Functions", ["Payment → courier → delivery", "Truce → seller → supervisor", "Settle the rest to the seller"])
    arrow([(1970, 1055), (1970, 1120)], color=TEAL)
    card((1660, 1120, 2280, 1305), "Data + evidence", ["DynamoDB orders, ledger, decisions", "S3 pickup photos + evidence", "KMS signing key"], border="#8cbbb0")
    arrow([(1660, 1015), (1604, 1015), (1604, 1235), (1575, 1235)], color=TEAL)

    card((90, 1150, 1100, 1340), "Evidence is input; policy controls execution", ["A photo or prompt cannot sign an authorization.", "Payment screenshots cannot confirm funds.", "Cloud resources shown here come from the CDK source."], color="#edf1f4")
    arrow([(1375, 1345), (1375, 1380)])
    d.rounded_rectangle((90, 1380, 2280, 1440), radius=16, fill="#e0eeeb")
    d.text((119, 1397), "Workflow notices  →  WhatsApp buyer  ·  RCS/SMS seller (one-way)  ·  SMS delivery code  ·  SES approvals + evidence", font=font(27), fill=TEAL)
    d.text((75, 1505), "Architecture from source · Decionis AgentSafe gateway in enforcement, fails closed · PSP and courier simulated; messaging and models real.", font=font(25), fill=MUTED)
    im.save(OUT / "architecture.png", optimize=True)


def registration_images() -> None:
    source = Image.open(OUT / "assets/decionis-logo-wordmark.png").convert("RGBA")
    logo = Image.new("RGBA", (224, 224), (0, 0, 0, 0))
    d = ImageDraw.Draw(logo)
    d.text((112, 73), "Clasp", font=font(66, True), fill=TEAL, anchor="mm")
    d.rounded_rectangle((32, 123, 192, 128), radius=2, fill=TEAL)
    brand = source.copy()
    brand.thumbnail((192, 54), Image.Resampling.LANCZOS)
    logo.alpha_composite(brand, ((224 - brand.width) // 2, 155))
    logo.save(OUT / "assets/rcs-logo.png", optimize=True)

    banner = Image.new("RGB", (1440, 448), PAPER)
    d = ImageDraw.Draw(banner)
    d.rectangle((0, 0, 18, 448), fill=TEAL)
    d.text((84, 76), "Clasp", font=font(112, True), fill=TEAL)
    d.text((88, 226), "Orders. Delivery. Fair resolution.", font=font(43), fill=INK)
    d.text((88, 303), "Nothing moves until both sides are kept whole.", font=font(29), fill=MUTED)
    brand = source.copy()
    brand.thumbnail((308, 84), Image.Resampling.LANCZOS)
    banner.paste(brand, (1050, 100), brand)
    banner.save(OUT / "assets/rcs-banner.png", optimize=True)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    architecture()
    registration_images()
    records = []
    for name in ("architecture.png", "assets/rcs-logo.png", "assets/rcs-banner.png"):
        path = OUT / name
        with Image.open(path) as im:
            records.append({"file": name, "dimensions": list(im.size), "bytes": path.stat().st_size,
                            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    assert records[1]["bytes"] < 50_000
    assert records[2]["bytes"] < 200_000
    (OUT / "assets/manifest.json").write_text(json.dumps({
        "method": "Deterministic Pillow composition; no generated imagery",
        "brand_source": "https://decionis.com/decionis-logo-wordmark.png",
        "brand_treatment": "Existing wordmark preserved, scaled proportionally",
        "accent_color": TEAL,
        "exports": records,
    }, indent=2) + "\n")
    for record in records:
        print(record["file"], record["dimensions"], record["bytes"], "bytes")


if __name__ == "__main__":
    main()
