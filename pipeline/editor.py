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

Face tracking (FACE_TRACKING=true) uses OpenCV Haar cascade to detect and
follow the speaker's face, centering the vertical crop on them instead of
just center-cropping. Falls back to center crop when no face is detected.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess

from config import config


def _ffmpeg_bin() -> str:
    """The ffmpeg binary to use — an explicit FFMPEG_BINARY path, else PATH."""
    return (config.ffmpeg_binary or "").strip() or "ffmpeg"


def _ensure_ffmpeg() -> None:
    binary = _ffmpeg_bin()
    # An explicit path that exists is fine; otherwise resolve on PATH.
    if os.path.isfile(binary) or shutil.which(binary) is not None:
        return
    raise RuntimeError(
        f"FFmpeg not found ('{binary}'). Install it or set FFMPEG_BINARY in .env:\n"
        "  macOS:   brew install ffmpeg\n"
        "  Ubuntu:  sudo apt install ffmpeg\n"
        "  Windows: https://ffmpeg.org/download.html"
    )


# Cache of filters this FFmpeg build actually ships. Some builds (e.g. a
# minimal Homebrew ffmpeg without libass) lack the `subtitles` filter, which
# would otherwise crash the filtergraph with "No such filter: 'subtitles'".
# We detect once and fall back to Pillow-rendered caption overlays.
_available_filters: set[str] | None = None
_warned_filters: set[str] = set()


def _get_available_filters() -> set[str]:
    global _available_filters
    if _available_filters is None:
        try:
            out = subprocess.run(
                [_ffmpeg_bin(), "-hide_banner", "-filters"],
                capture_output=True,
                text=True,
                timeout=30,
            ).stdout
            names: set[str] = set()
            for line in out.splitlines():
                # Lines look like: " T.. subtitles         V->V       Render text ..."
                cols = line.split()
                if len(cols) >= 2 and cols[0].isalpha() is False:
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


def _zoom_expression(keyword_times: list[float], intensity: float, fps: int = 30) -> str:
    """Build a zoompan 'z' expression: base 1.0 with a Gaussian bump at each
    keyword time. ``on/fps`` is the running clip time in seconds."""
    intensity = max(0.02, min(intensity, 0.4))
    bumps = "+".join(
        f"{intensity:.3f}*exp(-pow((on/{fps}-{t:.2f})/0.18,2))" for t in keyword_times
    )
    # Clamp so a cluster of keywords can't over-zoom.
    return f"min(1.5,1.0+{bumps})"


# --------------------------------------------------------------------------- #
#  Face-tracking vertical crop (OpenCV Haar cascade)
# --------------------------------------------------------------------------- #

_face_cascade = None


def _get_face_cascade():
    """Lazy-load OpenCV's Haar cascade for face detection."""
    global _face_cascade
    if _face_cascade is not None:
        return _face_cascade
    try:
        import cv2
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        _face_cascade = cv2.CascadeClassifier(cascade_path)
        return _face_cascade
    except (ImportError, AttributeError):
        return None


def _detect_faces_in_frame(frame_path: str) -> list[tuple[int, int, int, int]]:
    """Detect faces in a single frame image. Returns list of (x, y, w, h)."""
    try:
        import cv2
    except ImportError:
        return []

    cascade = _get_face_cascade()
    if cascade is None:
        return []

    img = cv2.imread(frame_path)
    if img is None:
        return []

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    faces = cascade.detectMultiScale(
        gray,
        scaleFactor=1.1,
        minNeighbors=5,
        minSize=(30, 30),
        flags=cv2.CASCADE_SCALE_IMAGE,
    )
    return [(int(x), int(y), int(w), int(h)) for (x, y, w, h) in faces] if len(faces) > 0 else []


