#!/usr/bin/env python3
"""Fetch RSS/Atom feeds for the AI News Dashboard and bake data.js.

Stdlib only — no pip installs. Run:  python3 fetch_news.py
Writes data.js (window.NEWS_DATA = {...}) next to index.html.
"""

import html
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent

# category: 'ai' = AI outlet (kept always), 'religion' = religion outlet
# (kept only when the item mentions AI), 'podcast' = audio show (kept always,
# marked as listenable).
FEEDS = [
    # --- AI news & labs ---
    {"name": "The Verge · AI", "url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", "category": "ai"},
    {"name": "Ars Technica · AI", "url": "https://arstechnica.com/ai/feed/", "category": "ai"},
    {"name": "MIT Tech Review · AI", "url": "https://www.technologyreview.com/topic/artificial-intelligence/feed", "category": "ai"},
    {"name": "Wired · AI", "url": "https://www.wired.com/feed/tag/ai/latest/rss", "category": "ai"},
    {"name": "404 Media", "url": "https://www.404media.co/rss/", "category": "ai", "require_ai": True},
    {"name": "Simon Willison", "url": "https://simonwillison.net/atom/everything/", "category": "ai"},
    {"name": "OpenAI News", "url": "https://openai.com/news/rss.xml", "category": "ai"},
    {"name": "Google DeepMind", "url": "https://deepmind.google/blog/rss.xml", "category": "ai"},
    # --- Podcasts ---
    {"name": "Hard Fork", "url": "https://feeds.simplecast.com/l2i9YnTd", "category": "podcast"},
    {"name": "The AI Daily Brief", "url": "https://anchor.fm/s/f7cac464/podcast/rss", "category": "podcast"},
    {"name": "Latent Space", "url": "https://www.latent.space/feed", "category": "podcast"},
    {"name": "Practical AI", "url": "https://changelog.com/practicalai/feed", "category": "podcast"},
    # --- Religion & Jewish press (kept only when the item touches AI/tech) ---
    {"name": "Religion News Service", "url": "https://religionnews.com/feed/", "category": "religion"},
    {"name": "JTA", "url": "https://www.jta.org/feed", "category": "religion"},
    {"name": "eJewishPhilanthropy", "url": "https://ejewishphilanthropy.com/feed/", "category": "religion"},
    {"name": "The Forward", "url": "https://forward.com/feed/", "category": "religion"},
    {"name": "Sinai and Synapses", "url": "https://sinaiandsynapses.org/feed/", "category": "religion"},
    # AI and Faith is wholly AI-focused, so its items skip the AI-mention filter.
    {"name": "AI and Faith", "url": "https://aiandfaith.org/feed/", "category": "religion", "always_religion_hit": True, "skip_ai_filter": True},
]

MAX_PER_FEED = 25
MAX_AGE_DAYS = 45

# Word-boundary regex: short tokens like "agi"/"ai" must not match inside
# ordinary words ("fragile", "said").
AI_RE = re.compile(
    r"\b(artificial intelligence|a\.?i\.|ai|chatgpt|claude|gemini|openai|"
    r"anthropic|llms?|large language models?|machine learning|chatbots?|"
    r"gpts?|gpt-[\w.]+|deep learning|neural net\w*|generative|copilot|"
    r"midjourney|deepfakes?|algorithms?|automation|robots?|robotics|"
    r"agentic|superintelligence|agi)\b",
    re.IGNORECASE,
)

# Weighted religion terms. Jewish terms weighted highest (Dan's world),
# general religion next, adjacent-spiritual last. Title hits count double.
RELIGION_TERMS = {
    3: ["rabbi", "torah", "jewish", "judaism", "halach", "halakh", "synagogue",
        "talmud", "mitzv", "shabbat", "kosher", "yeshiva", "siddur", "liturg",
        "tefillah", "t'filah", "midrash", "hebrew"],
    2: ["religio", "faith", "theolog", "clergy", "church", "pastor", "priest",
        "sermon", "worship", "bible", "biblical", "scripture", "quran", "islam",
        "muslim", "christian", "catholic", "buddhis", "hindu", "interfaith",
        "congregation", "seminar", "chaplain", "prayer", "sacred", "divine"],
    1: ["god", "soul", "spiritual", "ethic", "moral", "meaning of life",
        "afterlife", "ritual", "belief"],
}


