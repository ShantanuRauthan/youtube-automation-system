"""Analytics feedback loop.

Pulls real performance (views/likes/comments) for uploaded Shorts via the
YouTube API, stores it in the state DB, and turns it into signals that bias
future runs toward what actually works.

Run it after your Shorts have gathered some views:

    python analytics.py
"""

from __future__ import annotations

from collections import defaultdict

from pipeline import state, uploader


def refresh_metrics() -> int:
    """Fetch current stats for every uploaded Short and store them. Returns count."""
    shorts = [s for s in state.list_shorts() if s.get("youtube_id")]
    if not shorts:
        print("No uploaded Shorts to refresh yet.")
        return 0

    id_to_short = {s["youtube_id"]: s for s in shorts}
    stats = uploader.fetch_stats(list(id_to_short.keys()))

    n = 0
    for yt_id, st in stats.items():
        short = id_to_short.get(yt_id)
        if not short:
            continue
        state.record_metrics(
            short_id=short["id"],
            youtube_id=yt_id,
            views=int(st.get("viewCount", 0) or 0),
            likes=int(st.get("likeCount", 0) or 0),
            comments=int(st.get("commentCount", 0) or 0),
        )
        n += 1

    print(f"Refreshed metrics for {n} uploaded Short(s).")
    return n


def performance_report() -> None:
    """Print the top performers and average views per category."""
    rows = state.performance_rows()
    if not rows:
        print("No metrics yet. Upload Shorts, let them gather views, then re-run.")
        return

    print("\n=== Top performing Shorts ===")
    for r in rows[:10]:
        print(
            f"  {r['views']:>9,} views | {r['likes']:>6,} likes | "
            f"{r['duration']:.0f}s | {(r['category'] or '-'):<12} | {r['title'][:45]}"
        )

    cat: dict[str, list[int]] = defaultdict(list)
    for r in rows:
        if r["category"]:
            cat[r["category"]].append(r["views"])
    if cat:
        print("\n=== Average views by category ===")
        for c, vs in sorted(cat.items(), key=lambda kv: -(sum(kv[1]) / len(kv[1]))):
            print(f"  {c:<14} {sum(vs) // len(vs):>9,} avg  ({len(vs)} shorts)")


def performance_hint() -> str:
    """A short natural-language signal fed into the analyzer's prompt."""
    return state.performance_hint()
