"""SQLite state layer: dedup, run history, and resumable checkpoints.

A single local database (``config.state_db``, default ``output/state.db``) tracks:

* ``runs``   — one row per ``python main.py`` invocation (history + status).
* ``shorts`` — one row per produced Short, with its review status, metadata,
               source attribution, and (once uploaded) its YouTube id.

This is what makes repeated daily runs safe:

* **Dedup**      — we never build a Short from a source segment we've already
                   used (``segment_overlaps``), so re-running the same category
                   won't repost the same moment.
* **Resumable**  — if a run crashes, already-recorded Shorts stay in the DB and
                   their output files on disk, so the next run skips them.
* **Review**     — the dashboard reads/writes ``status`` here to approve,
                   reject, edit, and upload Shorts.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from config import config

# Short lifecycle states.
PENDING = "pending_review"   # produced, awaiting a human decision
APPROVED = "approved"        # human approved, queued for upload
REJECTED = "rejected"        # human rejected (ignored by dedup so it can be retried)
UPLOADED = "uploaded"        # live on YouTube
FAILED = "upload_failed"     # an upload attempt errored
SAVED = "saved"              # review mode off + auto-upload off: kept locally


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    os.makedirs(os.path.dirname(config.state_db) or ".", exist_ok=True)
    conn = sqlite3.connect(config.state_db)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """Create tables if they don't exist. Safe to call on every run."""
    with _conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                category     TEXT,
                query        TEXT,
                started_at   REAL,
                finished_at  REAL,
                produced     INTEGER DEFAULT 0,
                status       TEXT DEFAULT 'running'
            );

            CREATE TABLE IF NOT EXISTS shorts (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id           INTEGER,
                source_video_id  TEXT,
                source_url       TEXT,
                source_title     TEXT,
                seg_start        REAL,
                seg_end          REAL,
                file             TEXT,
                title            TEXT,
                description      TEXT,
                tags             TEXT,        -- JSON array
                hashtags         TEXT,        -- JSON array
                category_id      TEXT,
                hook             TEXT,
                status           TEXT DEFAULT 'pending_review',
                youtube_id       TEXT,
                error            TEXT,
                created_at       REAL
            );

            CREATE INDEX IF NOT EXISTS idx_shorts_video  ON shorts(source_video_id);
            CREATE INDEX IF NOT EXISTS idx_shorts_status ON shorts(status);
            """
        )


# --------------------------------------------------------------------------- #
#  Runs
# --------------------------------------------------------------------------- #
def start_run(category: str, query: str) -> int:
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO runs (category, query, started_at, status) VALUES (?, ?, ?, 'running')",
            (category, query, time.time()),
        )
        return int(cur.lastrowid)


def finish_run(run_id: int, produced: int, status: str = "done") -> None:
    with _conn() as c:
        c.execute(
            "UPDATE runs SET finished_at = ?, produced = ?, status = ? WHERE id = ?",
            (time.time(), produced, status, run_id),
        )


# --------------------------------------------------------------------------- #
#  Dedup
# --------------------------------------------------------------------------- #
def segment_overlaps(video_id: str, start: float, end: float) -> bool:
    """True if we've already produced a (non-rejected) Short overlapping this
    time window of this source video. Rejected Shorts don't block re-use."""
    with _conn() as c:
        rows = c.execute(
            "SELECT seg_start, seg_end FROM shorts "
            "WHERE source_video_id = ? AND status != ?",
            (video_id, REJECTED),
        ).fetchall()
    for r in rows:
        if not (end <= r["seg_start"] or start >= r["seg_end"]):
            return True
    return False


def video_used_count(video_id: str) -> int:
    with _conn() as c:
        row = c.execute(
            "SELECT COUNT(*) AS n FROM shorts WHERE source_video_id = ? AND status != ?",
            (video_id, REJECTED),
        ).fetchone()
    return int(row["n"])


# --------------------------------------------------------------------------- #
#  Shorts
# --------------------------------------------------------------------------- #
def record_short(
    *,
    run_id: int,
    source_video_id: str,
    source_url: str,
    source_title: str,
    seg_start: float,
    seg_end: float,
    file: str,
    title: str,
    description: str,
    tags: list[str],
    hashtags: list[str],
    category_id: str,
    hook: str,
    status: str,
    youtube_id: Optional[str] = None,
) -> int:
    with _conn() as c:
        cur = c.execute(
            """
            INSERT INTO shorts (
                run_id, source_video_id, source_url, source_title,
                seg_start, seg_end, file, title, description, tags, hashtags,
                category_id, hook, status, youtube_id, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                run_id, source_video_id, source_url, source_title,
                seg_start, seg_end, file, title, description,
                json.dumps(tags), json.dumps(hashtags),
                category_id, hook, status, youtube_id, time.time(),
            ),
        )
        return int(cur.lastrowid)


def update_short(short_id: int, **fields: Any) -> None:
    """Update arbitrary columns. ``tags``/``hashtags`` are JSON-encoded."""
    if not fields:
        return
    for key in ("tags", "hashtags"):
        if key in fields and not isinstance(fields[key], str):
            fields[key] = json.dumps(fields[key])
    cols = ", ".join(f"{k} = ?" for k in fields)
    with _conn() as c:
        c.execute(f"UPDATE shorts SET {cols} WHERE id = ?", (*fields.values(), short_id))


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    for key in ("tags", "hashtags"):
        try:
            d[key] = json.loads(d.get(key) or "[]")
        except (TypeError, ValueError):
            d[key] = []
    return d


def get_short(short_id: int) -> Optional[dict]:
    with _conn() as c:
        row = c.execute("SELECT * FROM shorts WHERE id = ?", (short_id,)).fetchone()
    return _row_to_dict(row) if row else None


def list_shorts(status: Optional[str] = None) -> list[dict]:
    with _conn() as c:
        if status:
            rows = c.execute(
                "SELECT * FROM shorts WHERE status = ? ORDER BY created_at DESC", (status,)
            ).fetchall()
        else:
            rows = c.execute("SELECT * FROM shorts ORDER BY created_at DESC").fetchall()
    return [_row_to_dict(r) for r in rows]


def counts_by_status() -> dict[str, int]:
    with _conn() as c:
        rows = c.execute(
            "SELECT status, COUNT(*) AS n FROM shorts GROUP BY status"
        ).fetchall()
    return {r["status"]: int(r["n"]) for r in rows}
