# YouTube Shorts Automation

One command turns high-view YouTube videos into captioned vertical Shorts and (optionally) uploads them automatically.

```
YouTube search  ->  transcript  ->  AI analysis  ->  interesting segments
   ->  FFmpeg vertical crop  ->  karaoke captions  ->  keyword zooms
   ->  AI-written context  ->  review dashboard  ->  upload
```

## What it does

1. Asks you for a **category** (menu or free-form topic).
2. Searches YouTube for the **highest-view** videos on that topic.
3. Pulls each video's **timestamped transcript** (Whisper fallback if none exists).
4. Uses **AI** (Groq, Gemini, or Ollama) to pick the most engaging segments.
5. Downloads the video and uses **FFmpeg** to crop it to 9:16 with a blurred background.
6. Burns **karaoke captions** (word-by-word highlighted ASS) from the real transcript timestamps.
7. Adds **keyword zooms** — brief punch-in effects on emphasis moments (numbers, strong words).
8. Uses AI to write a **title, description, tags, and hashtags**.
9. Saves Shorts to a **review dashboard** for human approval before uploading.
10. Optionally **uploads** the finished Short to your channel.

Everything after choosing the category is automatic.

## Requirements

- **Python 3.9+**
- **FFmpeg** on your PATH
  - macOS: `brew install ffmpeg`
  - Ubuntu: `sudo apt install ffmpeg`
  - Windows: https://ffmpeg.org/download.html

## Setup

```bash
# 1. Create a virtual environment and install dependencies
python3 -m venv venv
source venv/bin/activate        # run this every time you open a new terminal
pip install -r requirements.txt

# 2. Configure
cp .env.example .env
# then edit .env (see below)
```

### Credentials (important)

> YouTube's API does **not** use your Gmail username/password. It uses an API key
> for searching and Google OAuth for uploading. This is set up the correct way below.

1. **YouTube Data API key** (for searching) — required
   - Go to https://console.cloud.google.com/apis/credentials
   - Enable **YouTube Data API v3**, create an **API key**
   - Put it in `.env` as `YOUTUBE_API_KEY`

2. **AI provider** (free) — pick one in `.env`:
   - `AI_PROVIDER=groq` (**recommended**) — free, fast, high limits. Get a key at https://console.groq.com/keys and set `GROQ_API_KEY`
   - `AI_PROVIDER=gemini` — free tier, lower limits. Get a key at https://aistudio.google.com/app/apikey and set `GEMINI_API_KEY`
   - `AI_PROVIDER=ollama` — fully offline/free. Install https://ollama.com, run `ollama pull llama3.1`, then `ollama serve`

3. **Google OAuth** (for auto-upload) — required only if `AUTO_UPLOAD=true`
   - In Google Cloud Console → **Credentials** → **Create OAuth client ID** → **Desktop app**
   - Download the JSON, save it as `client_secret.json` (path set by `CLIENT_SECRET_FILE`)
   - First run opens a browser once to authorize; a `token.json` is cached afterward

## Run

```bash
source venv/bin/activate
python main.py
```

Pick a category, then let it work. Shorts are saved as "pending review" by default.

### Review dashboard

```bash
python dashboard.py
```

Opens at `http://localhost:5000`. Preview every Short, edit title/description/tags, then approve, reject, or upload — one click each.

## Tuning (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `REVIEW_MODE` | `true` | Hold Shorts for human approval instead of auto-uploading |
| `DEDUP` | `true` | Skip source segments already turned into a Short (safe re-runs) |
| `AUTO_UPLOAD` | `true` | Publish automatically (only when `REVIEW_MODE=false`) |
| `UPLOAD_PRIVACY` | `private` | `private`, `unlisted`, or `public` |
| `MAX_VIDEOS` | `3` | How many source videos per run |
| `SHORTS_PER_VIDEO` | `1` | Shorts to cut from each source video |
| `MIN_VIEWS` | `100000` | Only use videos with at least this many views |
| `MIN_SHORT_SECONDS` / `MAX_SHORT_SECONDS` | `15` / `60` | Short length bounds |
| `MAX_SOURCE_SECONDS` | `1800` | Skip source videos longer than this |
| `KARAOKE_CAPTIONS` | `true` | Word-by-word highlighted ASS captions (vs plain SRT) |
| `KEYWORD_ZOOM` | `true` | Brief punch-in zooms on emphasis moments |
| `KEYWORD_ZOOM_INTENSITY` | `0.12` | Zoom intensity (0.02–0.40) |
| `BRAND_HANDLE` | empty | Channel handle shown as corner watermark |
| `SHOW_HEADER_BAR` | `true` | Draw a top banner with the AI-written hook |
| `SHOW_WATERMARK` | `true` | Draw the brand handle watermark |
| `REFRAME_ZOOM` | `1.06` | Gentle foreground zoom (1.0–1.5) |
| `CREDIT_SOURCE` | `true` | Small "Source: ..." credit near the bottom |
| `VOICEOVER_MODE` | `off` | `off`, `ai`, or `file` — spoken commentary over each clip |
| `VOICEOVER_ENGINE` | `edge` | `edge` (online TTS) or `piper` (offline) |
| `VOICEOVER_VOICE` | `en-US-AndrewMultilingualNeural` | edge-tts voice name |
| `DUCK_VOLUME` | `0.15` | Lower original audio under voiceover (0.0–1.0) |
| `DASHBOARD_PORT` | `5000` | Port for the review dashboard |

## Notes & responsibility

- Start with `UPLOAD_PRIVACY=private` to review results before going public.
- Reuploading others' content can violate copyright and YouTube's policies. Use clips you have rights to, keep segments short/transformative, and credit sources. You are responsible for what you publish.
- If a video has no captions and Whisper isn't installed, that video is skipped. Install Whisper with `pip install openai-whisper` to enable the fallback.

## Project layout

```
main.py                 # single-command orchestrator
dashboard.py            # Flask review dashboard (python dashboard.py)
config.py               # env config + category presets
pipeline/
  youtube_search.py     # Data API search by views
  transcript.py         # captions + Whisper fallback
  downloader.py         # yt-dlp download
  analyzer.py           # AI -> interesting segments
  captions.py           # SRT + karaoke ASS captions
  editor.py             # FFmpeg vertical crop + burn captions + keyword zooms
  context_writer.py     # AI title/description/tags
  uploader.py           # Google OAuth upload
  voiceover.py          # TTS commentary (edge-tts / piper)
  state.py              # SQLite state: dedup, run history, review workflow
  ai.py                 # AI provider abstraction (Groq, Gemini, Ollama)
```
