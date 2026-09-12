"""Generate a custom thumbnail for a Short.

Approach: grab a representative frame from the finished Short with FFmpeg, then
overlay the AI hook text and channel handle with Pillow. We use Pillow (not
FFmpeg's drawtext) for the text because some FFmpeg builds ship without the
libfreetype/drawtext filter, and Pillow renders text reliably everywhere.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import textwrap


def _grab_frame(video_path: str, out_png: str, at: float = 1.0) -> bool:
    """Extract a single frame at ``at`` seconds into ``out_png``."""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("FFmpeg is required to extract a thumbnail frame.")
    subprocess.run(
        ["ffmpeg", "-y", "-ss", f"{at:.2f}", "-i", video_path, "-frames:v", "1", out_png],
        capture_output=True,
        text=True,
    )
    return os.path.exists(out_png)


def _load_font(size: int):
    """Best-effort bold font lookup with a graceful fallback."""
    from PIL import ImageFont

    candidates = [
        "DejaVuSans-Bold.ttf",
        "Arial Bold.ttf",
        "Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/Library/Fonts/Arial.ttf",
    ]
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def generate(video_path: str, hook: str, out_path: str, brand_handle: str = "") -> str:
    """Create a JPEG thumbnail at ``out_path`` and return its path."""
    from PIL import Image, ImageDraw

    frame = out_path + ".frame.png"
    if not _grab_frame(video_path, frame, at=1.0):
        raise RuntimeError("could not extract a frame for the thumbnail")

    img = Image.open(frame).convert("RGB")
    W, H = img.size

    # Darken the lower portion so overlaid text stays legible.
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rectangle([0, int(H * 0.58), W, H], fill=(0, 0, 0, 160))
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")

    draw = ImageDraw.Draw(img)

    # Hook text, wrapped to a few big lines near the bottom.
    text = (hook or "").strip()
    if text:
        font = _load_font(int(W * 0.075))
        lines = textwrap.wrap(text, width=18)[:3]
        y = int(H * 0.60)
        for line in lines:
            draw.text(
                (int(W * 0.06), y),
                line,
                font=font,
                fill=(255, 255, 255),
                stroke_width=max(2, int(W * 0.004)),
                stroke_fill=(0, 0, 0),
            )
            y += int(W * 0.09)

    # Channel handle badge top-left.
    if brand_handle:
        hf = _load_font(int(W * 0.042))
        handle = brand_handle if brand_handle.startswith("@") else f"@{brand_handle}"
        draw.text(
            (int(W * 0.06), int(H * 0.05)),
            handle,
            font=hf,
            fill=(255, 255, 255),
            stroke_width=max(2, int(W * 0.003)),
            stroke_fill=(0, 0, 0),
        )

    img.save(out_path, "JPEG", quality=88)
    try:
        os.remove(frame)
    except OSError:
        pass
    return out_path


def make_caption_png(
    text: str,
    out_path: str,
    *,
    width: int = 1080,
    height: int = 1920,
    center_y: int = 1360,
    max_line_chars: int = 22,
    max_lines: int = 3,
) -> str | None:
    """Render one caption line as a transparent 1080x1920 PNG (text centered in
    the lower third). Used as a libass-free fallback for burned-in captions:
    the editor composites these with time-gated FFmpeg overlays.

    Returns the PNG path, or ``None`` if there's nothing to draw.
    """
    from PIL import Image, ImageDraw

    text = (text or "").strip()
    if not text:
        return None

    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    font = _load_font(int(width * 0.054))  # ~58px, matches the ASS style
    lines = textwrap.wrap(text, width=max_line_chars)[:max_lines]
    line_h = int(width * 0.066)
    block_h = line_h * len(lines)
    y = center_y - block_h // 2

    for line in lines:
        tw = draw.textlength(line, font=font)
        draw.text(
            ((width - tw) / 2, y),
            line,
            font=font,
            fill=(255, 255, 255),
            stroke_width=max(3, int(width * 0.005)),
            stroke_fill=(0, 0, 0),
        )
        y += line_h

    img.save(out_path, "PNG")
    return out_path


def make_branding_overlay(
    out_path: str,
    *,
    width: int = 1080,
    height: int = 1920,
    headline: str = "",
    brand_handle: str = "",
    source_credit: str = "",
    show_header_bar: bool = True,
    show_watermark: bool = True,
) -> str | None:
    """Render a transparent 1080x1920 PNG containing the header bar, the channel
    watermark, and the source credit, to be composited onto the Short in a single
    FFmpeg overlay pass.

    This deliberately uses Pillow instead of FFmpeg's ``drawtext`` filter: some
    FFmpeg builds ship without libfreetype, so ``drawtext`` silently disappears
    and the branding never renders. Pillow draws text reliably on every build.

    Returns the PNG path, or ``None`` if there is nothing to draw.
    """
    from PIL import Image, ImageDraw

    draw_header = show_header_bar and bool(headline.strip())
    draw_wm = show_watermark and bool(brand_handle.strip())
    draw_credit = bool(source_credit.strip())
    if not (draw_header or draw_wm or draw_credit):
        return None

    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Header bar (top): translucent black band with the wrapped AI hook.
    if draw_header:
        bar_top = int(height * 0.047)   # ~90px
        bar_h = int(height * 0.115)     # ~220px
        draw.rectangle([0, bar_top, width, bar_top + bar_h], fill=(0, 0, 0, 140))
        font = _load_font(int(width * 0.048))  # ~52px
        lines = textwrap.wrap(headline.strip(), width=26)[:3]
        line_h = int(width * 0.058)
        y = bar_top + (bar_h - line_h * len(lines)) // 2
        for line in lines:
            tw = draw.textlength(line, font=font)
            draw.text(
                ((width - tw) / 2, y),
                line,
                font=font,
                fill=(255, 255, 255),
                stroke_width=3,
                stroke_fill=(0, 0, 0),
            )
            y += line_h

    # Watermark handle (bottom-right), on a small rounded translucent chip.
    if draw_wm:
        handle = brand_handle if brand_handle.startswith("@") else f"@{brand_handle}"
        hf = _load_font(int(width * 0.031))  # ~34px
        tw = draw.textlength(handle, font=hf)
        th = int(width * 0.031 * 1.25)
        pad = 16
        x1 = width - tw - pad * 2 - 40
        y1 = height - th - pad * 2 - 140
        draw.rounded_rectangle(
            [x1, y1, x1 + tw + pad * 2, y1 + th + pad * 2], radius=14, fill=(0, 0, 0, 95)
        )
        draw.text((x1 + pad, y1 + pad), handle, font=hf, fill=(255, 255, 255, 220))

    # Source credit (bottom-center), smaller and dimmer (good-faith attribution).
    if draw_credit:
        cf = _load_font(int(width * 0.022))  # ~24px
        credit = source_credit.strip()
        tw = draw.textlength(credit, font=cf)
        th = int(width * 0.022 * 1.25)
        pad = 8
        x1 = (width - tw) / 2 - pad
        y1 = height - th - pad * 2 - 30
        draw.rounded_rectangle(
            [x1, y1, x1 + tw + pad * 2, y1 + th + pad * 2], radius=8, fill=(0, 0, 0, 80)
        )
        draw.text((x1 + pad, y1 + pad), credit, font=cf, fill=(255, 255, 255, 185))

    img.save(out_path, "PNG")
    return out_path
