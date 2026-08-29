#!/usr/bin/env python3
"""Local review dashboard for produced Shorts.

    python dashboard.py      # -> http://localhost:5000

Preview every generated Short, edit its title / description / tags, then
approve, reject, or upload it to YouTube — one click each. Nothing is uploaded
automatically: this is the human-in-the-loop gate that makes the whole system
safe to run on a schedule.

Reads and writes the same SQLite state DB that ``main.py`` populates.
"""

from __future__ import annotations

import os

from flask import Flask, Response, abort, redirect, render_template_string, request, send_file, url_for

from config import config
from pipeline import state, uploader

app = Flask(__name__)

STATUS_LABELS = {
    state.PENDING: "Pending review",
    state.APPROVED: "Approved",
    state.REJECTED: "Rejected",
    state.UPLOADED: "Uploaded",
    state.FAILED: "Upload failed",
    state.SAVED: "Saved locally",
}

# Order shown in the UI: things needing attention first.
STATUS_ORDER = [state.PENDING, state.APPROVED, state.FAILED, state.SAVED, state.UPLOADED, state.REJECTED]


PAGE = """
<!doctype html>
<html lang="en" class="dark">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Shorts Review</title>
  <style>
    :root {
      --bg: #0b0b0f; --panel: #16161d; --panel-2: #1e1e28;
      --border: #2a2a37; --text: #eaeaf2; --muted: #9a9aae;
      --accent: #6d5efc; --green: #22c55e; --red: #ef4444; --amber: #f59e0b; --blue: #3b82f6;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0; background: var(--bg); color: var(--text);
      font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
      line-height: 1.5;
    }
    header {
      position: sticky; top: 0; z-index: 10; background: rgba(11,11,15,.85);
      backdrop-filter: blur(8px); border-bottom: 1px solid var(--border);
      padding: 18px 24px; display: flex; align-items: center; gap: 16px; flex-wrap: wrap;
    }
    header h1 { font-size: 18px; margin: 0; font-weight: 650; letter-spacing: -.01em; }
    .pills { display: flex; gap: 8px; flex-wrap: wrap; margin-left: auto; }
    .pill {
      font-size: 12px; padding: 4px 10px; border-radius: 999px;
      background: var(--panel-2); border: 1px solid var(--border); color: var(--muted);
    }
    .pill b { color: var(--text); font-weight: 600; }
    main { max-width: 1100px; margin: 0 auto; padding: 24px; }
    .empty { color: var(--muted); text-align: center; padding: 80px 0; }
    .group-title {
      font-size: 13px; text-transform: uppercase; letter-spacing: .08em;
      color: var(--muted); margin: 28px 0 12px;
    }
    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 20px; }
    .card {
      background: var(--panel); border: 1px solid var(--border); border-radius: 14px;
      overflow: hidden; display: flex; flex-direction: column;
    }
    video { width: 100%; background: #000; aspect-ratio: 9/16; max-height: 460px; object-fit: contain; }
    .card-body { padding: 14px; display: flex; flex-direction: column; gap: 10px; }
    .status {
      align-self: flex-start; font-size: 11px; font-weight: 600; padding: 3px 9px;
      border-radius: 999px; text-transform: uppercase; letter-spacing: .04em;
    }
    .s-pending_review { background: rgba(245,158,11,.15); color: var(--amber); }
    .s-approved { background: rgba(59,130,246,.15); color: var(--blue); }
    .s-uploaded { background: rgba(34,197,94,.15); color: var(--green); }
    .s-rejected { background: rgba(239,68,68,.15); color: var(--red); }
    .s-upload_failed { background: rgba(239,68,68,.15); color: var(--red); }
    .s-saved { background: rgba(154,154,174,.15); color: var(--muted); }
    label { font-size: 11px; color: var(--muted); display: block; margin-bottom: 4px; }
    input, textarea {
      width: 100%; background: var(--panel-2); border: 1px solid var(--border);
      color: var(--text); border-radius: 8px; padding: 8px 10px; font: inherit; font-size: 13px;
      resize: vertical;
    }
    input:focus, textarea:focus { outline: none; border-color: var(--accent); }
    .src { font-size: 12px; color: var(--muted); }
    .src a { color: var(--muted); }
    .row { display: flex; gap: 8px; flex-wrap: wrap; }
    button {
      flex: 1; min-width: 90px; border: none; border-radius: 8px; padding: 9px 12px;
      font: inherit; font-size: 13px; font-weight: 600; cursor: pointer; color: #fff;
    }
    button:hover { filter: brightness(1.08); }
    .btn-save { background: var(--panel-2); border: 1px solid var(--border); color: var(--text); }
    .btn-approve { background: var(--blue); }
    .btn-upload { background: var(--green); }
    .btn-reject { background: transparent; border: 1px solid var(--red); color: var(--red); }
    .err { color: var(--red); font-size: 12px; }
    .yt { font-size: 12px; }
    .yt a { color: var(--green); }
    form.inline { display: contents; }
  </style>
</head>
<body>
  <header>
    <h1>Shorts Review</h1>
    <div class="pills">
      {% for st in status_order %}
        {% if counts.get(st) %}
          <span class="pill">{{ labels[st] }} <b>{{ counts[st] }}</b></span>
        {% endif %}
      {% endfor %}
    </div>
  </header>
  <main>
    {% if not shorts %}
      <div class="empty">No Shorts yet. Run <code>python main.py</code> to produce some.</div>
    {% endif %}

    {% for st in status_order %}
      {% set items = grouped.get(st) %}
      {% if items %}
        <div class="group-title">{{ labels[st] }} ({{ items|length }})</div>
        <div class="grid">
          {% for s in items %}
            <div class="card">
              <video controls preload="metadata" src="{{ url_for('video', short_id=s.id) }}"></video>
              <div class="card-body">
                <span class="status s-{{ s.status }}">{{ labels[s.status] }}</span>
                {% if s.youtube_id %}
                  <div class="yt">Live: <a href="https://youtube.com/shorts/{{ s.youtube_id }}" target="_blank" rel="noopener">youtube.com/shorts/{{ s.youtube_id }}</a></div>
                {% endif %}
                {% if s.error %}<div class="err">{{ s.error }}</div>{% endif %}

                <form class="inline" method="post" action="{{ url_for('save', short_id=s.id) }}">
                  <div>
                    <label>Title</label>
                    <input name="title" value="{{ s.title }}" maxlength="100">
                  </div>
                  <div>
                    <label>Description</label>
                    <textarea name="description" rows="3">{{ s.description }}</textarea>
                  </div>
                  <div>
                    <label>Tags (comma separated)</label>
                    <input name="tags" value="{{ s.tags|join(', ') }}">
                  </div>
                  <div class="src">
                    Source: <a href="{{ s.source_url }}" target="_blank" rel="noopener">{{ s.source_title[:48] }}</a>
                    &middot; {{ '%.0f'|format(s.seg_start) }}s–{{ '%.0f'|format(s.seg_end) }}s
                  </div>
                  <div class="row">
                    <button class="btn-save" type="submit">Save edits</button>
                  </div>
                </form>

                <div class="row">
                  {% if s.status != 'uploaded' %}
                    <form class="inline" method="post" action="{{ url_for('action', short_id=s.id, verb='upload') }}">
                      <button class="btn-upload" type="submit">Upload</button>
                    </form>
                    {% if s.status != 'approved' %}
                    <form class="inline" method="post" action="{{ url_for('action', short_id=s.id, verb='approve') }}">
                      <button class="btn-approve" type="submit">Approve</button>
                    </form>
                    {% endif %}
                    {% if s.status != 'rejected' %}
                    <form class="inline" method="post" action="{{ url_for('action', short_id=s.id, verb='reject') }}">
                      <button class="btn-reject" type="submit">Reject</button>
                    </form>
                    {% endif %}
                  {% endif %}
                </div>
              </div>
            </div>
          {% endfor %}
        </div>
      {% endif %}
    {% endfor %}
  </main>
</body>
</html>
"""


