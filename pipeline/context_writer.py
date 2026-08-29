"""AI-written title, description, tags, and hashtags for the finished Short."""

from __future__ import annotations

from dataclasses import dataclass, field

from pipeline.ai import generate_json


@dataclass
class ShortMetadata:
    title: str
    description: str
    tags: list[str] = field(default_factory=list)
    hashtags: list[str] = field(default_factory=list)


def write_context(
    category: str,
    source_title: str,
    hook: str,
    segment_reason: str,
    transcript_snippet: str,
) -> ShortMetadata:
    prompt = f"""You are a YouTube Shorts growth expert. Write publish-ready metadata for a
vertical Short in the "{category}" category.

The clip was cut from the video "{source_title}".
Editor's hook: "{hook}"
Why this moment was chosen: "{segment_reason}"
Clip transcript:
\"\"\"{transcript_snippet[:1500]}\"\"\"

Return JSON:
{{
  "title": "<=90 char scroll-stopping title, no clickbait lies>",
  "description": "2-4 sentence description that adds context and a call to action",
  "tags": ["8-12 relevant search tags"],
  "hashtags": ["3-6 hashtags WITHOUT the # symbol, e.g. shorts, {category.lower()}"]
}}
"""

    data = generate_json(prompt)
    if not isinstance(data, dict):
        data = {}

    hashtags = [h.lstrip("#") for h in data.get("hashtags", []) if h]
    if "shorts" not in [h.lower() for h in hashtags]:
        hashtags.insert(0, "shorts")

    title = (data.get("title") or f"{category}: {hook or source_title}")[:100]
    description = data.get("description") or f"An interesting moment from '{source_title}'."
    # Append hashtags to description so they register on the Short.
    description = f"{description}\n\n" + " ".join(f"#{h}" for h in hashtags)

    return ShortMetadata(
        title=title,
        description=description,
        tags=[str(t) for t in data.get("tags", [])][:15],
        hashtags=hashtags,
    )
