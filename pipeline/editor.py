"""Crop a clip to vertical 9:16 and burn in captions using FFmpeg.

The optional branding layers (header bar, watermark, reframe zoom, source
credit) exist to make each Short genuinely *transformative* — your commentary
and framing become the focus. They are NOT a way to defeat YouTube Content ID:
the underlying audio/video fingerprint still matches, and the original creator
can still claim it. Reuse others' footage responsibly (commentary, analysis,
education) and keep uploads private until you've confirmed you have the right
to publish them.
"""

from __future__ import annotations

import os
import shutil
import subprocess


def _ensure_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError(
            "FFmpeg is not installed or not on PATH. Install it:\n"
            "  macOS:   brew install ffmpeg\n"
            "  Ubuntu:  sudo apt install ffmpeg\n"
            "  Windows: https://ffmpeg.org/download.html"
        )


def _escape_for_filter(path: str) -> str:
    """Escape a path for use inside an FFmpeg filtergraph (subtitles=...)."""
    p = path.replace("\\", "/")
    p = p.replace(":", r"\:")
    p = p.replace("'", r"\'")
    return p


def _escape_text(text: str) -> str:
    """Escape arbitrary text for the drawtext filter."""
    if not text:
        return ""
    # Order matters: backslash first.
    text = text.replace("\\", r"\\")
    text = text.replace(":", r"\:")
    text = text.replace("'", r"\u2019")  # curly apostrophe avoids quote issues
    text = text.replace("%", r"\%")
    text = text.replace(",", r"\,")
    return text


def _wrap(text: str, width: int = 28) -> str:
    """Soft-wrap a headline into a couple of lines for the header bar."""
    words = text.split()
    lines: list[str] = []
    current = ""
    for w in words:
        if len(current) + len(w) + 1 <= width:
            current = f"{current} {w}".strip()
        else:
            lines.append(current)
            current = w
    if current:
        lines.append(current)
    return "\n".join(lines[:3])  # cap at 3 lines


def make_short(
    source_path: str,
    start: float,
    end: float,
    srt_path: str,
    output_path: str,
    *,
    headline: str = "",
    brand_handle: str = "",
    source_credit: str = "",
    show_header_bar: bool = True,
    show_watermark: bool = True,
    reframe_zoom: float = 1.06,
    voiceover_path: str = "",
    duck_volume: float = 0.15,
) -> str:
    """Produce a 1080x1920 Short with a blurred background, burned captions,
    and optional transformative branding layers."""
    _ensure_ffmpeg()
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    duration = max(0.1, end - start)
    zoom = max(1.0, min(reframe_zoom, 1.5))

    # ----- foreground scale (with a gentle reframe zoom) -----
    fg_w = int(round(1080 * zoom))
    fg_w -= fg_w % 2  # keep even for libx264

    # ----- caption style; pushed down a bit when a header bar is present -----
    caption_margin_v = 90
    subtitle_style = (
        "FontName=Arial,Fontsize=16,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=1,"
        f"Alignment=2,MarginV={caption_margin_v}"
    )
    srt_escaped = _escape_for_filter(srt_path)

    # ----- build the filtergraph in stages -----
    parts = [
        # blurred fill background
        "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,boxblur=24:6,eq=saturation=1.05:contrast=1.03[bg]",
        # foreground: reframe zoom + subtle color normalization
        f"[0:v]scale={fg_w}:-2:force_original_aspect_ratio=decrease,"
        "eq=saturation=1.08:contrast=1.04[fg]",
        "[bg][fg]overlay=(W-w)/2:(H-h)/2[comp]",
    ]
    last = "comp"

    # header bar with the AI-written hook/commentary (transformative framing)
    if show_header_bar and headline:
        wrapped = _escape_text(_wrap(headline))
        parts.append(
            f"[{last}]drawbox=x=0:y=90:w=1080:h=220:color=black@0.55:t=fill[hbg]"
        )
        parts.append(
            "[hbg]drawtext=text='" + wrapped + "':"
            "fontcolor=white:fontsize=52:line_spacing=10:"
            "x=(w-text_w)/2:y=120:"
            "box=0:borderw=3:bordercolor=black@0.7[hb]"
        )
        last = "hb"

    # corner channel-handle watermark
    if show_watermark and brand_handle:
        handle = _escape_text(brand_handle if brand_handle.startswith("@") else f"@{brand_handle}")
        parts.append(
            f"[{last}]drawtext=text='" + handle + "':"
            "fontcolor=white@0.85:fontsize=34:"
            "x=w-text_w-40:y=h-text_h-140:"
            "box=1:boxcolor=black@0.35:boxborderw=12[wm]"
        )
        last = "wm"

    # small source credit near the bottom (good-faith attribution)
    if source_credit:
        credit = _escape_text(source_credit)
        parts.append(
            f"[{last}]drawtext=text='" + credit + "':"
            "fontcolor=white@0.7:fontsize=24:"
            "x=(w-text_w)/2:y=h-40:"
            "box=1:boxcolor=black@0.3:boxborderw=8[cr]"
        )
        last = "cr"

    # burn in captions last so they sit on top of everything
    parts.append(
        f"[{last}]subtitles='{srt_escaped}':force_style='{subtitle_style}'[v]"
    )

    use_vo = bool(voiceover_path and os.path.exists(voiceover_path))
    if use_vo:
        # Duck the original audio and mix the commentary on top. The voiceover
        # is input #1 and starts at 0 (the same instant the clip begins).
        duck = max(0.0, min(duck_volume, 1.0))
        parts.append(
            f"[0:a]volume={duck:.3f}[duck];"
            f"[duck][1:a]amix=inputs=2:duration=first:dropout_transition=2:normalize=0,"
            "dynaudnorm=f=250[aout]"
        )

    filter_complex = ";".join(parts)

    cmd = ["ffmpeg", "-y", "-ss", f"{start:.3f}", "-i", source_path]
    if use_vo:
        cmd += ["-i", voiceover_path]
    cmd += [
        "-t", f"{duration:.3f}",
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-map", "[aout]" if use_vo else "0:a?",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "20",
        "-c:a", "aac",
        "-b:a", "160k",
        "-r", "30",
        "-movflags", "+faststart",
        output_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg failed:\n{result.stderr[-1500:]}")

    return output_path
