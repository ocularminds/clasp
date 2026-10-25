#!/usr/bin/env python
"""Assemble real, prepared phone captures into the runbook's 180-second demo.

Requires ffmpeg/ffprobe, three synchronized phone clips of at least 160 seconds,
a final console capture of at least 20 seconds, and a 180-second narration track.
It never creates placeholder footage or overwrites an existing export.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path


def probe(path: Path) -> dict:
    result = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
                            check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("buyer", "seller", "third", "console", "narration"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("demo/recordings/clasp-demo.mp4"))
    args = parser.parse_args()
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        parser.error("Install ffmpeg and ffprobe first.")
    if args.output.exists():
        parser.error("The output already exists. Choose a new filename to preserve the recording.")
    inputs = [args.buyer, args.seller, args.third, args.console, args.narration]
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
    for i, label in enumerate(["BUYER / WHATSAPP", "SELLER / RCS", "SMS / EMAIL"]):
        filters.append(
            f"[{i}:v]trim=duration=160,setpts=PTS-STARTPTS,"
            "scale=600:960:force_original_aspect_ratio=decrease,"
            "pad=640:1080:(ow-iw)/2:94:color=0x142d3a,setsar=1,fps=30,"
            f"drawtext=fontfile='{font}':text='{label}':fontsize=31:fontcolor=white:x=(w-tw)/2:y=32[p{i}]")
    filters.extend([
        "[p0][p1][p2]hstack=inputs=3[phones]",
        "[3:v]trim=duration=20,setpts=PTS-STARTPTS,scale=1920:1080:force_original_aspect_ratio=decrease,"
        "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=0x142d3a,setsar=1,fps=30[console]",
        "[phones][console]concat=n=2:v=1:a=0[v]",
    ])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    command = ["ffmpeg", "-hide_banner", "-n"]
    for path in inputs:
        command.extend(["-i", str(path)])
    command.extend(["-filter_complex", ";".join(filters), "-map", "[v]", "-map", "4:a:0",
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
