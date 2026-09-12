"""Discovery + Topic Scoring.

Finds candidate topics that are in DEMAND but not OVERSATURATED, so the pipeline
can auto-pick *what* to make instead of you guessing a category by hand.

Two signals per candidate topic:
  * demand      — Google Trends interest (pytrends) when available, else how
                  often the topic shows up in YouTube's trending feed.
  * competition — how many videos already target it (search totalResults).

Opportunity score:

    score = demand / log10(max(competition, 10))

so a topic people want but few have covered ranks highest.

pytrends is an UNOFFICIAL Google Trends scraper and will rate-limit or break
when Google changes things, so every Trends call degrades gracefully to
YouTube-only signals — a Trends hiccup can never stop a run.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from googleapiclient.discovery import build

from config import CATEGORIES, config


@dataclass
class TopicIdea:
    label: str
    query: str
    category_id: str
    demand: float
    competition: int
    score: float
    source: str  # "google-trends" or "youtube-trending"


# Common words to ignore when mining topic terms from trending titles.
_STOPWORDS = {
    "the", "and", "for", "with", "you", "your", "this", "that", "from", "have",
    "what", "when", "will", "just", "official", "video", "full", "live", "new",
    "best", "how", "why", "get", "top", "vs", "feat", "ft", "part", "episode",
    "shorts", "short", "trailer", "song", "songs", "music", "audio", "lyrics",
}


def _youtube():
    return build("youtube", "v3", developerKey=config.youtube_api_key, cache_discovery=False)


def _trending_terms(youtube, region: str, pool: int) -> Counter:
    """Mine candidate topic terms (tags + title words) from YouTube trending."""
    counter: Counter = Counter()
    try:
        resp = (
            youtube.videos()
            .list(part="snippet", chart="mostPopular", regionCode=region, maxResults=min(pool, 50))
            .execute()
        )
    except Exception:
        return counter

    for item in resp.get("items", []):
        snippet = item.get("snippet", {})
        for tag in snippet.get("tags", []) or []:
            t = tag.strip().lower()
            if 3 <= len(t) <= 40 and not t.isdigit() and t not in _STOPWORDS:
                counter[t] += 1
        for word in re.findall(r"[a-zA-Z]{4,}", snippet.get("title", "")):
            wl = word.lower()
            if wl not in _STOPWORDS:
                counter[wl] += 1
    return counter


def _competition(youtube, query: str) -> int:
    """Approximate how saturated a topic is via search total results."""
    try:
        resp = youtube.search().list(q=query, part="id", type="video", maxResults=1).execute()
        return int(resp.get("pageInfo", {}).get("totalResults", 0))
    except Exception:
        return 0


def _trends_demand(terms: list[str]) -> dict[str, float]:
    """Return {term: mean interest} from Google Trends. {} on any failure.

    pytrends is optional and flaky; if it's missing or errors we return what we
    have so far and callers fall back to YouTube-trending frequency instead.
    """
    if not config.discovery_use_trends:
        return {}
    try:
        from pytrends.request import TrendReq
    except Exception:
        return {}

    out: dict[str, float] = {}
    try:
        py = TrendReq(hl="en-US", tz=0)
        # Google Trends allows up to 5 terms per payload.
        for i in range(0, len(terms), 5):
            batch = terms[i : i + 5]
            try:
                py.build_payload(batch, timeframe="now 7-d")
                df = py.interest_over_time()
            except Exception:
                continue
            if df is None or df.empty:
                continue
            for t in batch:
                if t in df.columns:
                    out[t] = float(df[t].mean())
    except Exception:
        return out
    return out


def discover(limit: int = 8) -> list[TopicIdea]:
    """Return topic ideas ranked by opportunity (demand vs. competition)."""
    youtube = _youtube()

    # Candidate pool = the built-in category queries + mined trending terms.
    candidates: dict[str, tuple[str, str]] = {}  # label -> (search query, category_id)
    for name, meta in CATEGORIES.items():
        candidates[name.lower()] = (meta["query"], meta["category_id"])

    tags = _trending_terms(youtube, config.discovery_region, config.discovery_pool)
    for term, _freq in tags.most_common(20):
        candidates.setdefault(term, (f"{term} explained", "27"))

    labels = list(candidates.keys())
    trends = _trends_demand(labels)

    ideas: list[TopicIdea] = []
    for label in labels:
        query, category_id = candidates[label]
        comp = _competition(youtube, query)
        if trends:
            demand = trends.get(label, 0.0)
            source = "google-trends"
        else:
            # Fallback: how prominent the term was in the trending feed.
            demand = float(tags.get(label, 1))
            source = "youtube-trending"
        score = demand / math.log10(max(comp, 10))
        ideas.append(
            TopicIdea(
                label=label.title(),
                query=query,
                category_id=category_id,
                demand=round(demand, 2),
                competition=comp,
                score=round(score, 4),
                source=source,
            )
        )

    ideas.sort(key=lambda i: i.score, reverse=True)
    return ideas[:limit]
