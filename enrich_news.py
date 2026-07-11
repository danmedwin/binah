#!/usr/bin/env python3
"""Add AI summaries + "why this matters" bullets to items in data.js.

Stdlib only. Reads ANTHROPIC_API_KEY from the environment (locally: export it;
on GitHub: a repo Actions secret). If the key is absent the script exits
cleanly so the refresh pipeline still works — items just show their feed blurb.

Only items missing an aiSummary are sent (fetch_news.py carries prior
enrichment over by link), so a routine refresh summarizes ~a few dozen new
items, not the whole set.

Model: claude-haiku-4-5 — cheap and well-suited to short-blurb summarization;
switch MODEL to "claude-opus-4-8" for richer analysis at ~5x the cost.
"""

import json
import os
import re
import sys
import time
import urllib.error
from pathlib import Path
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-haiku-4-5"
BATCH_SIZE = 15

SYSTEM = (
    "You summarize AI-news items for a dashboard read by a tech-savvy rabbi and "
    "Jewish educator who follows both the AI industry and the intersection of AI "
    "with religion, ethics, and education. For each item you receive (title, "
    "source, feed blurb), write: (1) summary — 2-3 plain sentences saying what "
    "actually happened, no hype, no 'this article discusses'; (2) why — 2-3 short "
    "bullets on why it matters, concrete and specific (impact on the AI landscape, "
    "on educators/clergy/communities, on policy or practice). When an item touches "
    "religion or education, say so plainly in the bullets; otherwise do not force "
    "that angle. Base everything only on the provided text — never invent facts."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "link": {"type": "string"},
                    "summary": {"type": "string"},
                    "why": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["link", "summary", "why"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

BRIEF_SYSTEM = (
    "You are the front-page editor of an AI-news dashboard read by a tech-savvy "
    "rabbi and Jewish educator. From the items provided (all AI news of the last "
    "few days — industry, research, policy, culture; NOT limited to religion), "
    "pick the genuinely consequential developments and write the day's brief: "
    "4-5 'what you need to know' takeaways, ordered by importance. Each takeaway "
    "has: text — one plain sentence under ~35 words, concrete, no hype; group "
    "related items into one takeaway (e.g. several model launches = one "
    "frontier-model bullet); and links — the 1-3 links (verbatim from the "
    "provided list) of the stories that takeaway is drawn from. Base everything "
    "only on the provided text."
)

BRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "bullets": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "links": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "links"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["bullets"],
    "additionalProperties": False,
}


def call_claude(api_key, system, schema, user_content):
    payload = {
        "model": MODEL,
        "max_tokens": 8000,
        "system": system,
        "output_config": {"format": {"type": "json_schema", "schema": schema}},
        "messages": [{"role": "user", "content": user_content}],
    }
    req = Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )
    for attempt in range(4):
        try:
            with urlopen(req, timeout=300) as r:
                body = json.loads(r.read())
            text = next(b["text"] for b in body["content"] if b["type"] == "text")
            return json.loads(text)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 529) and attempt < 3:
                wait = 15 * (attempt + 1)
                print(f"  HTTP {e.code}, retrying in {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            print(f"  !! HTTP {e.code}: {e.read()[:300]}", file=sys.stderr)
            return None
    return None


def build_brief(api_key, items):
    """Regenerate the front-page Brief from the last ~72h of items."""
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
    recent = [i for i in items if i.get("date") and i["date"] >= cutoff]
    if len(recent) < 10:  # thin news window — use the newest 40 instead
        recent = sorted(items, key=lambda x: x.get("date") or "", reverse=True)[:40]
    payload = [{"link": i["link"], "title": i["title"], "source": i["source"],
                "blurb": (i.get("aiSummary") or i["summary"])[:250]} for i in recent]
    res = call_claude(
        api_key, BRIEF_SYSTEM, BRIEF_SCHEMA,
        "Write today's brief from these items:\n\n" + json.dumps(payload, ensure_ascii=False),
    )
    if not res or not res.get("bullets"):
        return None
    valid = {i["link"] for i in recent}
    bullets = []
    for b in res["bullets"]:
        text = (b.get("text") or "").strip()
        if not text:
            continue
        bullets.append({
            "text": text,
            "links": [l for l in b.get("links", []) if l in valid][:3],
        })
    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "bullets": bullets[:5],
    }


def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("ANTHROPIC_API_KEY not set — skipping enrichment (items keep feed blurbs).")
        return

    path = HERE / "data.js"
    raw = path.read_text(encoding="utf-8")
    data = json.loads(re.sub(r"^window\.NEWS_DATA = |;\s*$", "", raw.strip()))
    items = data["items"]
    todo = [i for i in items if not i.get("aiSummary")]
    by_link = {i["link"]: i for i in items}
    done = 0
    if todo:
        print(f"Enriching {len(todo)} of {len(items)} items with {MODEL}...")
        for start in range(0, len(todo), BATCH_SIZE):
            batch = todo[start:start + BATCH_SIZE]
            user = "Summarize each of these news items:\n\n" + json.dumps(
                [{"link": i["link"], "title": i["title"], "source": i["source"],
                  "blurb": i["summary"]} for i in batch],
                ensure_ascii=False,
            )
            res = call_claude(api_key, SYSTEM, SCHEMA, user)
            for row in (res or {}).get("items", []):
                it = by_link.get(row.get("link"))
                if it and row.get("summary"):
                    it["aiSummary"] = row["summary"].strip()
                    it["whyMatters"] = [w.strip() for w in row.get("why", []) if w.strip()][:3]
                    done += 1
            print(f"  {min(start + BATCH_SIZE, len(todo))}/{len(todo)} processed")
    else:
        print("All items already enriched.")

    brief = build_brief(api_key, items)
    if brief:
        data["highlights"] = brief
        n_links = sum(len(b["links"]) for b in brief["bullets"])
        print(f"Brief regenerated: {len(brief['bullets'])} takeaways, {n_links} story links.")
    else:
        print("Brief unchanged (generation failed or returned empty).")

    payload = "window.NEWS_DATA = " + json.dumps(data, ensure_ascii=False, indent=1) + ";\n"
    path.write_text(payload, encoding="utf-8")
    print(f"Wrote data.js: {done} items enriched.")


if __name__ == "__main__":
    main()