def fetch(url):
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (AI News Dashboard; personal use)"})
    with urlopen(req, timeout=30) as r:
        return r.read()


def text_of(el):
    if el is None:
        return ""
    return "".join(el.itertext()).strip()


def strip_html(s):
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    # WordPress feed boilerplate: "... Read More The post X appeared first on Y."
    s = re.sub(r"\s*Read More\b.*$", "", s)
    s = re.sub(r"\s*The post .{0,120} appeared first on .{0,60}$", "", s)
    return s.strip()


def parse_date(s):
    if not s:
        return None
    try:
        return parsedate_to_datetime(s)
    except (TypeError, ValueError):
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            dt = datetime.strptime(s.strip(), fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def localname(tag):
    return tag.rsplit("}", 1)[-1]


IMG_SRC_RE = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)


def extract_image(el):
    """Pull an image URL from media:content / media:thumbnail / enclosure / itunes:image."""
    n = localname(el.tag)
    if n in ("content", "thumbnail") and "media" in el.tag:
        url = el.get("url", "")
        mtype = el.get("type", "") or el.get("medium", "")
        if url and ("image" in mtype or n == "thumbnail" or not mtype):
            return url
    elif n == "enclosure" and "image" in (el.get("type") or ""):
        return el.get("url", "")
    elif n == "image" and "itunes" in el.tag:
        return el.get("href", "")
    return ""


def parse_feed(raw):
    """Return list of dicts from RSS 2.0 or Atom bytes."""
    root = ET.fromstring(raw)
    items = []
    if localname(root.tag) == "feed":  # Atom
        for entry in root:
            if localname(entry.tag) != "entry":
                continue
            it = {"title": "", "link": "", "date": None, "summary": "", "image": "", "raw_html": ""}
            for child in entry:
                n = localname(child.tag)
                if n == "title":
                    it["title"] = text_of(child)
                elif n == "link" and "media" not in child.tag:
                    rel = child.get("rel", "alternate")
                    if rel == "alternate" or not it["link"]:
                        it["link"] = child.get("href", "")
                elif n in ("published", "updated") and not it["date"]:
                    it["date"] = parse_date(text_of(child))
                elif n in ("summary", "content") and not it["summary"]:
                    it["summary"] = text_of(child)
                    it["raw_html"] = it["summary"]
                elif not it["image"]:
                    it["image"] = extract_image(child)
                # media:group nests media:content/thumbnail one level down
                if not it["image"] and localname(child.tag) == "group":
                    for sub in child:
                        it["image"] = extract_image(sub)
                        if it["image"]:
                            break
            items.append(it)
    else:  # RSS
        for item in root.iter():
            if localname(item.tag) != "item":
                continue
            it = {"title": "", "link": "", "date": None, "summary": "", "audio": "", "image": "", "raw_html": ""}
            for child in item:
                n = localname(child.tag)
                if n == "title":
                    it["title"] = text_of(child)
                elif n == "link" and "media" not in child.tag and not it["link"]:
                    it["link"] = text_of(child) or child.get("href", "")
                elif n == "pubDate":
                    it["date"] = parse_date(text_of(child))
                elif n in ("description", "summary") and not it["summary"]:
                    it["summary"] = text_of(child)
                    it["raw_html"] = it["summary"]
                elif n == "encoded":
                    it["raw_html"] = it["raw_html"] or text_of(child)
                elif n == "enclosure" and "audio" in (child.get("type") or ""):
                    it["audio"] = child.get("url", "")
                if not it["image"]:
                    it["image"] = extract_image(child)
                    if not it["image"] and localname(child.tag) == "group":
                        for sub in child:
                            it["image"] = extract_image(sub)
                            if it["image"]:
                                break
            # Fallback: first <img> in the description/content HTML
            if not it["image"] and it["raw_html"]:
                m = IMG_SRC_RE.search(it["raw_html"])
                if m and m.group(1).startswith("http"):
                    it["image"] = html.unescape(m.group(1))
            items.append(it)
    return items


