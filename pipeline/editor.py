"""Crop a clip to vertical 9:16 and burn in captions using FFmpeg.

The optional branding layers (header bar, watermark, reframe zoom, source
credit) exist to make each Short genuinely *transformative* — your commentary
and framing become the focus. They are NOT a way to defeat YouTube Content ID:
the underlying audio/video fingerprint still matches, and the original creator
can still claim it. Reuse others' footage responsibly (commentary, analysis,
education) and keep uploads private until you've confirmed you have the right
to publish them.

Captions can be plain SRT or animated karaoke ASS. When ``keyword_times`` are
supplied, brief punch-in zooms fire on those emphasized moments to add motion.
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


# Cache of filters this FFmpeg build actually ships. Some builds (e.g. a
# minimal Homebrew ffmpeg without libfreetype/libass) lack `drawtext` and/or
# `subtitles`, which would otherwise crash the filtergraph with
# "No such filter: 'drawtext'". We detect once and degrade gracefully.
_available_filters: set[str] | None = None
_warned_filters: set[str] = set()


def _get_available_filters() -> set[str]:
    global _available_filters
    if _available_filters is None:
        try:
            out = subprocess.run(
                ["ffmpeg", "-hide_banner", "-filters"],
                capture_output=True,
                text=True,
                timeout=30,
            ).stdout
            names: set[str] = set()
            for line in out.splitlines():
                # Lines look like: " T.. drawtext         V->V       Draw text ..."
                cols = line.split()
                if len(cols) >= 2 and cols[0].isalpha() is False:
                    # first col is a flags token like "T.." or "..C"
                    names.add(cols[1])
            _available_filters = names
        except Exception:
            _available_filters = set()
    return _available_filters


def _has_filter(name: str) -> bool:
    return name in _get_available_filters()


def _warn_once(key: str, message: str) -> None:
    if key not in _warned_filters:
        _warned_filters.add(key)
        print(message)


def _escape_for_filter(path: str) -> str:
    """Escape a path for use inside an FFmpeg filtergraph (subtitles=filename='...').

    The path is wrapped in single quotes by the caller, so we only normalize
    Windows backslashes to forward slashes and escape any single quote. Colons
    (e.g. a Windows drive letter) are literal inside single quotes, so they must
    NOT be backslash-escaped here.
    """
    p = path.replace("\\", "/")
    p = p.replace("'", r"\'")
    return p


def _escape_text(text: str) -> str:
    """Escape arbitrary text for the drawtext filter."""
    if not text:
        return ""
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
    return "\n".join(lines[:3])


def _zoom_expression(keyword_times: list[float], intensity: float, fps: int = 30) -> str:
    """Build a zoompan 'z' expression: base 1.0 with a Gaussian bump at each
    keyword time. ``on/fps`` is the running clip time in seconds."""
    intensity = max(0.02, min(intensity, 0.4))
    bumps = "+".join(
        f"{intensity:.3f}*exp(-pow((on/{fps}-{t:.2f})/0.18,2))" for t in keyword_times
    )
    # Clamp so a cluster of keywords can't over-zoom.
    return f"min(1.5,1.0+{bumps})"


def make_short(
    source_path: str,
    start: float,
    end: float,
    caption_path: str,
    output_path: str,
    *,
    caption_is_ass: bool = False,
    headline: str = "",
    brand_handle: str = "",
    source_credit: str = "",
    show_header_bar: bool = True,
    show_watermark: bool = True,
    reframe_zoom: float = 1.06,
    keyword_times: list[float] | None = None,
    keyword_zoom_intensity: float = 0.0,
    voiceover_path: str = "",
    duck_volume: float = 0.15,
) -> str:
    """Produce a 1080x1920 Short with a blurred background, burned captions,
    optional transformative branding layers, and optional keyword punch-ins."""
    _ensure_ffmpeg()
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    # Detect which optional filters this FFmpeg build actually has. A minimal
    # build without libfreetype/libass lacks these; we skip those layers rather
    # than crash, so the core vertical crop still produces a Short.
    has_drawtext = _has_filter("drawtext")
    has_subtitles = _has_filter("subtitles")
    if not has_drawtext and (show_header_bar or show_watermark or source_credit):
        _warn_once(
            "drawtext",
            "  -> NOTE: your FFmpeg has no 'drawtext' filter (built without libfreetype); "
            "skipping header/watermark/credit. Reinstall full FFmpeg to enable them "
            "(macOS: brew reinstall ffmpeg).",
        )
    if not has_subtitles and caption_path:
        _warn_once(
            "subtitles",
            "  -> NOTE: your FFmpeg has no 'subtitles' filter (built without libass); "
            "skipping burned-in captions. Reinstall full FFmpeg to enable them "
            "(macOS: brew reinstall ffmpeg).",
        )

    duration = max(0.1, end - start)
    zoom = max(1.0, min(reframe_zoom, 1.5))

    fg_w = int(round(1080 * zoom))
    fg_w -= fg_w % 2  # keep even for libx264

    caption_escaped = _escape_for_filter(caption_path)

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
    if show_header_bar and headline and has_drawtext:
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
    if show_watermark and brand_handle and has_drawtext:
        handle = _escape_text(brand_handle if brand_handle.startswith("@") else f"@{brand_handle}")
        parts.append(
            f"[{last}]drawtext=text='" + handle + "':"
            "fontcolor=white@0.85:fontsize=34:"
            "x=w-text_w-40:y=h-text_h-140:"
            "box=1:boxcolor=black@0.35:boxborderw=12[wm]"
        )
        last = "wm"

    # small source credit near the bottom (good-faith attribution)
    if source_credit and has_drawtext:
        credit = _escape_text(source_credit)
        parts.append(
            f"[{last}]drawtext=text='" + credit + "':"
            "fontcolor=white@0.7:fontsize=24:"
            "x=(w-text_w)/2:y=h-40:"
            "box=1:boxcolor=black@0.3:boxborderw=8[cr]"
        )
        last = "cr"

    # burn in captions (ASS carries its own styling; SRT needs force_style).
    # NOTE: do NOT wrap the filename in single quotes here. When FFmpeg is
    # invoked via subprocess (no shell) the quotes are passed literally into
    # the filtergraph and FFmpeg's parser rejects them ("No option name
    # near ..."). The path is escaped instead. force_style DOES need quoting
    # because it contains commas, which would otherwise split the filterchain.
    # NOTE: FFmpeg 8.0 removed the positional shorthand for the subtitles
    # filter, so the path MUST be given with the explicit `filename=` key.
    # `subtitles=path.ass` now errors with "No option name near ...".
    if has_subtitles and caption_path:
        if caption_is_ass:
            parts.append(f"[{last}]subtitles=filename='{caption_escaped}'[cap]")
        else:
            subtitle_style = (
                "FontName=Arial,Fontsize=16,PrimaryColour=&H00FFFFFF,"
                "OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=1,"
                "Alignment=2,MarginV=90"
            )
            parts.append(
                f"[{last}]subtitles=filename='{caption_escaped}':force_style='{subtitle_style}'[cap]"
            )
        last = "cap"

    # keyword punch-in zooms (fixed 1080x1920 output, so this is reliable)
    use_zoom = bool(keyword_times) and keyword_zoom_intensity > 0
    if use_zoom:
        z_expr = _zoom_expression(keyword_times, keyword_zoom_intensity)
        parts.append(
            f"[{last}]fps=30,zoompan=z='{z_expr}':"
            "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            "d=1:s=1080x1920[vz]"
        )
        last = "vz"

    use_vo = bool(voiceover_path and os.path.exists(voiceover_path))
    if use_vo:
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
        "-map", f"[{last}]",
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