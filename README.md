# The Daily Brief · 每日简报

A self-hosted news digest that refreshes itself every morning at **08:00 Beijing time** and
publishes straight to GitHub Pages. No server, no database, no hosting bill.

It pulls from **18 hand-picked outlets**, ranks the stories, translates the headlines into
Chinese, and commits the result back to the repository — which is also what triggers the
site to redeploy.

```
                 ┌──────────────────────────────────────────────┐
  every day      │  GitHub Actions  (cron 00:00 UTC / 08:00 CN)  │
  08:00 CST      └───────────────────────┬──────────────────────┘
                                         │
              ┌──────────────────────────┼──────────────────────────┐
              ▼                          ▼                          ▼
     ┌────────────────┐        ┌──────────────────┐        ┌────────────────┐
     │ fetch 18 feeds │        │  rank + dedupe   │        │ translate zh   │
     │ RSS / Atom /   │  ───▶  │  per-source cap  │  ───▶  │ (cached on     │
     │ RDF, concurrent│        │  72h freshness   │        │  disk, once)   │
     └────────────────┘        └──────────────────┘        └───────┬────────┘
                                                                   │
              ┌────────────────────────────────────────────────────┘
              ▼
     docs/data/latest.json  +  docs/data/archive/YYYY-MM-DD.json
              │
              ├──▶  git commit & push   (edition history lives in the repo)
              └──▶  actions/deploy-pages  (static site goes live)
```

---

## Sources

| Category | Outlets |
|---|---|
| **World** 国际头条 | BBC World · The Guardian · NPR · Al Jazeera |
| **Technology** 科技 | Ars Technica · The Verge · WIRED · Hacker News |
| **Business** 商业财经 | CNBC · MarketWatch · Guardian Business · The New York Times |
| **Science** 科学 | ScienceDaily · NASA · Nature · Phys.org · Science News · New Scientist |

18 feeds in total. Every headline links straight back to the publisher's own article. Nothing
is re-hosted, and all rights stay with the original outlets.

---

## Local development

The pipeline is **standard library only** — there is nothing to `pip install`.

```bash
# 1. build an edition
python3 scripts/build.py                  # full build, with Chinese translation
python3 scripts/build.py --no-translate   # faster, English only
python3 scripts/build.py --per-category 15

# 2. preview the site
cd docs && python3 -m http.server 8000
# open http://localhost:8000
```

> Open the site through a local HTTP server, not `file://` — the page fetches
> `data/latest.json` and browsers block that on the `file:` protocol.

### Output layout

```
docs/
├── index.html                 the site shell
├── assets/style.css           theme + layout (light & dark)
├── assets/app.js              rendering, filters, i18n
└── data/
    ├── latest.json            today's edition (what the page loads)
    ├── index.json             list of available editions
    └── archive/
        └── 2026-09-14.json    one immutable file per day (last 90 kept)

scripts/
├── config.py                  ← edit this to change sources or tuning
├── fetcher.py                 HTTP, RSS/Atom/RDF parsing, ranking
├── translator.py              translation providers + persistent cache
├── build.py                   pipeline entry point
└── .cache/translations.json   committed cache: each string translated once
```

---

## Deploying it to your own GitHub account

### 1. Create the repository and push

```bash
cd daily-news-digest
git init -b main
git add .
git commit -m "feat: daily news digest"
git remote add origin https://github.com/<your-user>/daily-news-digest.git
git push -u origin main
```

### 2. Turn on Pages — **important**

Go to **Settings → Pages → Build and deployment → Source** and choose **GitHub Actions**.

Do *not* choose "Deploy from a branch". The daily job commits with the built-in
`GITHUB_TOKEN`, and GitHub deliberately suppresses workflow runs triggered by that token —
so a branch-based Pages build would never fire and the site would go stale. The workflow here
deploys explicitly with `actions/deploy-pages`, which always runs.

### 3. Run it once by hand

**Actions → Daily news update → Run workflow.** First run takes about a minute; after that
the schedule takes over at 08:00 Beijing time every day.

Your site will be at `https://<your-user>.github.io/daily-news-digest/`.

---

## Configuration

Everything lives in `scripts/config.py`.

