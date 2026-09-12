# YouTube Shorts Automation

One command turns high-view YouTube videos into captioned vertical Shorts and (optionally) uploads them automatically.

```
YouTube search  ->  transcript  ->  AI analysis  ->  interesting segments
   ->  FFmpeg vertical crop  ->  burned captions  ->  AI-written context  ->  Short  ->  auto-upload
```

## What it does

1. Asks you for a **category** (menu or free-form topic).
2. Searches YouTube for the **highest-view** videos on that topic.
3. Pulls each video's **timestamped transcript** (Whisper fallback if none exists).
4. Uses a **free AI provider** to pick the most engaging segments.
5. Downloads the video and uses **FFmpeg** to crop it to 9:16 with a blurred background.
6. **Burns captions** built from the real transcript timestamps.
7. Uses AI to write a **title, description, tags, and hashtags**.
8. Optionally **uploads** the finished Short to your channel.

Everything after choosing the category is automatic.

## Requirements

- **Python 3.9+**
- **FFmpeg** on your PATH
  - macOS: `brew install ffmpeg`
  - Ubuntu: `sudo apt install ffmpeg`
  - Windows: https://ffmpeg.org/download.html

## Setup

```bash
# 1. Install dependencies
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
   - `AI_PROVIDER=gemini` → get a free key at https://aistudio.google.com/app/apikey and set `GEMINI_API_KEY`
   - `AI_PROVIDER=ollama` → fully offline/free. Install https://ollama.com, run `ollama pull llama3.1`, then `ollama serve`

3. **Google OAuth** (for auto-upload) — required only if `AUTO_UPLOAD=true`
   - In Google Cloud Console → **Credentials** → **Create OAuth client ID** → **Desktop app**
   - Download the JSON, save it as `client_secret.json` (path set by `CLIENT_SECRET_FILE`)
   - First run opens a browser once to authorize; a `token.json` is cached afterward

## Run

```bash
python main.py
```

Pick a category, then let it work. Finished Shorts and their metadata land in `output/`.

## Tuning (`.env`)

| Variable | Meaning |
|---|---|
| `MAX_VIDEOS` | How many source videos per run |
| `SHORTS_PER_VIDEO` | Shorts to cut from each source video |
| `MIN_VIEWS` | Only use videos with at least this many views |
| `MIN_SHORT_SECONDS` / `MAX_SHORT_SECONDS` | Short length bounds |
| `MAX_SOURCE_SECONDS` | Skip source videos longer than this |
| `AUTO_UPLOAD` | `true` to publish automatically |
| `UPLOAD_PRIVACY` | `private`, `unlisted`, or `public` |

## Notes & responsibility

- Start with `UPLOAD_PRIVACY=private` to review results before going public.
- Reuploading others' content can violate copyright and YouTube's policies. Use clips you have rights to, keep segments short/transformative, and credit sources. You are responsible for what you publish.
- If a video has no captions and Whisper isn't installed, that video is skipped. Install Whisper with `pip install openai-whisper` to enable the fallback.

## Project layout

```
main.py                 # single-command orchestrator
config.py               # env config + category presets
pipeline/
  youtube_search.py     # Data API search by views
  transcript.py         # captions + Whisper fallback
  downloader.py         # yt-dlp download
  analyzer.py           # AI -> interesting segments
  captions.py           # transcript timestamps -> SRT
  editor.py             # FFmpeg vertical crop + burn captions
  context_writer.py     # AI title/description/tags
  uploader.py           # Google OAuth upload
```
