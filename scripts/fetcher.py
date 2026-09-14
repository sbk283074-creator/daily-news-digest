"""Feed fetching, parsing and normalisation.

Standard library only - no requirements.txt, nothing to install, which keeps
the GitHub Actions job to a few seconds.
"""

from __future__ import annotations

import gzip
import hashlib
import html
import io
import random
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from config import (
    FETCH_RETRIES as FEED_RETRIES,
    FEED_WORKERS,
    FETCH_TIMEOUT,
    MAX_AGE_HOURS,
    SUMMARY_CUT_MARKERS,
    TRACKING_PARAMS,
    USER_AGENT,
)

# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\u00a0]+")
_BLOCK_RE = re.compile(r"</?(p|div|br|li|h[1-6]|tr|blockquote)[^>]*>", re.I)
_SCRIPT_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.I | re.S)


class NonFeedPayload(Exception):
    """HTTP 200, but the body is an HTML wall page rather than a feed.

    Cloudflare and friends happily return a challenge page to datacentre IPs
    (GitHub runners included) with a 200 status, so status codes alone are not
    enough to tell a good fetch from a broken one.
    """


class _RetryAfter(Exception):
    """Internal signal: back off for `delay` seconds, then try again."""

    def __init__(self, delay: float):
        super().__init__(f"rate limited, retry in {delay:.1f}s")
        self.delay = delay


# A feed always starts with markup such as <?xml, <rss, <feed or <rdf:RDF.
# An HTML doctype means we were served a wall page.
_HTML_START_RE = re.compile(rb"^\s*(?:<!doctype\s+html|<html)", re.I)
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def _looks_like_html(raw: bytes) -> bool:
    return bool(_HTML_START_RE.match(raw[:400]))


def _retry_after(headers) -> float:
    """Seconds to wait, per RFC 9110: either a delta or an HTTP-date."""
    value = (headers.get("Retry-After") or "").strip() if headers else ""
    if not value:
        return 0.0
    try:
        return max(0.0, min(float(value), 20.0))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return 0.0
    if when is None:
        return 0.0
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, min((when - datetime.now(timezone.utc)).total_seconds(), 20.0))


def _backoff(attempt: int) -> float:
    """0.8s, 1.6s, 3.2s ... with a little jitter so parallel workers desync."""
    return round(0.8 * (2 ** attempt) + random.uniform(0, 0.4), 2)


def _decompress(raw: bytes, encoding: str) -> bytes:
    if encoding == "gzip" or raw[:2] == b"\x1f\x8b":
        return gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
    if encoding == "deflate" or raw[:1] == b"\x78":
        try:
            return zlib.decompress(raw)
        except zlib.error:
            return zlib.decompress(raw, -zlib.MAX_WBITS)
    return raw


def http_get(
    url: str,
    timeout: int = FETCH_TIMEOUT,
    retries: int = FEED_RETRIES,
    expect_feed: bool = True,
) -> bytes:
    """GET a URL with backed-off retries and transparent gzip/deflate decoding.

    Datacentre IPs get rate limited (NASA answers 429) and bot-walled (Nature
    answers 200 with an HTML challenge), so a naive fetch loop silently loses
    whole outlets. Here a retryable status or an HTML body both back off and
    try again instead of failing the moment the first attempt goes wrong.
    """
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )
            try:
                resp = urllib.request.urlopen(req, timeout=timeout)
            except urllib.error.HTTPError as exc:
                if exc.code in _RETRYABLE_STATUS and attempt < retries:
                    raise _RetryAfter(_retry_after(exc.headers) or _backoff(attempt)) from exc
                raise
            with resp:
                raw = resp.read()
                enc = (resp.headers.get("Content-Encoding") or "").lower()
                final_url = resp.geturl()
            raw = _decompress(raw, enc)
            if expect_feed and _looks_like_html(raw):
                raise NonFeedPayload(f"{final_url} returned HTML, not a feed")
            return raw
        except _RetryAfter as exc:
            last = exc
            if attempt < retries:
                time.sleep(exc.delay)
        except NonFeedPayload as exc:
            # Usually a bot wall, occasionally a transient error page - one
            # more try after a pause is worth it, then give up.
            last = exc
            if attempt < retries:
                time.sleep(_backoff(attempt))
        except (urllib.error.URLError, socket.timeout, OSError, ValueError) as exc:
            last = exc
            if attempt < retries:
                time.sleep(_backoff(attempt))
    raise last if last else RuntimeError("unreachable")