```python
PER_CATEGORY   = 30   # stories kept per category
PER_SOURCE_CAP = 9    # max from any one outlet, per category
MAX_AGE_HOURS  = 72   # freshness window

FEEDS = [
    {
        "id": "bbc-world",           # stable key, used for ids and filtering
        "name": "BBC World",         # shown on the card
        "home": "https://www.bbc.com/news/world",
        "category": "world",         # world | tech | business | science
        "url": "https://feeds.bbci.co.uk/news/world/rss.xml",
        # optional, tried in order when `url` errors or returns a non-feed body:
        # "fallbacks": ["https://example.com/other-endpoint.rss"],
        # optional, per-feed override of MAX_AGE_HOURS:
        # "max_age_hours": 24 * 14,
    },
    ...
]
```

The ranking is round-robin across outlets, so no single publisher can flood a category —
which is exactly what a naive "sort by date" would do to you.

To add a category, add an entry to `CATEGORIES` and point feeds at its `id`. The frontend
builds its tabs, colours and filters from the generated JSON, so nothing else needs editing.

---

## Translation (optional but recommended)

Chinese headlines are machine translated in the build, and results are cached in
`scripts/.cache/translations.json` — a given string is translated exactly once, ever. A warm
run adds ~5 seconds; a cold run translating ~240 new strings takes under a minute.

The provider chain, first available wins:

| Provider | Key needed | Notes |
|---|---|---|
| DeepL | `DEEPL_API_KEY` | 500k chars/month free, best quality |
| Google Cloud Translation | `GOOGLE_TRANSLATE_API_KEY` | reliable, paid |
| Google web endpoint | none | rate limited, but works for a daily run |
| MyMemory | none | small daily quota, last resort |

**No key is required.** The free endpoints handle a once-a-day workload comfortably. If every
provider is unavailable the build still succeeds and the Chinese view falls back to English —
it degrades, it never breaks.

To use a key, add it under **Settings → Secrets and variables → Actions**, and the workflow
picks it up automatically.

> Chinese titles and summaries are machine translated for convenience. The site carries a
> disclaimer, and every card links to the original English article.

---

## Frontend features

- **Bilingual UI** — EN / 中文 toggle; affects labels, categories, relative times and article text. Choice is remembered.
- **Light & dark theme**, following your system preference by default.
- **Category tabs** with live counts.
- **Search** across headlines, summaries and sources in both languages (press `/` to focus).
- **Source and edition filters** — every past day is browsable from the archive dropdown.
- **Top stories** — the most recent item from each category, so the hero stays diverse.
- **Progressive rendering** — 24 cards at a time, images lazy-loaded.
- **Zero external requests** — no CDN, no fonts, no analytics.

---

## Notes on reliability

A few decisions that look fussy but matter:

- **Per-feed isolation.** One dead feed must never take down the run. Failures are recorded in `feed_status` inside the JSON and shown in the footer as `18/18 feeds responded`, with the per-endpoint reason when something fails.
- **Refuses to publish a broken edition.** If fewer than 20 stories survive filtering, the build exits non-zero and the previous edition stays live.
- **Built for a datacentre IP.** GitHub runners share an IP pool that publishers rate limit aggressively: NASA answers `429`, and Nature has been seen answering `200` with an HTML challenge page. So `http_get` backs off between attempts (0.8s → 1.6s → 3.2s, with jitter), honours `Retry-After`, and treats an HTML body as a failed fetch rather than as a feed that happened to parse to nothing. A detected wall switches the retry to browser-navigation headers, because such walls routinely pass a top-level navigation while blocking anything that announces itself as a feed reader. `fallbacks` then gives each outlet another endpoint to try. Running this from a home connection hides all of that; running it from CI does not.
- **Redundant sources per category.** No category depends on one feed, so a blocked publisher costs a few cards rather than a whole section.
- **Translation circuit breaker.** The first HTTP 429 retires that provider for the run instead of triggering a retry storm — the difference between a 40-second build and a hung one. Deferred strings stay English and are translated on the next run; the cache is committed, so nothing is ever translated twice.
- **Tracking parameters are stripped** before an article URL is stored or hashed, so the same story from two pulls deduplicates correctly.

---

## License

The pipeline and site code are yours to use freely. The news content is not — it belongs to
the publishers listed above, and every card links back to them.
