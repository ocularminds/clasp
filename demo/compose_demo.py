#!/usr/bin/env python
"""Assemble real, prepared phone captures into the runbook's 180-second demo.

Requires ffmpeg/ffprobe, Pillow (the assets extra), two synchronized phone clips and an email/console capture
of at least 160 seconds, a final console capture of at least 20 seconds, and a
180-second narration track. The email/console panel gets half the screen width.
It never creates placeholder footage or overwrites an existing export.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path


def probe(path: Path) -> dict:
    result = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
                            check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def make_labels(path: Path, font: Path) -> None:
    """Render labels once; many packaged ffmpeg builds omit the drawtext filter."""
    from PIL import Image, ImageDraw, ImageFont

    overlay = Image.new("RGBA", (1920, 1080), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    title_font = ImageFont.truetype(str(font), 28)
    subtitle_font = ImageFont.truetype(str(font), 20)
    panels = [(240, "BUYER", "WHATSAPP / SMS"),
              (720, "SELLER", "WHATSAPP / SMS"),
              (1440, "SUPERVISOR / EMAIL + CONSOLE", "RCS configured / handset untested")]
    for center, label, subtitle in panels:
        draw.text((center, 28), label, font=title_font, fill="white", anchor="mt")
        draw.text((center, 70), subtitle, font=subtitle_font, fill="white", anchor="mt")
    overlay.save(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("buyer", "seller", "console", "narration"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--panel", "--third", dest="panel", type=Path, required=True,
                        help="160-second real supervisor email/console capture (--third is a legacy alias).")
    parser.add_argument("--output", type=Path, default=Path("demo/recordings/clasp-demo.mp4"))
    args = parser.parse_args()
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        parser.error("Install ffmpeg and ffprobe first.")
    if args.output.exists():
        parser.error("The output already exists. Choose a new filename to preserve the recording.")
    inputs = [args.buyer, args.seller, args.panel, args.console, args.narration]
    for path, minimum, kind in zip(inputs, [160, 160, 160, 20, 180], ["video"] * 4 + ["audio"]):
        if not path.is_file():
            parser.error(f"Missing actual recording: {path}")
        info = probe(path)
        if float(info["format"]["duration"]) < minimum or not any(s["codec_type"] == kind for s in info["streams"]):
            parser.error(f"{path} needs at least {minimum} seconds of {kind}.")
    fonts = [Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
             Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")]
    font = next((p for p in fonts if p.exists()), None)
    if not font:
        parser.error("Arial Bold or DejaVu Sans Bold is required for the phone labels.")
    filters = []
    for i, width in enumerate([480, 480, 960]):
        filters.append(
            f"[{i}:v]trim=duration=160,setpts=PTS-STARTPTS,"
            f"scale={width - 40}:940:force_original_aspect_ratio=decrease,"
            f"pad={width}:1080:(ow-iw)/2:112+(940-ih)/2:color=0x142d3a,setsar=1,fps=30[p{i}]")
    filters.extend([
        "[p0][p1][p2]hstack=inputs=3[phones]",
        "[phones][5:v]overlay=shortest=1[labeled]",
        "[3:v]trim=duration=20,setpts=PTS-STARTPTS,scale=1920:1080:force_original_aspect_ratio=decrease,"
        "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=0x142d3a,setsar=1,fps=30[console]",
        "[labeled][console]concat=n=2:v=1:a=0[v]",
    ])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="clasp-demo-") as temp:
        overlay = Path(temp) / "labels.png"
        try:
            make_labels(overlay, font)
        except ImportError:
            parser.error("Install Pillow with: uv pip install --python .venv/bin/python -e '.[assets]'")
        command = ["ffmpeg", "-hide_banner", "-n"]
        for path in inputs:
            command.extend(["-i", str(path)])
        command.extend(["-loop", "1", "-framerate", "30", "-i", str(overlay),
                        "-filter_complex", ";".join(filters), "-map", "[v]", "-map", "4:a:0",
                        "-af", "atrim=duration=180,asetpts=PTS-STARTPTS", "-t", "180",
                        "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(args.output)])
        subprocess.run(command, check=True)
    info = probe(args.output)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    if (video["width"], video["height"]) != (1920, 1080) or abs(float(info["format"]["duration"]) - 180) > .2:
        raise RuntimeError("Export dimensions or duration failed verification.")
    print(f"Exported {args.output}: 1920x1080, 180 seconds. Review the full video before uploading.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
