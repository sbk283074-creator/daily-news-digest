"""Pipeline entry point.

    collect feeds -> filter -> dedupe -> quota/rank -> translate -> write JSON

Run locally with:  python scripts/build.py
Run with no translation (fast sanity check):  python scripts/build.py --no-translate
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (  # noqa: E402
    CATEGORIES,
    FEEDS,
    PER_CATEGORY,
    PER_SOURCE_CAP,
    TRANSLATE_BUDGET_SECONDS,
)
from fetcher import collect, dedupe, filter_fresh, quota_and_rank  # noqa: E402
from translator import Translator, translate_articles  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "docs", "data")
ARCHIVE_DIR = os.path.join(DATA_DIR, "archive")
EDITIONS = 90  # how many daily archives to keep on disk
MIN_ARTICLES = 20  # below this we treat the run as broken and refuse to publish

CN_TZ = ZoneInfo("Asia/Shanghai")
_WEEKDAY_ZH = "一二三四五六日"


def edition_labels(day) -> tuple[str, str]:
    en = f"{day.strftime('%A')}, {day.day} {day.strftime('%B %Y')}"
    zh = f"{day.year}年{day.month}月{day.day}日 星期{_WEEKDAY_ZH[day.weekday()]}"
    return en, zh


def write_json(path: str, payload) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write("\n")


def prune_archive() -> None:
    if not os.path.isdir(ARCHIVE_DIR):
        return
    files = sorted(f for f in os.listdir(ARCHIVE_DIR) if f.endswith(".json"))
    for name in files[:-EDITIONS]:
        try:
            os.remove(os.path.join(ARCHIVE_DIR, name))
        except OSError:
            pass


def rebuild_index() -> list[str]:
    dates = sorted(
        (f[:-5] for f in os.listdir(ARCHIVE_DIR) if f.endswith(".json")),
        reverse=True,
    )
    write_json(os.path.join(DATA_DIR, "index.json"), {"schema": 1, "dates": dates})
    return dates


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Daily Brief dataset.")
    parser.add_argument("--no-translate", action="store_true", help="skip zh translation (fast)")
    parser.add_argument("--translate-budget", type=int, default=0, help="seconds allowed for translation")
    parser.add_argument("--per-category", type=int, default=PER_CATEGORY)
    parser.add_argument("--per-source-cap", type=int, default=PER_SOURCE_CAP)
    args = parser.parse_args()

    started = datetime.now(timezone.utc)
    print(f"[1/5] fetching {len(FEEDS)} feeds ...")

    articles, status = collect(FEEDS)
    ok = sum(1 for s in status if s["ok"])
    print(f"      {ok}/{len(status)} feeds ok, {len(articles)} raw items")
    for entry in status:
        if not entry["ok"]:
            print(f"      ! {entry['name']}: {entry['error']}")

    print("[2/5] filtering + deduping ...")
    articles = filter_fresh(articles)
    before = len(articles)
    articles = dedupe(articles)
    print(f"      {before} -> {len(articles)} after dedupe")

    print("[3/5] ranking with per-source quota ...")
    articles = quota_and_rank(articles, args.per_category, args.per_source_cap)
    print(f"      {len(articles)} selected")

    if len(articles) < MIN_ARTICLES:
        print(f"FATAL: only {len(articles)} articles (< {MIN_ARTICLES}). "
              "Refusing to overwrite the published edition.", file=sys.stderr)
        return 1

    print("[4/5] translating to zh-CN ...")
    translator = Translator(
        enabled=not args.no_translate,
        budget_seconds=args.translate_budget or TRANSLATE_BUDGET_SECONDS,
    )
    if translator.enabled and not translator.providers:
        print("      no translation provider configured - publishing English only")
    translate_articles(articles, translator)
    translator.save()
    stats = translator.stats
    print(f"      cache {stats['cache_entries']} entries | hits {stats['cache_hits']} | "
          f"new {stats['translated']} | failed {stats['failed']} | deferred {stats['deferred']}")
    zh_titles = sum(1 for a in articles if a.get("title_zh") and a["title_zh"] != a["title"])
    print(f"      {zh_titles}/{len(articles)} titles translated")

    now = datetime.now(timezone.utc)
    edition = now.astimezone(CN_TZ).date()
    label_en, label_zh = edition_labels(edition)

    counts: dict[str, int] = {}
    for art in articles:
        counts[art["category"]] = counts.get(art["category"], 0) + 1

    cleaned = []
    for art in articles:
        for key in [k for k in art if k.startswith("_")]:
            art.pop(key, None)
        cleaned.append(art)
    cleaned.sort(
        key=lambda a: (a["category"], a.get("published") or ""),
        reverse=False,
    )

    payload = {
        "schema": 1,
        "edition": edition.isoformat(),
        "edition_label_en": label_en,
        "edition_label_zh": label_zh,
        "generated_at": started.isoformat().replace("+00:00", "Z"),
        "categories": [
            {**cat, "count": counts.get(cat["id"], 0)} for cat in CATEGORIES
        ],
        "sources": [
            {"id": f["id"], "name": f["name"], "category": f["category"], "home": f["home"]}
            for f in FEEDS
        ],
        "feed_status": status,
        "stats": {
            "articles": len(cleaned),
            "feeds_ok": ok,
            "feeds_total": len(status),
            "translation": translator.stats,
            "build_seconds": round((datetime.now(timezone.utc) - started).total_seconds(), 1),
        },
        "articles": cleaned,
    }

    print("[5/5] writing JSON ...")
    write_json(os.path.join(DATA_DIR, "latest.json"), payload)
    write_json(os.path.join(ARCHIVE_DIR, f"{edition.isoformat()}.json"), payload)
    prune_archive()
    dates = rebuild_index()
    print(f"      latest.json + archive/{edition.isoformat()}.json ({len(dates)} editions)")

    print(
        f"\nOK  {len(cleaned)} articles | {ok}/{len(status)} feeds | "
        f"{payload['stats']['build_seconds']}s | edition {payload['edition']}"
    )
    for cat in payload["categories"]:
        print(f"    {cat['en']:<12} {cat['count']:>3}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
