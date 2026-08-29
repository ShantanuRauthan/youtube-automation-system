"""Spoken commentary voiceover for each Short.

This is the single most valuable transformative-use layer: your own spoken
analysis/reaction becomes the substance of the video, rather than a straight
reupload. Three modes (set VOICEOVER_MODE in .env):

  off  -> no voiceover
  ai   -> the AI writes a short commentary script, then TTS speaks it
  file -> use your own recording (VOICEOVER_FILE) for every Short

For AI voice, two free engines are supported (VOICEOVER_ENGINE):
  edge  -> edge-tts, Microsoft neural voices, free, no API key (needs internet)
  piper -> fully offline local voices (needs PIPER_MODEL, a .onnx voice file)

Nothing here defeats YouTube Content ID. It makes the Short genuinely
transformative, which is what actually protects you from claims.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess

from config import config
from pipeline.ai import AIError, generate_text

# Rough speaking rate for a natural narration voice (words per second).
_WORDS_PER_SECOND = 2.4


def _script_from_ai(hook: str, reason: str, snippet: str, duration: float) -> str:
    """Ask the AI for a concise commentary script sized to the clip length."""
    max_words = max(12, int(duration * _WORDS_PER_SECOND))
    prompt = f"""You are a YouTube Shorts creator recording a voiceover that plays OVER a clip.
Write ONLY the words to be spoken — no stage directions, no quotes, no labels.

The clip's hook: "{hook}"
Why it's interesting: "{reason}"
Clip transcript for reference:
\"\"\"{snippet[:1200]}\"\"\"

Rules:
- Add YOUR OWN insight, framing, or reaction — do not just repeat the transcript.
- Conversational and punchy. Start with a strong first line that grabs attention.
- MUST fit in about {max_words} words (the clip is ~{duration:.0f} seconds).
- End with a light call to follow for more.
Return just the spoken words as plain text."""
    text = generate_text(prompt).strip()
    # Strip accidental surrounding quotes/fences.
    text = text.strip("`").strip().strip('"').strip()
    # Hard cap so it never overruns the clip badly.
    words = text.split()
    if len(words) > max_words + 8:
        text = " ".join(words[: max_words + 8])
    return text or hook


def _synthesize_edge(text: str, out_path: str) -> str:
    try:
        import edge_tts  # noqa: WPS433
    except ImportError as exc:
        raise AIError("edge-tts is not installed. Run: pip install edge-tts") from exc

    async def _run() -> None:
        communicate = edge_tts.Communicate(text, config.voiceover_voice)
        await communicate.save(out_path)

    asyncio.run(_run())
    return out_path


def _synthesize_piper(text: str, out_path: str) -> str:
    if shutil.which("piper") is None:
        raise AIError(
            "piper is not installed or not on PATH. Install it from "
            "https://github.com/rhasspy/piper (or `pip install piper-tts`)."
        )
    result = subprocess.run(
        ["piper", "--model", config.piper_model, "--output_file", out_path],
        input=text,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AIError(f"piper failed:\n{result.stderr[-800:]}")
    return out_path


def get_voiceover(
    *,
    hook: str,
    reason: str,
    snippet: str,
    duration: float,
    out_path: str,
) -> tuple[str, str]:
    """Return (audio_path, spoken_text) for this Short, or ("", "") if disabled.

    Never raises — on any failure it logs and returns no voiceover so the
    pipeline still produces a Short (just without narration).
    """
    mode = config.voiceover_mode
    if mode == "off":
        return "", ""

    if mode == "file":
        # Reuse the user's own recording for every Short.
        return (config.voiceover_file, "") if config.voiceover_file else ("", "")

    # mode == "ai"
    try:
        script = _script_from_ai(hook, reason, snippet, duration)
    except AIError as exc:
        print(f"     ! voiceover script generation failed ({exc}); using the hook text")
        script = hook or ""
    if not script:
        return "", ""

    try:
        if config.voiceover_engine == "piper":
            audio = _synthesize_piper(script, out_path)
        else:
            audio = _synthesize_edge(script, out_path)
        return audio, script
    except AIError as exc:
        print(f"     ! text-to-speech failed ({exc}); continuing without voiceover")
        return "", script
