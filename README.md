# בִּינָה, Binah — AI News Daily

A self-refreshing dashboard of AI news with a dedicated **AI & Religion** spotlight,
built as a single static page — no build step, no framework, no server.

- **`index.html`** — the dashboard (light + dark themes follow the system setting)
- **`data.js`** — the baked news data (`window.NEWS_DATA`)
- **`fetch_news.py`** — pulls 18 RSS/Atom feeds and rewrites `data.js` (Python 3 stdlib only)
- **`.github/workflows/refresh.yml`** — refreshes `data.js` every 6 hours once the repo is on GitHub

## How it works

`fetch_news.py` pulls AI outlets (Verge, Ars, MIT Tech Review, Wired, 404 Media,
Simon Willison, OpenAI, DeepMind), AI podcasts (Hard Fork, The AI Daily Brief,
Latent Space, Practical AI), and religion/Jewish press (RNS, JTA,
eJewishPhilanthropy, The Forward, Sinai and Synapses, AI and Faith).

- Religion-press items are kept **only when they touch AI** (word-boundary keyword match).
- Every item gets a **religion-relevance score** (Jewish terms weighted highest);
  items scoring ≥3 appear in the gold **בִּינָה וֶאֱמוּנָה, Binah Ve'emunah** spotlight
  and the *AI & Religion* tab.
- Podcast episodes get an inline **▶ Listen here** player.

## Refresh locally

```bash
python3 fetch_news.py   # rewrites data.js; then just reload index.html
```

## Deploy (one-time)

1. Create a GitHub repo (e.g. `danmedwin/binah`).
2. `git remote add origin … && git push -u origin main`
3. Settings → Pages → deploy from `main` / root.
4. Settings → Actions → General → Workflow permissions → **Read and write** (lets the refresh Action commit `data.js`).

## Tuning

- Add/remove feeds in `FEEDS` at the top of `fetch_news.py`.
- Adjust religion keywords/weights in `RELIGION_TERMS`, AI keywords in `AI_RE`.
- Spotlight threshold: `SPOT_MIN` in `index.html` (default 3).

---
v1.0 · built overnight by Claude for Rabbi Dan Medwin