@app.route("/")
def index() -> str:
    shorts = state.list_shorts()
    grouped: dict[str, list[dict]] = {}
    for s in shorts:
        grouped.setdefault(s["status"], []).append(s)
    return render_template_string(
        PAGE,
        shorts=shorts,
        grouped=grouped,
        counts=state.counts_by_status(),
        labels=STATUS_LABELS,
        status_order=STATUS_ORDER,
    )


@app.route("/video/<int:short_id>")
def video(short_id: int) -> Response:
    s = state.get_short(short_id)
    if not s or not os.path.exists(s["file"]):
        abort(404)
    return send_file(os.path.abspath(s["file"]), mimetype="video/mp4", conditional=True)


@app.route("/save/<int:short_id>", methods=["POST"])
def save(short_id: int):
    tags = [t.strip() for t in request.form.get("tags", "").split(",") if t.strip()]
    state.update_short(
        short_id,
        title=request.form.get("title", "").strip(),
        description=request.form.get("description", "").strip(),
        tags=tags,
    )
    return redirect(url_for("index"))


@app.route("/action/<int:short_id>/<verb>", methods=["POST"])
def action(short_id: int, verb: str):
    s = state.get_short(short_id)
    if not s:
        abort(404)

    if verb == "approve":
        state.update_short(short_id, status=state.APPROVED, error=None)
    elif verb == "reject":
        state.update_short(short_id, status=state.REJECTED, error=None)
    elif verb == "upload":
        if not os.path.exists(s["file"]):
            state.update_short(short_id, status=state.FAILED, error="File not found on disk.")
            return redirect(url_for("index"))
        try:
            yt_id = uploader.upload_short(
                file_path=s["file"],
                title=s["title"],
                description=s["description"],
                tags=s["tags"],
                category_id=s["category_id"] or "27",
            )
            state.update_short(short_id, status=state.UPLOADED, youtube_id=yt_id, error=None)
        except Exception as exc:  # keep row, surface the error in the UI
            state.update_short(short_id, status=state.FAILED, error=str(exc)[:500])
    else:
        abort(400)

    return redirect(url_for("index"))


def main() -> None:
    state.init_db()
    port = config.dashboard_port
    print(f"\nShorts review dashboard -> http://localhost:{port}\n(Ctrl+C to stop)\n")
    app.run(host="127.0.0.1", port=port, debug=False)


if __name__ == "__main__":
    main()
