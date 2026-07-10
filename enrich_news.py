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


def call_claude(api_key, batch):
    payload = {
        "model": MODEL,
        "max_tokens": 8000,
        "system": SYSTEM,
        "output_config": {"format": {"type": "json_schema", "schema": SCHEMA}},
        "messages": [{
            "role": "user",
            "content": "Summarize each of these news items:\n\n" + json.dumps(
                [{"link": i["link"], "title": i["title"], "source": i["source"],
                  "blurb": i["summary"]} for i in batch],
                ensure_ascii=False,
            ),
        }],
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
            return json.loads(text)["items"]
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 529) and attempt < 3:
                wait = 15 * (attempt + 1)
                print(f"  HTTP {e.code}, retrying in {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            print(f"  !! HTTP {e.code}: {e.read()[:300]}", file=sys.stderr)
            return []
    return []


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
    if not todo:
        print("All items already enriched.")
        return
    print(f"Enriching {len(todo)} of {len(items)} items with {MODEL}...")

    by_link = {i["link"]: i for i in items}
    done = 0
    for start in range(0, len(todo), BATCH_SIZE):
        batch = todo[start:start + BATCH_SIZE]
        for res in call_claude(api_key, batch):
            it = by_link.get(res.get("link"))
            if it and res.get("summary"):
                it["aiSummary"] = res["summary"].strip()
                it["whyMatters"] = [w.strip() for w in res.get("why", []) if w.strip()][:3]
                done += 1
        print(f"  {min(start + BATCH_SIZE, len(todo))}/{len(todo)} processed")

    payload = "window.NEWS_DATA = " + json.dumps(data, ensure_ascii=False, indent=1) + ";\n"
    path.write_text(payload, encoding="utf-8")
    print(f"Wrote data.js: {done} items enriched.")


if __name__ == "__main__":
    main()
