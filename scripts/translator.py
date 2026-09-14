"""English -> Simplified Chinese translation.

Design goals, in priority order:

1. **Never block the build.** A translation outage must degrade to English, not
   hang. Every stage runs inside a wall-clock budget and every HTTP call has a
   short timeout.
2. **Never hammer a free endpoint.** Requests are paced, batched, and the first
   HTTP 429 trips a circuit breaker for that provider instead of triggering a
   retry storm (the usual cause of a "stuck" pipeline).
3. **Pay once.** Every translated string is cached on disk and committed, so a
   headline is translated exactly once across the entire life of the repo.
   A run that cannot finish everything simply continues next time.

Provider chain (first available wins):
    deepl            - needs DEEPL_API_KEY, best quality, reliable
    google_cloud     - needs GOOGLE_TRANSLATE_API_KEY, reliable
    google_free      - no key, public endpoint, rate limited
    mymemory         - no key, small daily quota, last resort
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from config import TRANSLATE_BATCH, USER_AGENT

CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache", "translations.json")
CACHE_LIMIT = 12000
MAX_TEXT_LEN = 1500

# Keep a single request URL comfortably small - long URLs are what free
# endpoints punish hardest.
REQUEST_CHAR_BUDGET = 1200

# Seconds between consecutive requests to the same provider. Free endpoints
# rate limit by burst, so pacing matters more than raw throughput here.
PACE_SECONDS = 0.35

# If every provider is rate limited but there is still budget left, wait once
# and give them a second chance rather than shipping an untranslated page.
COOLDOWN_SECONDS = 45

# Summary text is only ever shown as a 3 line preview, so translating the
# opening of it is enough and roughly halves the request volume.
SUMMARY_TRANSLATE_CHARS = 160

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_MYMEMORY = "https://api.mymemory.translated.net/get?langpair=en%7Czh-CN&q="


class RateLimited(Exception):
    """Raised on HTTP 429 so the caller can switch provider instead of retrying."""


def _normalize(text: str) -> str:
    """Collapse whitespace. Idempotent - safe to apply twice to one string."""
    return re.sub(r"\s+", " ", text or "").strip()


def _prepare(text: str, limit: int = MAX_TEXT_LEN) -> str:
    """Normalise, then hard-truncate.

    Must stay idempotent: the cache key produced by the caller has to survive a
    second pass through here, otherwise every lookup silently misses and the
    translation is thrown away.
    """
    return _normalize(text)[:limit].strip()


def _clip(text: str, limit: int) -> str:
    """Normalise, then cut on a word boundary so the translator sees whole words."""
    text = _normalize(text)
    if len(text) <= limit:
        return text
    cut = text.rfind(" ", 0, limit)
    return (text[:cut] if cut > int(limit * 0.6) else text[:limit]).strip()


def _has_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text))


def _http(url: str, timeout: int, data: bytes | None = None, headers: dict | None = None) -> str:
    hdrs = {"User-Agent": USER_AGENT}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "ignore")


# --------------------------------------------------------------------------- #
# Providers
# --------------------------------------------------------------------------- #


class Provider:
    name = "base"
    max_batch = 20
    timeout = 15

    def available(self) -> bool:
        return True

    def translate(self, texts: list[str]) -> list[str]:
        raise NotImplementedError


class GoogleWeb(Provider):
    """Google's public web-translate endpoint - no API key required.

    The `client` parameter selects a quota bucket. `gtx` is the one every
    scraper uses and it rate limits after a few dozen requests; `dict-chrome-ex`
    (the Chrome dictionary extension) has its own much more forgiving bucket.
    Two hosts are registered as separate providers so a throttled host cannot
    take the whole translation stage down with it.
    """

    max_batch = 12
    timeout = 12

    def __init__(self, host: str, client: str, label: str):
        self.host = host
        self.client = client
        self.name = label

    def translate(self, texts: list[str]) -> list[str]:
        base = f"https://{self.host}/translate_a/t?client={self.client}&sl=en&tl=zh-CN&"
        url = base + "&".join("q=" + urllib.parse.quote(t) for t in texts)
        try:
            payload = json.loads(_http(url, self.timeout))
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 403, 503):
                raise RateLimited(f"{self.name} HTTP {exc.code}") from exc
            raise
        if isinstance(payload, str):
            payload = [payload]
        if not isinstance(payload, list) or len(payload) != len(texts):
            raise ValueError("unexpected payload shape")
        out = []
        for item in payload:
            if isinstance(item, list):
                item = item[0] if item else ""
            out.append(str(item).strip() if item else "")
        return out


class MyMemory(Provider):
    """Free tier, one string per call, ~1000 words/day anonymous."""

    name = "mymemory"
    max_batch = 1
    timeout = 10

    def translate(self, texts: list[str]) -> list[str]:
        text = texts[0]
        url = _MYMEMORY + urllib.parse.quote(text[:480])
        try:
            payload = json.loads(_http(url, self.timeout))
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 403, 503):
                raise RateLimited(f"{self.name} HTTP {exc.code}") from exc
            raise
        out = str((payload.get("responseData") or {}).get("translatedText") or "").strip()
        upper = out.upper()
        if not out or "MYMEMORY WARNING" in upper or "QUERY LENGTH" in upper or "INVALID" in upper:
            raise ValueError("no usable translation")
        return [out]


class DeepL(Provider):
    """Free tier: 500k characters/month. Enabled by setting DEEPL_API_KEY."""

    name = "deepl"
    max_batch = 25

    def __init__(self, key: str):
        self.key = key
        host = "api-free.deepl.com" if key.endswith(":fx") else "api.deepl.com"
        self.url = f"https://{host}/v2/translate"

    def available(self) -> bool:
        return bool(self.key)

    def translate(self, texts: list[str]) -> list[str]:
        data = urllib.parse.urlencode(
            [("target_lang", "ZH"), ("auth_key", self.key)]
            + [("text", t) for t in texts]
        ).encode()
        try:
            payload = json.loads(_http(self.url, 20, data=data))
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 456):
                raise RateLimited(f"{self.name} HTTP {exc.code}") from exc
            raise
        return [t.get("text", "").strip() for t in payload.get("translations", [])]


class GoogleCloud(Provider):
    """Official API. Enabled by setting GOOGLE_TRANSLATE_API_KEY."""

    name = "google-cloud"
    max_batch = 25

    def __init__(self, key: str):
        self.key = key

    def available(self) -> bool:
        return bool(self.key)

    def translate(self, texts: list[str]) -> list[str]:
        url = "https://translation.googleapis.com/language/translate/v2?key=" + urllib.parse.quote(self.key)
        data = urllib.parse.urlencode(
            [("target", "zh-CN"), ("format", "text")] + [("q", t) for t in texts]
        ).encode()
        try:
            payload = json.loads(_http(url, 20, data=data))
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raise RateLimited(f"{self.name} HTTP 429") from exc
            raise
        items = (((payload.get("data") or {}).get("translations")) or [])
        return [i.get("translatedText", "").strip() for i in items]


# --------------------------------------------------------------------------- #
# Translator
# --------------------------------------------------------------------------- #


class Translator:
    def __init__(self, cache_path: str = CACHE_PATH, enabled: bool = True, budget_seconds: int = 240):
        self.cache_path = cache_path
        self.enabled = enabled
        self.budget_seconds = budget_seconds
        self.cache: dict[str, str] = {}
        self.hits = 0
        self.misses = 0
        self.failed = 0
        self.skipped = 0
        self.providers: list[Provider] = []
        self.dead: set[str] = set()
        self.used: dict[str, int] = {}
        self._last_call = 0.0
        self._load()
        if enabled:
            self.providers = [
                p for p in (
                    DeepL(os.environ.get("DEEPL_API_KEY", "").strip()),
                    GoogleCloud(os.environ.get("GOOGLE_TRANSLATE_API_KEY", "").strip()),
                    GoogleWeb("clients5.google.com", "dict-chrome-ex", "google-clients5"),
                    GoogleWeb("translate.googleapis.com", "dict-chrome-ex", "google-apis"),
                    GoogleWeb("translate.google.com", "dict-chrome-ex", "google-web"),
                    MyMemory(),
                ) if p.available()
            ]

    # ---------------------------------------------------------------- cache
    def _load(self) -> None:
        try:
            with open(self.cache_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                self.cache = {k: v for k, v in data.items() if isinstance(v, str) and v}
        except (OSError, ValueError):
            self.cache = {}

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
        if len(self.cache) > CACHE_LIMIT:
            for key in list(self.cache)[: len(self.cache) - CACHE_LIMIT]:
                del self.cache[key]
        with open(self.cache_path, "w", encoding="utf-8") as fh:
            json.dump(self.cache, fh, ensure_ascii=False, separators=(",", ":"))
            fh.write("\n")

    # ------------------------------------------------------------- dispatch
    def _call(self, provider: Provider, batch: list[str]) -> list[str]:
        """Pace, then call. Marks the provider dead on rate limiting."""
        gap = PACE_SECONDS - (time.time() - self._last_call)
        if gap > 0:
            time.sleep(gap)
        self._last_call = time.time()
        self.used[provider.name] = self.used.get(provider.name, 0) + 1
        return provider.translate(batch)

    def _translate_batch(self, batch: list[str]) -> list[str] | None:
        for provider in self.providers:
            if provider.name in self.dead:
                continue
            chunk = batch[: provider.max_batch]
            for attempt in range(2):
                try:
                    out = self._call(provider, chunk)
                    if len(out) == len(chunk):
                        out += [""] * (len(batch) - len(chunk))
                        return out
                    break  # bad shape - give the next provider a go
                except RateLimited:
                    # Stop using this provider for the rest of the run.
                    self.dead.add(provider.name)
                    break
                except Exception:  # noqa: BLE001 - transient network noise
                    if attempt == 1:
                        break
                    time.sleep(0.8)
        return None

    # --------------------------------------------------------------- public
    @staticmethod
    def _make_batches(texts: list[str]) -> list[list[str]]:
        batches: list[list[str]] = []
        current: list[str] = []
        size = 0
        for text in texts:
            cost = len(text) * 3 + 3
            if current and (size + cost > REQUEST_CHAR_BUDGET or len(current) >= TRANSLATE_BATCH):
                batches.append(current)
                current, size = [], 0
            current.append(text)
            size += cost
        if current:
            batches.append(current)
        return batches

    def translate_many(self, texts: list[str]) -> dict[str, str]:
        """Return {original: chinese}. Untranslated entries are simply absent."""
        result: dict[str, str] = {}
        if not self.enabled or not self.providers:
            return result

        pending: list[str] = []
        pending_seen: set[str] = set()
        counted: set[str] = set()
        for raw in texts:
            text = _prepare(raw)
            if not text:
                continue
            if text in self.cache:
                # Count each unique string once even if it repeats.
                if text not in counted:
                    counted.add(text)
                    self.hits += 1
                result[text] = self.cache[text]
            elif _has_cjk(text) or len(text) < 2:
                self.cache[text] = text
                result[text] = text
            elif text not in pending_seen:
                pending_seen.add(text)
                pending.append(text)

        if not pending:
            return result

        self.misses += len(pending)
        deadline = time.time() + self.budget_seconds
        batches = self._make_batches(pending)
        total = len(batches)

        index = 0
        cooldowns = 0
        while index < total:
            if time.time() > deadline:
                remaining = sum(len(b) for b in batches[index:])
                self.skipped += remaining
                print(f"      translation budget reached after {index}/{total} batches; "
                      f"{remaining} strings deferred to the next run", flush=True)
                break

            if all(p.name in self.dead for p in self.providers):
                if cooldowns == 0 and time.time() + COOLDOWN_SECONDS < deadline:
                    cooldowns += 1
                    print(f"      every provider rate limited - cooling down {COOLDOWN_SECONDS}s "
                          f"and retrying once", flush=True)
                    time.sleep(COOLDOWN_SECONDS)
                    self.dead.clear()
                    continue
                remaining = sum(len(b) for b in batches[index:])
                self.skipped += remaining
                print(f"      every provider rate limited; {remaining} strings deferred "
                      f"to the next run", flush=True)
                break

            batch = batches[index]
            index += 1
            translated = self._translate_batch(batch)
            if translated is None:
                self.failed += len(batch)
                continue
            for original, out in zip(batch, translated):
                final = (out or "").strip()
                # Reject obvious garbage (echoed URLs, runaway output).
                if final and not final.startswith("http") and len(final) <= len(original) * 4 + 60:
                    self.cache[original] = final
                    result[original] = final
                else:
                    self.failed += 1

        return result

    @property
    def stats(self) -> dict:
        ok = sum(1 for p in self.providers if p.name not in self.dead)
        return {
            "cache_entries": len(self.cache),
            "cache_hits": self.hits,
            "translated": self.misses,
            "failed": self.failed,
            "deferred": self.skipped,
            "providers_ok": ok,
            "providers_total": len(self.providers),
            "requests": self.used,
        }


# --------------------------------------------------------------------------- #
# Article-level helper
# --------------------------------------------------------------------------- #


def translate_articles(articles: list[dict], translator: Translator) -> None:
    """Translate titles first, then summary previews.

    Ordering matters: if the budget runs out, the headlines - the part people
    actually scan - are already done, and summaries catch up on later runs
    from the cache.
    """
    title_keys: dict[str, str] = {}
    summary_keys: dict[str, str] = {}

    ordered: list[str] = []
    for art in articles:
        key = _prepare(art.get("title") or "")
        if key:
            title_keys[art["id"]] = key
        ordered.append(key)

    for art in articles:
        text = _clip(art.get("summary") or "", SUMMARY_TRANSLATE_CHARS)
        if text:
            summary_keys[art["id"]] = text
            ordered.append(text)

    mapping = translator.translate_many(ordered)

    for art in articles:
        title = art.get("title") or ""
        art["title_zh"] = mapping.get(title_keys.get(art["id"], "")) or title
        summary = art.get("summary") or ""
        art["summary_zh"] = mapping.get(summary_keys.get(art["id"], "")) or summary


__all__ = ["Translator", "translate_articles", "RateLimited"]