# --------------------------------------------------------------------------- #
# Text helpers
# --------------------------------------------------------------------------- #


def html_to_text(fragment: str | None, limit: int = 0) -> str:
    """Strip markup + entities from a feed field and collapse whitespace."""
    if not fragment:
        return ""
    text = _SCRIPT_RE.sub(" ", fragment)
    text = _BLOCK_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = text.replace("\u200b", "").replace("\ufeff", "")
    text = _WS_RE.sub(" ", text)
    text = re.sub(r"\s*\n\s*", " ", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    text = re.sub(r"^[|\-\u2013\u2014:,\s]+", "", text)
    if limit and len(text) > limit:
        text = trim_to_sentence(text, limit)
    return text


def trim_to_sentence(text: str, limit: int) -> str:
    """Cut at the last sentence boundary before `limit`, else at a word break."""
    window = text[: limit + 40]
    for punct in (". ", "! ", "? ", "\u3002", "\u3001"):
        idx = window.rfind(punct, int(limit * 0.5))
        if idx > 0:
            return window[: idx + len(punct)].strip()
    cut = window.rfind(" ", 0, limit)
    if cut < limit * 0.5:
        cut = limit
    return window[:cut].strip().rstrip(",;:\u2014-") + "\u2026"


def clean_summary(fragment: str | None, limit: int = 240) -> str:
    """Summary-specific cleanup: drop trailing housekeeping boilerplate."""
    text = html_to_text(fragment)
    low = text.lower()
    for marker in SUMMARY_CUT_MARKERS:
        idx = low.find(marker)
        if idx > 60:
            text = text[:idx].strip()
            low = text.lower()
    text = text.rstrip(" .\u2026")
    if len(text) > limit:
        text = trim_to_sentence(text, limit)
    return text


# --------------------------------------------------------------------------- #
# Dates / URLs
# --------------------------------------------------------------------------- #


def parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    try:
        dt = parsedate_to_datetime(value)
        if dt is not None:
            return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, IndexError):
        pass
    iso = value.replace("Z", "+00:00")
    iso = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", iso)
    for candidate in (iso, iso[:19], iso[:10]):
        try:
            dt = datetime.fromisoformat(candidate)
            return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def canonical_url(url: str) -> str:
    """Strip tracking noise so the same story from two pulls hashes the same."""
    if not url:
        return ""
    url = html.unescape(url.strip())
    try:
        parts = urllib.parse.urlsplit(url)
        query = [
            (k, v)
            for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=False)
            if k.lower() not in TRACKING_PARAMS
        ]
        path = parts.path.rstrip("/") or "/"
        return urllib.parse.urlunsplit(
            (parts.scheme, parts.netloc.lower(), path, urllib.parse.urlencode(query), "")
        )
    except ValueError:
        return url


def make_id(url: str, title: str) -> str:
    basis = canonical_url(url) or title
    return hashlib.sha1(basis.encode("utf-8", "ignore")).hexdigest()[:12]


def norm_title(title: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", title.lower())


# --------------------------------------------------------------------------- #
# Feed parsing (RSS 2.0, RSS 1.0/RDF and Atom in one pass)
# --------------------------------------------------------------------------- #


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _find(node: ET.Element, *names: str) -> ET.Element | None:
    for child in node:
        if _local(child.tag) in names:
            return child
    return None


def _text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return "".join(node.itertext()).strip()


def _attr(node: ET.Element, *names: str) -> str:
    lowered = {_local(k): v for k, v in node.attrib.items()}
    for name in names:
        if lowered.get(name):
            return lowered[name]
    return ""


_IMG_SRC_RE = re.compile(r"""<img[^>]+?src\s*=\s*["']([^"']+)["']""", re.I)
_JUNK_IMG_RE = re.compile(r"(1x1|spacer|pixel|blank\.gif|transparent|/logo|icon-|sprite|avatar)", re.I)


def _first_img(blob: str | None) -> str | None:
    """Fallback: some feeds only expose the lead image inside the body HTML."""
    if not blob:
        return None
    for match in _IMG_SRC_RE.finditer(blob[:12000]):
        url = html.unescape(match.group(1)).strip()
        if url.startswith("http") and not _JUNK_IMG_RE.search(url):
            return url
    return None


def upgrade_image(url: str | None) -> str | None:
    """Ask CDNs for a bigger rendition - feeds hand out postage stamps."""
    if not url:
        return None
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return url
    host, path, query = parts.netloc.lower(), parts.path, parts.query

    if "guim.co.uk" in host or "theguardian.com" in host:
        query = re.sub(r"(^|&)width=\d+", r"\1width=760", query)
    elif "ichef.bbci.co.uk" in host:
        path = re.sub(r"/(\d{2,4})/", "/800/", path, count=1)
    elif host.endswith("mktw.net") or host.endswith("wsj.net"):
        if not query:
            query = "width=640"
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, query, ""))