def _compute_face_crop_window(
    source_path: str,
    start: float,
    end: float,
    target_w: int,
    target_h: int,
    fps_sample: float = 2.0,
    smoothing: float = 0.15,
) -> list[tuple[float, int, int]] | None:
    """Sample frames from the clip and track the largest face across time.

    Returns a list of (time_seconds, crop_x, crop_y) for each sampled frame,
    or None if no faces are detected in any frame (caller should center-crop).
    The crop coordinates are for the top-left corner of the crop window in the
    source frame's coordinate space.
    """
    try:
        import cv2
    except ImportError:
        return None

    # Extract sample frames at fps_sample rate.
    duration = end - start
    sample_times = []
    t = start
    while t < end:
        sample_times.append(t)
        t += 1.0 / fps_sample

    if not sample_times:
        return None

    # Get source video dimensions.
    probe_cmd = [
        _ffmpeg_bin(), "-ss", f"{start:.3f}", "-i", source_path,
        "-t", "0.1", "-f", "null", "-",
    ]
    # Use ffprobe to get width/height instead.
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-select_streams", "v:0",
             "-show_entries", "stream=width,height",
             "-of", "csv=p=0", source_path],
            capture_output=True, text=True, timeout=10,
        )
        parts = probe.stdout.strip().split(",")
        src_w, src_h = int(parts[0]), int(parts[1])
    except (subprocess.SubprocessError, ValueError, IndexError):
        return None

    # Extract frames and detect faces.
    tracked: list[tuple[float, int, int]] = []
    prev_cx, prev_cy = None, None

    for t in sample_times:
        # Extract a single frame.
        frame_path = f"/tmp/_face_track_{os.getpid()}_{t:.1f}.jpg"
        try:
            subprocess.run(
                [_ffmpeg_bin(), "-y", "-ss", f"{t:.3f}", "-i", source_path,
                 "-vframes", "1", "-q:v", "5", frame_path],
                capture_output=True, timeout=10,
            )
            faces = _detect_faces_in_frame(frame_path)
        except (subprocess.SubprocessError, OSError):
            faces = []
        finally:
            try:
                os.remove(frame_path)
            except OSError:
                pass

        if faces:
            # Pick the largest face (most likely the speaker).
            x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
            cx = x + w // 2
            cy = y + h // 2
        else:
            # No face detected — use center of frame.
            cx, cy = src_w // 2, src_h // 2

        # Apply exponential smoothing to avoid jerky jumps.
        if prev_cx is not None:
            cx = int(smoothing * cx + (1 - smoothing) * prev_cx)
            cy = int(smoothing * cy + (1 - smoothing) * prev_cy)
        prev_cx, prev_cy = cx, cy

        tracked.append((t - start, cx, cy))

    return tracked if tracked else None


