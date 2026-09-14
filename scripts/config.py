"""Feed configuration for the Daily Brief news aggregator.

Everything the pipeline publishes is described here. To add or remove a
source, edit FEEDS only - the fetcher, the translator and the frontend all
read their structure from the generated JSON, so no other file needs to
change.

Only outlets with a public, stable feed are listed. Verified 2026-09.

Each feed takes an optional "fallbacks" list: alternate endpoints for the same
outlet, tried in order when the primary URL errors or returns a non-feed body.
"""

# --- tunables -----------------------------------------------------------------

# How many articles we keep per category after the per-source quota is applied.
PER_CATEGORY = 30

# No single outlet may contribute more than this many items to one category.
# This is what keeps the page from turning into a single-source dump.
PER_SOURCE_CAP = 9

# Drop anything older than this. Feeds are re-polled daily, so a 3 day window
# keeps the page fresh while still tolerating a quiet weekend.
MAX_AGE_HOURS = 72

# Network behaviour. Retries back off (0.8s, 1.6s, 3.2s ...) and honour
# Retry-After, so three attempts still land well inside the job budget while
# surviving the rate limiting a shared GitHub runner IP attracts.
FETCH_TIMEOUT = 25
FETCH_RETRIES = 3
FEED_WORKERS = 8

# Translation: total wall-clock ceiling for the whole stage, and how many
# strings may go into one request on the no-key providers.
TRANSLATE_BUDGET_SECONDS = 240
TRANSLATE_BATCH = 12

USER_AGENT = "Mozilla/5.0 (compatible; DailyBriefBot/1.0; +https://github.com/)"

# Used only as a second attempt after a feed answers with an HTML bot wall.
# Such walls routinely pass a top-level browser navigation and block anything
# that looks like a feed reader, so the retry has to look like the former.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# --- categories ---------------------------------------------------------------

CATEGORIES = [
    {"id": "world", "en": "World", "zh": "国际头条", "accent": "#2563eb"},
    {"id": "tech", "en": "Technology", "zh": "科技", "accent": "#7c3aed"},
    {"id": "business", "en": "Business", "zh": "商业财经", "accent": "#0d9488"},
    {"id": "science", "en": "Science", "zh": "科学", "accent": "#ea580c"},
]

# --- feeds --------------------------------------------------------------------

FEEDS = [
    # ---------------- World ----------------
    {
        "id": "bbc-world",
        "name": "BBC World",
        "home": "https://www.bbc.com/news/world",
        "category": "world",
        "url": "https://feeds.bbci.co.uk/news/world/rss.xml",
    },
    {
        "id": "guardian-world",
        "name": "The Guardian",
        "home": "https://www.theguardian.com/world",
        "category": "world",
        "url": "https://www.theguardian.com/world/rss",
    },
    {
        "id": "npr",
        "name": "NPR",
        "home": "https://www.npr.org",
        "category": "world",
        "url": "https://feeds.npr.org/1001/rss.xml",
    },
    {
        "id": "aljazeera",
        "name": "Al Jazeera",
        "home": "https://www.aljazeera.com",
        "category": "world",
        "url": "https://www.aljazeera.com/xml/rss/all.xml",
    },
    # ---------------- Technology ----------------
    {
        "id": "ars-technica",
        "name": "Ars Technica",
        "home": "https://arstechnica.com",
        "category": "tech",
        "url": "https://feeds.arstechnica.com/arstechnica/index",
    },
    {
        "id": "the-verge",
        "name": "The Verge",
        "home": "https://www.theverge.com",
        "category": "tech",
        "url": "https://www.theverge.com/rss/index.xml",
    },
    {
        "id": "wired",
        "name": "WIRED",
        "home": "https://www.wired.com",
        "category": "tech",
        "url": "https://www.wired.com/feed/rss",
    },
    {
        "id": "hacker-news",
        "name": "Hacker News",
        "home": "https://news.ycombinator.com",
        "category": "tech",
        "url": "https://news.ycombinator.com/rss",
    },
    # ---------------- Business ----------------
    {
        "id": "cnbc",
        "name": "CNBC",
        "home": "https://www.cnbc.com",
        "category": "business",
        "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
    },
    {
        "id": "marketwatch",
        "name": "MarketWatch",
        "home": "https://www.marketwatch.com",
        "category": "business",
        "url": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    },
    {
        "id": "guardian-business",
        "name": "Guardian Business",
        "home": "https://www.theguardian.com/uk/business",
        "category": "business",
        "url": "https://www.theguardian.com/uk/business/rss",
    },
    {
        "id": "nyt-business",
        "name": "The New York Times",
        "home": "https://www.nytimes.com/section/business",
        "category": "business",
        "url": "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml",
    },
    # ---------------- Science ----------------
    {
        "id": "sciencedaily",
        "name": "ScienceDaily",
        "home": "https://www.sciencedaily.com",
        "category": "science",
        "url": "https://www.sciencedaily.com/rss/all.xml",
    },
    {
        "id": "nasa",
        "name": "NASA",
        "home": "https://www.nasa.gov",
        "category": "science",
        "url": "https://www.nasa.gov/rss/dyn/breaking_news.rss",
        # NASA fronts several endpoints with one rate limiter, so from a shared
        # runner IP these mostly fail together - the backoff is what saves it.
        "fallbacks": [
            "https://www.nasa.gov/feed/",
            "https://www.nasa.gov/news-release/feed/",
        ],
    },
    {
        "id": "nature",
        "name": "Nature",
        "home": "https://www.nature.com",
        "category": "science",
        "url": "https://www.nature.com/nature.rss",
        # Nature's edge intermittently answers a datacentre IP with an HTML
        # challenge page - and when it does, it does so for every endpoint.
        # These are three front doors to the same feed, tried in order.
        "fallbacks": [
            "https://www.nature.com/nature/current-issue.rss",
            "https://www.nature.com/nature/press_releases.rss",
        ],
        # Nature is a weekly journal - a 72h window would yield one item a day.
        "max_age_hours": 24 * 14,
    },
    {
        "id": "phys-org",
        "name": "Phys.org",
        "home": "https://phys.org",
        "category": "science",
        "url": "https://phys.org/rss-feed/",
    },
    # Two more science outlets so a single blocked feed cannot gut the section.
    {
        "id": "science-news",
        "name": "Science News",
        "home": "https://www.sciencenews.org",
        "category": "science",
        "url": "https://www.sciencenews.org/feed",
    },
    {
        "id": "new-scientist",
        "name": "New Scientist",
        "home": "https://www.newscientist.com",
        "category": "science",
        "url": "https://www.newscientist.com/feed/home/",
    },
]

# --- cleaning -----------------------------------------------------------------

# Feeds routinely append housekeeping copy to their summaries. These markers
# get the rest of the summary discarded from that point on.
SUMMARY_CUT_MARKERS = [
    "continue reading",
    "read more",
    "the post ",
    "appeared first on",
    "sign up for",
    "subscribe to",
    "follow us on",
    "advertisement",
    "this article was originally published",
    "click here",
    "photo:",
    "credit:",
    "image:",
    "getty images",
    "all rights reserved",
]

# Tracking parameters stripped before an article URL is hashed or stored.
TRACKING_PARAMS = [
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_name", "utm_reader", "utm_brand", "fbclid", "gclid",
    "mc_cid", "mc_eid", "ref", "ref_src", "cmpid", "partner", "at_medium",
    "at_campaign", "ns_mchannel", "ns_campaign", "ito", "guccounter",
]
