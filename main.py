#!/usr/bin/env python3
"""YouTube Shorts Automation — single-command pipeline.

    python main.py

It asks for a category, then automatically:
  YouTube search (high-view videos) -> transcript -> AI analysis
  -> interesting segments -> FFmpeg vertical crop -> burned captions
  -> AI-written title/description/tags -> (optional) auto-upload.
"""

from __future__ import annotations

import json
import os
import sys
import traceback

from config import CATEGORIES, config
from pipeline import (
    analyzer,
    captions,
    context_writer,
    downloader,
    editor,
    state,
    transcript as transcript_mod,
    uploader,
    voiceover,
    youtube_search,
)


# --------------------------------------------------------------------------- #
#  Small console helpers
# --------------------------------------------------------------------------- #
def hr() -> None:
    print("-" * 60)


def step(msg: str) -> None:
    print(f"  -> {msg}")


def choose_category() -> tuple[str, dict]:
    names = list(CATEGORIES.keys())
    print("\nPick a category to source videos from:\n")
    for i, name in enumerate(names, 1):
        print(f"  {i:>2}. {name}")
    print()

    while True:
        raw = input("Enter a number (or type your own topic): ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(names):
            name = names[int(raw) - 1]
            return name, CATEGORIES[name]
        if raw:
            # Free-form topic: default to Education-style category id "27".
            return raw, {"query": raw, "category_id": "27"}
        print("  Please enter a valid choice.")


# --------------------------------------------------------------------------- #
#  Per-video processing
# --------------------------------------------------------------------------- #
def process_video(video, category_name: str, category_id: str, work_dir: str, run_id: int) -> list[dict]:
    produced: list[dict] = []

    step(f"Fetching transcript for: {video.title[:60]}")
    segments_transcript = transcript_mod.fetch_transcript(video.video_id)

    step("Downloading source video")
    source_path = downloader.download_video(video.video_id, work_dir)

    if not segments_transcript:
        step("No YouTube captions found — trying local Whisper fallback")
        segments_transcript = transcript_mod.transcribe_with_whisper(source_path)

    if not segments_transcript:
        step("Could not obtain a transcript. Skipping this video.")
        return produced

    step("Analyzing transcript for interesting segments (AI)")
    found = analyzer.find_segments(segments_transcript, video.title)
    if not found:
        step("AI found no strong segments. Skipping.")
        return produced

    for idx, seg in enumerate(found, 1):
        # Dedup: skip segments we've already turned into a Short (safe re-runs).
        if config.dedup and state.segment_overlaps(video.video_id, seg.start, seg.end):
            step(f"Segment {idx}: already used before ({seg.start:.0f}s–{seg.end:.0f}s) — skipping.")
            continue

        step(f"Segment {idx}: {seg.start:.0f}s–{seg.end:.0f}s ({seg.duration:.0f}s) — {seg.hook[:50]}")

        base = f"{video.video_id}_short{idx}"
        out_path = os.path.join(config.output_dir, f"{base}.mp4")

        # Animated karaoke captions (ASS) or plain SRT, per config.
        if config.karaoke_captions:
            caption_path = os.path.join(work_dir, f"{base}.ass")
            captions.build_ass(segments_transcript, seg.start, seg.end, caption_path)
            caption_is_ass = True
        else:
            caption_path = os.path.join(work_dir, f"{base}.srt")
            captions.build_srt(segments_transcript, seg.start, seg.end, caption_path)
            caption_is_ass = False

        # Emphasis moments used to trigger brief punch-in zooms.
        kw_times = (
            captions.keyword_times(segments_transcript, seg.start, seg.end)
            if config.keyword_zoom
            else []
        )

        # Grab the transcript text inside this window (used for voiceover + context).
        snippet = " ".join(
            t.text for t in segments_transcript if t.end > seg.start and t.start < seg.end
        )

        voiceover_path = ""
        if config.voiceover_mode != "off":
            step(f"Generating {config.voiceover_mode} voiceover commentary")
            vo_out = os.path.join(work_dir, f"{base}_vo.mp3")
            voiceover_path, spoken = voiceover.get_voiceover(
                hook=seg.hook,
                reason=seg.reason,
                snippet=snippet,
                duration=seg.duration,
                out_path=vo_out,
            )
            if voiceover_path and spoken:
                step(f'Commentary: "{spoken[:60]}..."')

        step("Cropping to vertical + burning captions + branding (FFmpeg)")
        source_credit = f"Source: {video.title[:40]}" if config.credit_source else ""
        editor.make_short(
            source_path,
            seg.start,
            seg.end,
            caption_path,
            out_path,
            caption_is_ass=caption_is_ass,
            headline=seg.hook,
            brand_handle=config.brand_handle,
            source_credit=source_credit,
            show_header_bar=config.show_header_bar,
            show_watermark=config.show_watermark,
            reframe_zoom=config.reframe_zoom,
            keyword_times=kw_times,
            keyword_zoom_intensity=config.keyword_zoom_intensity,
            voiceover_path=voiceover_path,
            duck_volume=config.duck_volume,
        )

        step("Writing title / description / tags (AI)")
        meta = context_writer.write_context(
            category=category_name,
            source_title=video.title,
            hook=seg.hook,
            segment_reason=seg.reason,
            transcript_snippet=snippet,
        )

        record = {
            "file": out_path,
            "title": meta.title,
            "description": meta.description,
            "tags": meta.tags,
            "hashtags": meta.hashtags,
            "source_video": video.url,
            "segment": {"start": seg.start, "end": seg.end},
            "youtube_id": None,
        }

        yt_id: str | None = None
        if config.review_mode:
            # Hold for human approval in the dashboard; never auto-upload.
            status = state.PENDING
            step("Review mode on — saved as pending. Approve it in the dashboard.")
        elif config.auto_upload:
            step("Uploading to YouTube")
            try:
                yt_id = uploader.upload_short(
                    file_path=out_path,
                    title=meta.title,
                    description=meta.description,
                    tags=meta.tags,
                    category_id=category_id,
                )
                record["youtube_id"] = yt_id
                status = state.UPLOADED
                step(f"Uploaded: https://youtube.com/shorts/{yt_id} ({config.upload_privacy})")
            except Exception as exc:  # keep the local file even if upload fails
                status = state.FAILED
                step(f"Upload failed (file kept locally): {exc}")
        else:
            status = state.SAVED
            step("AUTO_UPLOAD is off — Short saved locally only.")

        # Persist to the state DB (dedup, history, and the review dashboard).
        state.record_short(
            run_id=run_id,
            source_video_id=video.video_id,
            source_url=video.url,
            source_title=video.title,
            seg_start=seg.start,
            seg_end=seg.end,
            file=out_path,
            title=meta.title,
            description=meta.description,
            tags=meta.tags,
            hashtags=meta.hashtags,
            category_id=category_id,
            hook=seg.hook,
            status=status,
            youtube_id=yt_id,
        )

        # Save metadata sidecar next to the video.
        with open(out_path.replace(".mp4", ".json"), "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=2)

        produced.append(record)

    return produced


# --------------------------------------------------------------------------- #
#  Entry point
# --------------------------------------------------------------------------- #
def main() -> int:
    print("\n=== YouTube Shorts Automation ===")

    problems = config.validate()
    if problems:
        print("\nConfiguration problems found:\n")
        for p in problems:
            print(f"  ! {p}")
        print("\nCopy .env.example to .env and fill in the values, then re-run.")
        return 1

    state.init_db()

    category_name, category = choose_category()
    print(f"\nCategory: {category_name}  |  search query: '{category['query']}'")
    upload_mode = "review (dashboard)" if config.review_mode else ("auto" if config.auto_upload else "off")
    print(f"AI provider: {config.ai_provider}  |  upload mode: {upload_mode}")
    hr()

    os.makedirs(config.output_dir, exist_ok=True)
    work_dir = os.path.join(config.output_dir, "_work")
    os.makedirs(work_dir, exist_ok=True)

    run_id = state.start_run(category_name, category["query"])

    step("Searching YouTube for high-view videos")
    videos = youtube_search.search_videos(category["query"])
    if not videos:
        print("\nNo videos matched your filters (try lowering MIN_VIEWS in .env).")
        return 1

    print(f"\nFound {len(videos)} source video(s):")
    for v in videos:
        print(f"   - {v.views:,} views | {v.duration_seconds}s | {v.title[:55]}")
    hr()

    all_records: list[dict] = []
    for video in videos:
        print(f"\nProcessing: {video.title[:60]}")
        try:
            all_records.extend(
                process_video(video, category_name, category["category_id"], work_dir, run_id)
            )
        except Exception as exc:
            step(f"Error processing this video, skipping: {exc}")
            traceback.print_exc()

    state.finish_run(run_id, produced=len(all_records))

    hr()
    print(f"\nDone. Produced {len(all_records)} Short(s) in '{config.output_dir}/'.")
    for r in all_records:
        line = f"   - {os.path.basename(r['file'])}: {r['title']}"
        if r["youtube_id"]:
            line += f"  ->  https://youtube.com/shorts/{r['youtube_id']}"
        print(line)

    if config.review_mode and all_records:
        print(
            f"\nReview & upload your Shorts:  python dashboard.py"
            f"  ->  http://localhost:{config.dashboard_port}"
        )

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)