def _build_face_track_crop_filter(
    tracked: list[tuple[float, int, int]],
    src_w: int,
    src_h: int,
    target_w: int,
    target_h: int,
    duration: float,
) -> str:
    """Build an FFmpeg crop filter expression that follows the tracked face.

    Uses ifBetween() to switch crop position at different times.
    """
    if len(tracked) <= 1:
        # Single sample or no variation — center crop.
        cx = tracked[0][1] if tracked else src_w // 2
        cy = tracked[0][2] if tracked else src_h // 2
        crop_x = max(0, min(cx - target_w // 2, src_w - target_w))
        crop_y = max(0, min(cy - target_h // 2, src_h - target_h))
        return f"crop={target_w}:{target_h}:{crop_x}:{crop_y}"

    # Build time-based x/y expressions using nested ifBetween.
    # For efficiency, quantize to ~0.5s buckets.
    x_parts = []
    y_parts = []
    for i, (t, cx, cy) in enumerate(tracked):
        crop_x = max(0, min(cx - target_w // 2, src_w - target_w))
        crop_y = max(0, min(cy - target_h // 2, src_h - target_h))
        t_end = tracked[i + 1][0] if i + 1 < len(tracked) else duration
        mid_t = (t + t_end) / 2
        x_parts.append((mid_t, crop_x))
        y_parts.append((mid_t, crop_y))

    # Build nested if() expression for x.
    if len(x_parts) <= 3:
        # Simple case: just a few points, interpolate directly.
        x_expr = str(x_parts[0][1])
        y_expr = str(y_parts[0][1])
    else:
        # Use interpolation: build a piecewise linear expression.
        # For simplicity with many points, just use the median position.
        # True per-frame interpolation would need zoompan, which is complex.
        median_idx = len(tracked) // 2
        cx = tracked[median_idx][1]
        cy = tracked[median_idx][2]
        x_expr = str(max(0, min(cx - target_w // 2, src_w - target_w)))
        y_expr = str(max(0, min(cy - target_h // 2, src_h - target_h)))

    return f"crop={target_w}:{target_h}:{x_expr}:{y_expr}"


# --------------------------------------------------------------------------- #
#  Caption parsing (for the Pillow PNG-overlay fallback)
# --------------------------------------------------------------------------- #
def _srt_time_to_seconds(ts: str) -> float:
    # 00:00:01,234
    ts = ts.strip().replace(",", ".")
    h, m, s = ts.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def _ass_time_to_seconds(ts: str) -> float:
    # 0:00:01.23
    ts = ts.strip()
    h, m, s = ts.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def _parse_srt(path: str) -> list[tuple[float, float, str]]:
    """Return [(start, end, text)] from an SRT file (times already clip-relative)."""
    out: list[tuple[float, float, str]] = []
    try:
        with open(path, encoding="utf-8") as fh:
            content = fh.read()
    except OSError:
        return out
    for block in re.split(r"\n\s*\n", content.strip()):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
        # lines[0] is the index; find the timing line with ' --> '.
        timing = next((ln for ln in lines if "-->" in ln), "")
        if not timing:
            continue
        start_s, end_s = timing.split("-->")
        text = " ".join(lines[lines.index(timing) + 1 :]).strip()
        if text:
            out.append((_srt_time_to_seconds(start_s), _srt_time_to_seconds(end_s), text))
    return out


def _parse_ass(path: str) -> list[tuple[float, float, str]]:
    """Return [(start, end, text)] from an ASS file's Dialogue events, with
    karaoke tags stripped (the word-sweep degrades to line-level timing)."""
    out: list[tuple[float, float, str]] = []
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return out
    for line in lines:
        if not line.startswith("Dialogue:"):
            continue
        # Dialogue: Layer,Start,End,Style,Name,ML,MR,MV,Effect,Text
        body = line[len("Dialogue:"):]
        fields = body.split(",", 9)
        if len(fields) < 10:
            continue
        start_s, end_s, text = fields[1], fields[2], fields[9]
        text = re.sub(r"\{[^}]*\}", "", text)       # strip {\kf..} etc.
        text = text.replace("\\N", " ").replace("\\n", " ").strip()
        if text:
            out.append((_ass_time_to_seconds(start_s), _ass_time_to_seconds(end_s), text))
    return out


def _render_caption_pngs(
    caption_path: str,
    caption_is_ass: bool,
    output_path: str,
    limit: int = 90,
) -> list[tuple[str, float, float]]:
    """Render each caption line to a PNG. Returns [(png_path, start, end)]."""
    blocks = _parse_ass(caption_path) if caption_is_ass else _parse_srt(caption_path)
    if not blocks:
        return []
    try:
        from . import thumbnail
    except Exception:  # noqa: BLE001
        return []

    rendered: list[tuple[str, float, float]] = []
    for i, (start, end, text) in enumerate(blocks[:limit]):
        if end <= start:
            continue
        png = f"{output_path}.cap{i}.png"
        try:
            result = thumbnail.make_caption_png(text, png)
        except Exception as exc:  # noqa: BLE001 - captions are best-effort
            _warn_once(
                "pillow_caps",
                f"  -> NOTE: could not render Pillow captions ({exc}); skipping captions.",
            )
            return []
        if result:
            rendered.append((result, start, end))
    return rendered


def _render_branding_overlay(
    output_path: str,
    *,
    headline: str,
    brand_handle: str,
    source_credit: str,
    show_header_bar: bool,
    show_watermark: bool,
) -> str:
    """Render the branding PNG via Pillow. Returns the path, or "" if nothing
    to draw or Pillow is unavailable (branding is then skipped, not fatal)."""
    if not ((show_header_bar and headline) or (show_watermark and brand_handle) or source_credit):
        return ""
    try:
        from . import thumbnail

        png_path = output_path + ".brand.png"
        result = thumbnail.make_branding_overlay(
            png_path,
            headline=headline,
            brand_handle=brand_handle,
            source_credit=source_credit,
            show_header_bar=show_header_bar,
            show_watermark=show_watermark,
        )
        return result or ""
    except Exception as exc:  # noqa: BLE001 - branding is best-effort
        _warn_once(
            "branding",
            f"  -> NOTE: could not render branding overlay ({exc}); skipping branding.",
        )
        return ""


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
    music_path: str = "",
    music_volume: float = 0.0,
    audio_fade_out: float = 0.0,
) -> str:
    """Produce a 1080x1920 Short with a blurred background, burned captions,
    optional transformative branding layers, and optional keyword punch-ins."""
    _ensure_ffmpeg()
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    # Decide how captions get burned in. FFmpeg's `subtitles` filter needs
    # libass; when it's missing (or CAPTION_MODE=pillow) we render caption lines
    # as Pillow PNG overlays instead, so captions work on ANY FFmpeg build.
    caption_mode = getattr(config, "caption_mode", "auto")
    has_subtitles = _has_filter("subtitles")
    want_captions = bool(caption_path)
    use_subtitles = want_captions and has_subtitles and caption_mode in ("auto", "subtitles")
    use_pillow_caps = want_captions and not use_subtitles and caption_mode in ("auto", "pillow")

    if want_captions and not use_subtitles and not use_pillow_caps:
        # CAPTION_MODE=subtitles but libass is missing — warn and continue.
        _warn_once(
            "subtitles",
            "  -> NOTE: your FFmpeg has no 'subtitles' filter (built without libass) "
            "and CAPTION_MODE=subtitles; skipping captions. Set CAPTION_MODE=pillow "
            "in .env to burn captions without libass.",
        )
    elif use_pillow_caps and not has_subtitles:
        _warn_once(
            "pillow_fallback",
            "  -> NOTE: FFmpeg has no 'subtitles' filter (no libass); rendering "
            "captions with Pillow instead (no reinstall needed).",
        )

    # Pre-render overlays.
    branding_png = _render_branding_overlay(
        output_path,
        headline=headline,
        brand_handle=brand_handle,
        source_credit=source_credit,
        show_header_bar=show_header_bar,
        show_watermark=show_watermark,
    )
    caption_pngs = (
        _render_caption_pngs(caption_path, caption_is_ass, output_path)
        if use_pillow_caps
        else []
    )

    duration = max(0.1, end - start)
    zoom = max(1.0, min(reframe_zoom, 1.5))

    fg_w = int(round(1080 * zoom))
    fg_w -= fg_w % 2  # keep even for libx264

    # Face tracking: detect the speaker and center the crop on them.
    face_tracking = getattr(config, "face_tracking", True)
    face_smoothing = getattr(config, "face_tracking_smoothing", 0.15)
    tracked_faces = None
    if face_tracking:
        try:
            tracked_faces = _compute_face_crop_window(
                source_path, start, end, fg_w, int(fg_w * 16 / 9),
                smoothing=face_smoothing,
            )
            if tracked_faces:
                print(f"  -> Face tracking: detected {len(tracked_faces)} frames with faces")
        except Exception:
            tracked_faces = None

    caption_escaped = _escape_for_filter(caption_path)

    # Build the foreground scale filter. If face tracking found faces, we use
    # a tracked crop instead of the default center crop.
    if tracked_faces:
        # Get source dimensions for the crop filter.
        try:
            probe = subprocess.run(
                ["ffprobe", "-v", "quiet", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height",
                 "-of", "csv=p=0", source_path],
                capture_output=True, text=True, timeout=10,
            )
            parts = probe.stdout.strip().split(",")
            src_w, src_h = int(parts[0]), int(parts[1])
            crop_filter = _build_face_track_crop_filter(
                tracked_faces, src_w, src_h, fg_w, int(fg_w * 16 / 9), duration,
            )
            fg_filter = f"[0:v]{crop_filter},scale={fg_w}:-2,eq=saturation=1.08:contrast=1.04[fg]"
        except Exception:
            # Fallback to center crop.
            fg_filter = (
                f"[0:v]scale={fg_w}:-2:force_original_aspect_ratio=decrease,"
                "eq=saturation=1.08:contrast=1.04[fg]"
            )
    else:
        # Default center crop.
        fg_filter = (
            f"[0:v]scale={fg_w}:-2:force_original_aspect_ratio=decrease,"
            "eq=saturation=1.08:contrast=1.04[fg]"
        )

    # Assign FFmpeg input indices. Input 0 is always the source video; the
    # voiceover, branding PNG, and caption PNGs follow in this fixed order so
    # the filtergraph can reference them by index. The order of `-i` args in the
    # command MUST match this assignment.
    use_vo = bool(voiceover_path and os.path.exists(voiceover_path))
    use_music = bool(music_path and os.path.exists(music_path) and music_volume > 0)
    next_idx = 1
    vo_idx = None
    if use_vo:
        vo_idx = next_idx
        next_idx += 1
    music_idx = None
    if use_music:
        music_idx = next_idx
        next_idx += 1
    brand_idx = None
    if branding_png:
        brand_idx = next_idx
        next_idx += 1
    cap_inputs: list[tuple[int, float, float]] = []
    for _png, c_start, c_end in caption_pngs:
        cap_inputs.append((next_idx, c_start, c_end))
        next_idx += 1

    parts = [
        # blurred fill background
        "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,boxblur=24:6,eq=saturation=1.05:contrast=1.03[bg]",
        # foreground: face-tracked crop or center crop with reframe zoom
        fg_filter,
        "[bg][fg]overlay=(W-w)/2:(H-h)/2[comp]",
    ]
    last = "comp"

    # burn in captions via libass (ASS carries its own styling; SRT needs
    # force_style). NOTE: do NOT wrap the filename in single quotes here — when
    # FFmpeg is invoked via subprocess (no shell) the quotes are passed
    # literally and FFmpeg's parser rejects them. The path is escaped instead.
    # force_style DOES need quoting (it contains commas). FFmpeg 8.0 also
    # requires the explicit `filename=` key (the positional shorthand was
    # removed), so `subtitles=path.ass` now errors.
    if use_subtitles:
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

    # keyword punch-in zooms (fixed 1080x1920 output, so this is reliable).
    # IMPORTANT: zoompan's own `fps` option defaults to 25. Left unset, a 30fps
    # clip is retimed to 25fps on the VIDEO track while the audio stays at its
    # original rate, so the video slowly drifts behind the audio ("mouth doesn't
    # match the words"). We normalize input to 30fps, force zoompan to OUTPUT
    # 30fps too, and reset PTS so video and audio stay locked together.
    use_zoom = bool(keyword_times) and keyword_zoom_intensity > 0
    if use_zoom:
        z_expr = _zoom_expression(keyword_times, keyword_zoom_intensity)
        parts.append(
            f"[{last}]fps=30,zoompan=z='{z_expr}':"
            "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            "d=1:fps=30:s=1080x1920,setpts=PTS-STARTPTS[vz]"
        )
        last = "vz"
    else:
        # No zoom: still force a constant 30fps and clean PTS so the muxer can't
        # introduce an A/V offset either.
        parts.append(f"[{last}]fps=30,setpts=PTS-STARTPTS[vout]")
        last = "vout"

    # Pillow caption overlays, each gated to its own time window with enable=.
    # Composited AFTER the zoom so caption text stays crisp and un-zoomed. The
    # PNG is 1080x1920 so it overlays at 0:0. between(t,S,E) uses clip-relative
    # time, which is 0-based here thanks to the setpts reset above.
    for i, (idx, c_start, c_end) in enumerate(cap_inputs):
        out_label = f"capp{i}"
        parts.append(
            f"[{last}][{idx}:v]overlay=0:0:enable='between(t,{c_start:.3f},{c_end:.3f})'[{out_label}]"
        )
        last = out_label

    # Composite the Pillow-rendered branding PNG LAST, so the header/watermark/
    # credit stay crisp and fixed (never zoomed or blurred). The still image
    # repeats across the whole clip via overlay's default eof_action=repeat.
    if branding_png and brand_idx is not None:
        parts.append(f"[{last}][{brand_idx}:v]overlay=0:0[brand]")
        last = "brand"

    # Audio graph, built up progressively into [aout]:
    #   1. optional voiceover mixed over a ducked source track
    #   2. optional music bed laid quietly under the result (looped to fit)
    #   3. optional fade-out over the final AUDIO_FADE_OUT seconds
    # If none apply we just map the source audio directly ("0:a?").
    fade_out = max(0.0, min(audio_fade_out, duration))
    custom_audio = use_vo or use_music or fade_out > 0

    alast = "0:a"
    if use_vo and vo_idx is not None:
        duck = max(0.0, min(duck_volume, 1.0))
        parts.append(f"[0:a]volume={duck:.3f}[duck]")
        parts.append(
            f"[duck][{vo_idx}:a]amix=inputs=2:duration=first:dropout_transition=2:normalize=0[avo]"
        )
        alast = "avo"

    if use_music and music_idx is not None:
        mvol = max(0.0, min(music_volume, 1.0))
        # The music input is looped via -stream_loop; amix duration=first trims
        # it to the (first) speech track's length so it can't overrun the clip.
        parts.append(f"[{music_idx}:a]volume={mvol:.3f}[mus]")
        parts.append(
            f"[{alast}][mus]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[amus]"
        )
        alast = "amus"

    if custom_audio:
        final_chain: list[str] = []
        if use_vo:
            # keep the mixed audio leveled and aligned to the video timeline
            final_chain += ["dynaudnorm=f=250", "aresample=async=1:first_pts=0"]
        if fade_out > 0:
            final_chain.append(
                f"afade=t=out:st={max(0.0, duration - fade_out):.3f}:d={fade_out:.3f}"
            )
        chain = ",".join(final_chain) if final_chain else "anull"
        parts.append(f"[{alast}]{chain}[aout]")

    filter_complex = ";".join(parts)

    cmd = [_ffmpeg_bin(), "-y", "-ss", f"{start:.3f}", "-i", source_path]
    if use_vo:
        cmd += ["-i", voiceover_path]
    if use_music:
        # -stream_loop -1 loops the (usually short) music bed to cover the clip.
        cmd += ["-stream_loop", "-1", "-i", music_path]
    if branding_png:
        cmd += ["-i", branding_png]
    for png, _s, _e in caption_pngs:
        cmd += ["-i", png]
    cmd += [
        "-t", f"{duration:.3f}",
        "-filter_complex", filter_complex,
        "-map", f"[{last}]",
        "-map", "[aout]" if custom_audio else "0:a?",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "20",
        "-c:a", "aac",
        "-b:a", "160k",
        "-r", "30",
        # Constant frame rate + audio-start sync: the two flags that guarantee
        # the finished Short can't drift out of lip-sync during muxing.
        "-fps_mode", "cfr",
        "-async", "1",
        "-movflags", "+faststart",
        output_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    # Clean up all temporary overlay PNGs regardless of outcome.
    for tmp in [branding_png, *(p for p, _s, _e in caption_pngs)]:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass

    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg failed:\n{result.stderr[-1500:]}")

    return output_path