def score_religion(text_lower, title_lower):
    score = 0
    hits = []
    for weight, terms in RELIGION_TERMS.items():
        for t in terms:
            in_title = t in title_lower
            in_body = t in text_lower
            if in_title or in_body:
                score += weight * (2 if in_title else 1)
                hits.append(t)
    return score, hits


def mentions_ai(text_lower):
    return AI_RE.search(text_lower) is not None


def process_feed(feed):
    try:
        raw = fetch(feed["url"])
        parsed = parse_feed(raw)
    except Exception as e:  # noqa: BLE001 — a dead feed must not sink the run
        print(f"  !! {feed['name']}: {e}", file=sys.stderr)
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)
    out = []
    for it in parsed[: MAX_PER_FEED * 2]:
        if not it["title"] or not it["link"]:
            continue
        if it["date"] and it["date"] < cutoff:
            continue
        # Titles can carry double-encoded entities (&#8217;) and stray tags.
        title = strip_html(it["title"])
        if not it.get("image") and it.get("raw_html"):
            m = IMG_SRC_RE.search(it["raw_html"])
            if m and m.group(1).startswith("http"):
                it["image"] = html.unescape(m.group(1))
        summary = strip_html(it["summary"])[:400]
        blob = (title + " " + summary).lower()
        title_lower = title.lower()
        has_ai = mentions_ai(blob)
        if feed["category"] == "religion" and not has_ai and not feed.get("skip_ai_filter"):
            continue  # religion outlets: only their AI/tech coverage
        if feed.get("require_ai") and not has_ai:
            continue
        r_score, r_hits = score_religion(blob, title_lower)
        if feed.get("always_religion_hit"):
            r_score = max(r_score, 5)
        out.append({
            "title": title,
            "link": it["link"],
            "source": feed["name"],
            "category": feed["category"],
            "date": it["date"].astimezone(timezone.utc).isoformat() if it["date"] else None,
            "summary": summary,
            "religionScore": r_score,
            "religionHits": sorted(set(r_hits))[:6],
            "audio": it.get("audio", ""),
            "image": it.get("image", ""),
        })
        if len(out) >= MAX_PER_FEED:
            break
    print(f"  ok {feed['name']}: {len(out)} items", file=sys.stderr)
    return out


def load_previous_enrichment():
    """Map link -> {aiSummary, whyMatters} from the existing data.js, so a
    refresh doesn't drop (or re-pay for) summaries already generated."""
    path = HERE / "data.js"
    if not path.exists():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(re.sub(r"^window\.NEWS_DATA = |;\s*$", "", raw.strip()))
        return {
            i["link"]: {"aiSummary": i["aiSummary"], "whyMatters": i["whyMatters"]}
            for i in data.get("items", [])
            if i.get("aiSummary")
        }
    except Exception:
        return {}


def main():
    previous = load_previous_enrichment()
    all_items = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(process_feed, f) for f in FEEDS]
        for fut in as_completed(futures):
            all_items.extend(fut.result())

    # Dedup by normalized link, then by normalized title.
    seen_links, seen_titles, deduped = set(), set(), []
    for it in sorted(all_items, key=lambda x: x["date"] or "", reverse=True):
        link_key = re.sub(r"[?#].*$", "", it["link"]).rstrip("/")
        title_key = re.sub(r"\W+", "", it["title"].lower())
        if link_key in seen_links or title_key in seen_titles:
            continue
        seen_links.add(link_key)
        seen_titles.add(title_key)
        prev = previous.get(it["link"], {})
        it["aiSummary"] = prev.get("aiSummary", "")
        it["whyMatters"] = prev.get("whyMatters", [])
        deduped.append(it)

    data = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "feedCount": len(FEEDS),
        "items": deduped,
    }
    payload = "window.NEWS_DATA = " + json.dumps(data, ensure_ascii=False, indent=1) + ";\n"
    (HERE / "data.js").write_text(payload, encoding="utf-8")
    spotlight = sum(1 for i in deduped if i["religionScore"] >= 3)
    print(f"Wrote data.js: {len(deduped)} items, {spotlight} AI-and-religion spotlight candidates.")


if __name__ == "__main__":
    main()
