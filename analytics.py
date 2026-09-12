#!/usr/bin/env python3
"""Refresh performance metrics for uploaded Shorts and print a report.

    python analytics.py

Pulls view/like/comment counts for every uploaded Short via the YouTube API,
stores them in the state DB, and shows what's working so future runs of
`python main.py` can learn from it (LEARN=true).
"""

from __future__ import annotations

import sys

from pipeline import analytics, state


def main() -> int:
    state.init_db()
    try:
        analytics.refresh_metrics()
    except Exception as exc:  # network / auth / scope issues shouldn't crash the report
        print(f"Could not refresh metrics: {exc}")
        print(
            "If this is a scope error, delete your token file (default token.json) "
            "and re-run so the new read-only permission is granted."
        )

    analytics.performance_report()

    hint = analytics.performance_hint()
    if hint:
        print(f"\nLearning signal for future runs: {hint}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