def _pick_image(item: ET.Element) -> str | None:
    """Best-effort lead image. Many outlets ship none - the UI handles that."""
    for child in item:
        tag = _local(child.tag)
        if tag in ("thumbnail", "content", "group"):
            url = _attr(child, "url")
            medium = _attr(child, "medium", "type")
            if not url and tag == "group":
                for grand in child:
                    if _local(grand.tag) in ("content", "thumbnail"):
                        return _attr(grand, "url") or None
                continue
            if url and ("image" in medium.lower() or tag == "thumbnail" or not medium):
                if url.startswith("http"):
                    return url
        if tag == "enclosure":
            mime = _attr(child, "type")
            url = _attr(child, "url")
            if url.startswith("http") and (not mime or mime.startswith("image")):
                return url
        if tag == "link":
            if "enclosure" in _attr(child, "rel") and _attr(child, "type").startswith("image"):
                return _attr(child, "href") or None
    return None


def _pick_link(item: ET.Element) -> str:
    # RSS 1.0 / RDF puts the canonical URL in an attribute, not a <link> node.
    about = _attr(item, "about")
    if about.startswith("http"):
        return about

    # Atom: <link rel="alternate" href=...>; RSS: <link>text</link>
    best = ""
    for child in item:
        if _local(child.tag) != "link":
            continue
        href = _attr(child, "href")
        if href:
            rel = _attr(child, "rel") or "alternate"
            if rel == "alternate" and _attr(child, "type") in ("", "text/html"):
                return href
            if not best:
                best = href
        elif _text(child):
            return _text(child)
    if best:
        return best
    # Some feeds put the canonical URL in guid/@isPermaLink or a bare id.
    for child in item:
        if _local(child.tag) in ("guid", "id"):
            value = _text(child)
            if value.startswith("http"):
                return value
    return ""


def parse_feed(raw: bytes, feed: dict) -> list[dict]:
    """Parse one feed payload into normalised article dicts."""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        cleaned = re.sub(rb"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)", b"&amp;", raw)
        try:
            root = ET.fromstring(cleaned)
        except ET.ParseError:
            return []

    # RSS 2.0 -> rss/channel/item ; RSS 1.0 -> rdf:RDF/item ; Atom -> feed/entry
    nodes = [n for n in root.iter() if _local(n.tag) in ("item", "entry")]
    articles: list[dict] = []

    for item in nodes:
        title = html_to_text(_text(_find(item, "title")), 300)
        if not title:
            continue
        url = _pick_link(item)
        if not url.startswith("http"):
            continue

        summary_src = ""
        for name in ("description", "summary", "encoded", "content", "subtitle"):
            node = _find(item, name)
            if node is not None and _text(node):
                summary_src = _text(node)
                break

        published = None
        for name in ("pubdate", "published", "date", "updated", "created", "issued", "modified"):
            node = _find(item, name)
            if node is not None:
                published = parse_date(_text(node))
                if published:
                    break

        articles.append(
            {
                "id": make_id(url, title),
                "title": title,
                "summary": clean_summary(summary_src),
                "url": canonical_url(url),
                "source": feed["name"],
                "source_id": feed["id"],
                "category": feed["category"],
                "published": published.isoformat().replace("+00:00", "Z") if published else None,
                "_ts": published,
                "_max_age": feed.get("max_age_hours", MAX_AGE_HOURS),
                "image": upgrade_image(
                    _pick_image(item) or _first_img("".join(item.itertext()))
                ),
            }
        )
    return articles


def _short_host(url: str) -> str:
    try:
        return urllib.parse.urlsplit(url).netloc
    except ValueError:
        return url[:40]


def fetch_feed(feed: dict) -> tuple[dict, list[dict], str | None]:
    """Fetch one outlet, walking `fallbacks` if the primary URL disappoints.

    A single outlet can publish the same feed behind several endpoints, and
    which one a given network can reach varies. Trying them in order turns a
    hard failure into a slower success.
    """
    urls = [feed["url"], *(feed.get("fallbacks") or [])]
    problems: list[str] = []
    for url in urls:
        try:
            raw = http_get(url)
            items = parse_feed(raw, feed)
            if items:
                return feed, items, None
            problems.append(f"{_short_host(url)}: parsed 0 items")
        except Exception as exc:  # noqa: BLE001 - one bad feed must not kill the run
            problems.append(f"{_short_host(url)}: {type(exc).__name__}: {exc}")
    return feed, [], " | ".join(problems)


# --------------------------------------------------------------------------- #
# Pipeline stage: collect -> filter -> dedupe -> quota -> rank
# --------------------------------------------------------------------------- #


def collect(feeds: list[dict]) -> tuple[list[dict], list[dict]]:
    """Fetch every feed concurrently. Returns (articles, feed_status)."""
    articles: list[dict] = []
    status: list[dict] = []
    with ThreadPoolExecutor(max_workers=FEED_WORKERS) as pool:
        futures = {pool.submit(fetch_feed, f): f for f in feeds}
        for fut in as_completed(futures):
            feed, items, err = fut.result()
            status.append(
                {
                    "id": feed["id"],
                    "name": feed["name"],
                    "category": feed["category"],
                    "ok": err is None,
                    "count": len(items),
                    "error": err,
                }
            )
            articles.extend(items)
    status.sort(key=lambda s: (s["category"], s["name"]))
    return articles, status


def filter_fresh(articles: list[dict], now: datetime | None = None) -> list[dict]:
    """Prefer items inside the freshness window; top up with undated ones.

    Weekly journals (Nature) legitimately publish rarely, so a feed may carry a
    per-source `max_age_hours` override instead of the global window.
    """
    now = now or datetime.now(timezone.utc)
    fresh, undated = [], []
    for art in articles:
        ts = art.get("_ts")
        if ts is None:
            undated.append(art)
        else:
            window = art.get("_max_age") or MAX_AGE_HOURS
            if ts >= now - timedelta(hours=window):
                fresh.append(art)
    # Undated items are usually just missing a parser-friendly field, so a few
    # of them are worth keeping rather than discarding the whole outlet.
    return fresh + undated


def dedupe(articles: list[dict]) -> list[dict]:
    """Collapse the same story appearing twice (syndication, re-posts)."""
    seen_url: set[str] = set()
    seen_title: set[str] = set()
    out: list[dict] = []
    for art in sorted(articles, key=lambda a: a.get("_ts") or datetime.min.replace(tzinfo=timezone.utc), reverse=True):
        url_key = canonical_url(art["url"])
        title_key = norm_title(art["title"])[:80]
        if url_key in seen_url or (title_key and title_key in seen_title):
            continue
        seen_url.add(url_key)
        if title_key:
            seen_title.add(title_key)
        out.append(art)
    return out


def quota_and_rank(articles: list[dict], per_category: int, per_source_cap: int) -> list[dict]:
    """Round-robin across outlets so every source is represented, then trim."""
    by_cat: dict[str, list[dict]] = {}
    for art in articles:
        by_cat.setdefault(art["category"], []).append(art)

    final: list[dict] = []
    epoch = datetime.min.replace(tzinfo=timezone.utc)

    for cat, items in by_cat.items():
        items.sort(key=lambda a: a.get("_ts") or epoch, reverse=True)
        buckets: dict[str, list[dict]] = {}
        for art in items:
            buckets.setdefault(art["source_id"], []).append(art)

        # Order sources by how recent their newest story is, then interleave.
        order = sorted(
            buckets,
            key=lambda sid: buckets[sid][0].get("_ts") or epoch,
            reverse=True,
        )
        picked: list[dict] = []
        depth = 0
        while len(picked) < per_category and any(len(buckets[s]) > depth for s in order):
            for sid in order:
                if len(picked) >= per_category:
                    break
                if depth < min(len(buckets[sid]), per_source_cap):
                    picked.append(buckets[sid][depth])
            depth += 1
        picked.sort(key=lambda a: a.get("_ts") or epoch, reverse=True)
        final.extend(picked)

    return final
