# Product Spy

A local product-research dashboard. It pulls public ad data from **TikTok**, **Facebook** and **Instagram** (via [Apify](https://apify.com) Actors), extracts and deduplicates the products being advertised, scores each one with a **Trend Score (0–100)**, and stores every run in **Supabase** (hosted Postgres) so you can track products, ads and competitors over time.

- **Backend:** Flask JSON API (`app/server.py`)
- **Frontend:** plain HTML/CSS/JS served by the same Flask app (`app/static/`)
- **Analysis engine:** `app/core.py` (normalization, filtering, dedup, scoring)
- **Data collection:** `app/apify.py` (Apify Actors + 7-day local cache)
- **Storage:** `app/db.py` (Supabase/Postgres, tables auto-created)

> **What a Trend Score means:** a high score means *stronger public ad/trend signals* (reach, engagement, repetition, cross-platform presence, persistence). It is **not** verified sales. Public ad libraries do not expose commercial sales figures.

---

## Table of contents

1. [Features](#features)
2. [How it works](#how-it-works)
3. [Requirements](#requirements)
4. [Installation](#installation)
5. [Configuration](#configuration)
6. [Running the app](#running-the-app)
7. [Usage guide](#usage-guide)
8. [Importing your own data](#importing-your-own-data)
9. [Trend Score](#trend-score)
10. [API reference](#api-reference)
11. [Database schema](#database-schema)
12. [Project structure](#project-structure)
13. [Testing](#testing)
14. [Troubleshooting](#troubleshooting)
15. [Cost, limitations and legal notes](#cost-limitations-and-legal-notes)
16. [License](#license)

---

## Features

- **One-click search** across TikTok (Creative Center Top Ads) and Meta (Facebook + Instagram Ad Library) for a chosen country, e.g. 🇩🇿 Algeria (`DZ`).
- **Multiple ways to load data:** live Apify search, an existing Apify dataset ID, uploaded CSV/JSON/JSONL/XLSX/XLS files, or bundled sample data.
- **Non-product filtering:** drops app-install, game, story/novel and adult-content ads (keyword and URL heuristics, multilingual incl. Arabic and French), and shows what was excluded and why.
- **Product deduplication:** groups ads that advertise the same product using token-overlap similarity with an adjustable threshold.
- **Trend Score 0–100** per product, plus a ranked Top Products table and CSV download.
- **Persistence in Supabase:** products are matched across runs, ads are upserted (not duplicated), and each save writes a snapshot for trend history.
- **Ad intelligence:** per-ad creative format, messaging angle, CTA, offer and hook (keyword heuristics), ad lifespan, countries, and market expansion.
- **Competitor intelligence:** advertiser search and profiles (active vs. total ads, top products, top angles, countries).
- **Cost control:** identical searches are cached on disk for 7 days; a "Force refresh" option bypasses the cache.

## How it works

```
Apify Actors / dataset / file upload / sample
        │
        ▼
  normalize_row()      unify field names, parse numbers like "10K", resolve best URL
        │
        ▼
  is_non_product_ad()  optional filter: apps, games, novels, adult content
        │
        ▼
  build_products()     group similar ads → products → Trend Score 0–100
        │
        ├──► Top Products table / CSV download (browser)
        │
        ▼  (only when you click "Save this run to database")
  db.save_run()        Supabase/Postgres: products, ads, advertisers, creatives,
                       ad_countries, product_snapshots
```

Analysis (`/api/analyze`) never touches the database. Only saving and the **Tracked Products**, **Ad Intelligence** and **Competitor Intelligence** tabs use it.

## Requirements

- **Python 3.11 or newer** recommended (the project was written and documented for 3.11+)
- An **Apify account and API token** (only needed for live search and dataset loading)
- A **Supabase project** (free tier is fine) — its Postgres connection string is required to start using saved data
- Python packages (installed from `requirements.txt`):

| Package | Version | Used for |
|---|---|---|
| Flask | `>=3.0,<4` | Web server and JSON API |
| pandas | `>=2.2,<3` | Reading CSV/XLSX uploads |
| openpyxl | `>=3.1,<4` | XLSX support for pandas |
| apify-client | `>=1.7,<2` | Running Apify Actors |
| psycopg2-binary | `>=2.9,<3` | Postgres/Supabase driver |

Reading legacy `.xls` files with pandas may additionally require `xlrd`, which is **not** in `requirements.txt`. If you need `.xls`, run `pip install xlrd`, or save the file as `.xlsx`.

## Installation

### 1. Get the code

Unzip the project and open a terminal in the project root (the folder containing `requirements.txt`).

### 2. Set up Supabase (database)

1. Create a project at [supabase.com](https://supabase.com).
2. In the dashboard click **Connect** and copy the **Session pooler** connection string. Do not use "Direct connection": it is IPv6-only unless you have the IPv4 add-on, and most home/office networks are IPv4-only.
3. Replace `[YOUR-PASSWORD]` with your database password. It has the form:

   ```
   postgresql://postgres.<project-ref>:[YOUR-PASSWORD]@aws-<region>.pooler.supabase.com:5432/postgres
   ```

   If your password contains special characters (`@`, `:`, `/`, `#`, `%`…), URL-encode them (for example `@` → `%40`).

You do **not** need to run any SQL. The tables are created automatically the first time the app connects.

### 3. Get an Apify token

Create an account at [apify.com](https://apify.com) and copy your API token from **Settings → API & Integrations**. It looks like `apify_api_...`.

### 4. Install and run

**Windows**

1. Set your environment variables first (see [Configuration](#configuration)).
2. Double-click `run_windows.bat`, or run it from a terminal. It creates `.venv`, installs dependencies, starts the server, and opens your browser.

Manual equivalent (PowerShell):

```powershell
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app\server.py
```

**macOS / Linux**

```bash
./run.sh
```

(If needed: `chmod +x run.sh`.) Manual equivalent:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app/server.py
```

### 5. Verify the database connection (recommended)

With `DATABASE_URL` set and the virtual environment active:

```bash
python app/check_db.py
```

Expected output on success:

```
Connecting…
✅ Connected to Postgres/Supabase.
Tables: ad_countries, ads, advertisers, creatives, product_snapshots, products
Row counts: products=0, ads=0, advertisers=0, product_snapshots=0, creatives=0
```

On failure it prints `❌ Could not connect: <reason>` and exits with code 1.

## Configuration

Configuration is done with environment variables.

| Variable | Required | Description |
|---|---|---|
| `APIFY_TOKEN` | For live search and dataset loading | Apify API token. Read **only** from the server's environment; there is no token field in the UI. All searches are billed to this account. |
| `DATABASE_URL` | For saving and the Tracked / Intelligence / Competitor tabs | Supabase **Session pooler** connection string. |
| `SUPABASE_DB_URL` | Optional alias | Used only if `DATABASE_URL` is empty or unset. |

> **Important: `.env` files are not loaded automatically.** The app reads real environment variables and does not use `python-dotenv`. `.env.example` is a template you can fill in and then load into your shell yourself.

**Windows PowerShell (current session):**

```powershell
$env:APIFY_TOKEN="apify_api_..."
$env:DATABASE_URL="postgresql://postgres.xxxx:PASSWORD@aws-0-eu-central-1.pooler.supabase.com:5432/postgres"
```

**Windows PowerShell (permanent; applies to *new* terminals only):**

```powershell
setx APIFY_TOKEN "apify_api_..."
setx DATABASE_URL "postgresql://postgres.xxxx:PASSWORD@aws-0-eu-central-1.pooler.supabase.com:5432/postgres"
```

**macOS / Linux (current session):**

```bash
export APIFY_TOKEN="apify_api_..."
export DATABASE_URL="postgresql://postgres.xxxx:PASSWORD@aws-0-eu-central-1.pooler.supabase.com:5432/postgres"
```

**macOS / Linux using a `.env` file:**

```bash
cp .env.example .env      # then edit .env and fill in the two values
set -a; source .env; set +a
./run.sh
```

`.env` is listed in `.gitignore`; never commit real secrets.

The server listens on `http://127.0.0.1:8765` (host and port are set in `app/server.py`) and opens your browser automatically. The API has **no authentication**, so keep it bound to localhost; do not expose it to a network as-is.

## Running the app

```bash
python app/server.py
```

Then open <http://127.0.0.1:8765> if your browser did not open by itself. Stop with `Ctrl+C`.

The top bar shows whether an Apify token was detected (from `GET /api/status`).

## Usage guide

### Search live ads

1. In the left panel choose a **Region** and **Country** (e.g. MENA → 🇩🇿 Algeria).
2. Optionally enter **keywords**, comma separated (e.g. `skincare, chaussures, منتج`).
3. Pick the **TikTok period** (7, 30 or 180 days) and **Max ads per source** (10–200, default 50). Start with 20–50 to limit cost.
4. Click **Search TikTok + Facebook + Instagram**.
5. Review the messages at the top (for example `TikTok: 42 ads`, `Meta (Facebook/Instagram): 37 ads`) and the results.

Keyword behavior:

- **TikTok:** only the **first** keyword is used. With no keyword, broad country discovery is used.
- **Meta:** all keywords are used as search queries. With none, a built-in list of generic French/English/Arabic terms is used (`produit`, `livraison`, `promo`, `منتج`, `تخفيض`, …).
- **Global (all countries):** the app searches with `US` as the country code and shows a note saying so. For regional research, choose a specific country.

### Load an existing Apify dataset

Paste a dataset ID into **Apify dataset ID** and click **Load from dataset ID**. This reads the dataset directly and requires `APIFY_TOKEN`.

### Import a file or try the sample

Drop one or more `.csv`, `.json`, `.jsonl`, `.xlsx` or `.xls` files into the import box, or click **Load sample data** to see the app work with no Apify token and no database (six demo ads).

### Analysis settings

| Setting | Default | Effect |
|---|---|---|
| Duplicate similarity threshold | `0.32` (slider 0.20–0.70) | Lower merges more ads into one product; higher splits more. |
| Top N products | `20` | How many products are shown. |
| Exclude app/game/novel/adult-content ads | on | Removes non-product ads before grouping. |
| Force refresh (skip 7-day cache) | off | Re-runs the Apify Actors instead of using cached results. |
| TikTok / Meta Actor | `burbn/tiktok-top-ads-spy` / `webdatalabs/meta-ad-library-scraper` | Editable Actor IDs, so you can switch providers without changing the analysis code. |

Tip: with the bundled sample data, the default threshold (0.32) keeps the three "Portable Blender" ads as separate products because their wording differs. Lowering the slider to about 0.25 merges them into one product.

### Tabs

- **Search & Results:** ads loaded, product count, excluded ads (with reasons), the Top Products table, **Download CSV**, the best ad link per product, and score components. **Save this run to database** stores the current analysis and reports how many products/ads/creatives were new or updated.
- **Tracked Products:** every saved product with its latest Trend Score, times tracked, days tracked and a reach-based direction label (`🆕 NEW`, `📈 RISING`, `📉 DECLINING`, `🟢 STABLE`).
- **Ad Intelligence:** pick a saved product to see advertisers, platforms, countries, lifespan, main angle and CTA, offers seen, a trend history, newly entered markets, and a per-ad breakdown.
- **Competitor Intelligence:** search or browse advertisers and open a profile (active/total ads, products, platforms, countries, creative variations, top products, top angles).

### Typical workflow

1. Search a country → review Top Products.
2. Click **Save this run to database**.
3. Repeat the search on later days (use **Force refresh**, because otherwise results are cached for 7 days).
4. Use **Tracked Products** to see which products are rising, and **Ad Intelligence** for how they are advertised.

## Importing your own data

Files are normalized by `normalize_row()`. Column/field names are matched **case-insensitively**, and the first non-empty alias wins.

| Meaning | Accepted names |
|---|---|
| Platform | `platform`, `platforms`, `source`, `network`, `channel` |
| Ad ID | `ad_id`, `adId`, `id`, `creative_id` (falls back to the Meta archive ID, then a hash) |
| Advertiser | `advertiser`, `advertiserName`, `page_name`, `pageName`, `brand_name`, `brand`, `brandName`, `company`, `author`, `account_name` |
| Ad text | `ad_text`, `adText`, `body`, `text`, `description`, `caption`, `primary_text`, `creative_text`, `adCopy`, `ad_copy` |
| Title | `title`, `headline`, `ad_title`, `adTitle`, `name`, `ad_name` |
| Product name | `product`, `product_name`, `product_title`, `item_name` |
| Start date | `started_at`, `start_date`, `start_time`, `ad_start_date`, `first_seen`, `startDate`, `firstShownDate` (ISO 8601, e.g. `2026-09-01`) |
| Country | `country`, `country_code`, `countryCode`, `target_country`, `targetCountry` |
| Impressions | `impressions`, `impression_count`, `impressionCount`, `reachRange` |
| Views | `views`, `view_count`, `video_views`, `videoViews` |
| Likes | `likes`, `like_count`, `likeCount`, `like` |
| Comments | `comments`, `comment_count`, `commentCount` |
| Shares | `shares`, `share_count`, `shareCount` |
| Spend | `spend`, `ad_spend`, `amount_spent`, `cost` |
| Landing URL | `landingUrl`, `landing_url`, `landingPageUrl`, `landing_page`, `destination_url`, `destinationUrl`, `link`, `ad_url`, `url`, `adCreativeUrl` |

Notes:

- **Numbers** may be plain or abbreviated: `10K` → 10,000, `1.5M` → 1,500,000, `2B` → 2,000,000,000; commas are ignored. Missing values become `0`.
- **Platform** is recognized from text containing `tiktok`, `facebook`/`meta`, or `instagram`/`ig`. Anything else becomes `Unknown`, which counts as its own platform in the cross-platform score. Include a `platform` column for best results.
- **JSON** files may be a top-level array, or an object with an `items` or `data` array. **JSONL** is one object per line.
- **URL resolution order:** landing/destination URL → Meta Ad Library permalink (from the archive ID, Facebook/Instagram only) → advertiser page URL → video/creative URL.

Minimal CSV example:

```csv
platform,ad_id,advertiser,title,ad_text,views,likes,comments,shares,start_date,url
TikTok,tt1,Demo Brand,Portable Blender,Portable blender for smoothies. Shop now,420000,12000,900,3000,2026-09-01,https://example.com/a
```

## Trend Score

Products are ranked by a weighted score from 0 to 100:

```
trend_score = 0.33 × reach
            + 0.24 × engagement
            + 0.18 × ad_repetition
            + 0.15 × cross_platform
            + 0.10 × persistence
```

| Component | Weight | Definition (0–100) |
|---|---|---|
| `reach` | 0.33 | Sum over the product's ads of `max(impressions, views)`, then `log1p` and min–max normalized across all products in the run. |
| `engagement` | 0.24 | Sum of `likes + 2×comments + 3×shares`, then `log1p` and min–max normalized. |
| `ad_repetition` | 0.18 | Number of ads for the product, then `log1p` and min–max normalized. |
| `cross_platform` | 0.15 | `platforms / 3 × 100`, capped at 100 (max at TikTok + Facebook + Instagram). |
| `persistence` | 0.10 | `15 × (number of ads whose start date is 7+ days ago)`, capped at 100. Ads without a parseable start date do not count. |

Things to know:

- **Scores are relative to the run.** Min–max normalization compares products only against the other products in the same run (if every product ties on a component, it scores 50). Use scores to rank within a run, not to compare across runs.
- For that reason, the **direction** label in Tracked Products is based on raw `reach` change between the two latest snapshots (≥ +20% rising, ≤ −20% declining, otherwise stable), not on the Trend Score.
- When several groups from one run match the same database product on save, they are merged into one snapshot with an ad-count-weighted average score.

## API reference

Base URL: `http://127.0.0.1:8765`. All request and response bodies are JSON unless noted. There is no authentication.

**Error handling.** Validation errors return `4xx` with `{"error": "<message>"}`. Unhandled exceptions (for example a missing/unreachable database, or malformed input such as a non-numeric `limit`) return Flask's default `500` HTML error page rather than JSON.

**The ad object.** Endpoints that return ads use this normalized shape, and `/api/analyze` and `/api/save` expect it back:

```json
{
  "platform": "TikTok",
  "ad_id": "tt1",
  "advertiser": "Demo Brand",
  "text": "Portable blender for smoothies. Shop now",
  "title": "Portable Blender",
  "product_hint": "",
  "url": "https://example.com/a",
  "url_type": "landing",
  "started_at": "2026-09-01",
  "impressions": 0.0,
  "likes": 12000.0,
  "comments": 900.0,
  "shares": 3000.0,
  "views": 420000.0,
  "spend": 0.0,
  "country": "",
  "raw": { }
}
```

`url_type` is one of `landing`, `library`, `page`, `video`, `none`. `raw` holds the original source row and is optional.

> **Important:** `/api/analyze` and `/api/save` do not fill in defaults for missing fields. Send complete ad objects exactly as returned by `/api/search`, `/api/dataset`, `/api/upload` or `/api/sample`. A partial object (for example without `product_hint`) causes a `500`.

### Configuration and reference data

#### `GET /api/status`

```json
{
  "apify_token_set": true,
  "default_tiktok_actor": "burbn/tiktok-top-ads-spy",
  "default_meta_actor": "webdatalabs/meta-ad-library-scraper"
}
```

#### `GET /api/regions`

Region and country list used by the UI (MENA, European Union, Other, Global).

```json
{
  "regions": [
    {
      "name": "🕌 MENA",
      "countries": [
        { "label": "🇩🇿 Algeria", "code": "DZ" },
        { "label": "🇲🇦 Morocco", "code": "MA" }
      ]
    }
  ]
}
```

`ALL` is the code for "Global".

### Getting ads

#### `POST /api/search`

Runs the TikTok and Meta Apify Actors and returns normalized ads. Requires `APIFY_TOKEN`.

| Field | Type | Default | Notes |
|---|---|---|---|
| `country` | string | *(required)* | ISO-2 code (case-insensitive). `ALL` searches with `US`. |
| `keywords` | string[] | `[]` | TikTok uses only the first; Meta uses all. |
| `max_results` | integer | `50` | Per source. |
| `period` | string | `"30"` | TikTok period in days (UI offers 7, 30, 180). |
| `include_details` | boolean | `false` | Accepted but currently has no effect. |
| `tiktok_actor` | string | `burbn/tiktok-top-ads-spy` | Apify Actor ID. |
| `meta_actor` | string | `webdatalabs/meta-ad-library-scraper` | Apify Actor ID. |
| `force_refresh` | boolean | `false` | `true` bypasses the 7-day disk cache. |

```bash
curl -X POST http://127.0.0.1:8765/api/search \
  -H "Content-Type: application/json" \
  -d '{"country":"DZ","keywords":["skincare"],"max_results":30,"period":"30"}'
```

Response:

```json
{
  "ads": [ /* ad objects; ad.country is set to the searched country if the source gave none */ ],
  "messages": ["TikTok: 30 ads", "Meta (Facebook/Instagram): 28 ads"]
}
```

Failures of an individual source do **not** produce an HTTP error. They appear in `messages` (e.g. `"TikTok error: No Apify token configured…"`) and that source contributes no ads. Errors: `400` if `country` is missing.

#### `POST /api/dataset`

Loads rows from an existing Apify dataset. Requires `APIFY_TOKEN`.

```bash
curl -X POST http://127.0.0.1:8765/api/dataset \
  -H "Content-Type: application/json" \
  -d '{"dataset_id":"aBcD123"}'
```

Response: `{"ads": [ ... ]}`. Errors: `400` `{"error": "dataset_id is required"}`, or `400` with the underlying Apify error text.

#### `POST /api/upload`

Multipart form upload. Use the field name `files` (repeatable). Supported: `.csv`, `.json`, `.jsonl`, `.xlsx`, `.xls`.

```bash
curl -X POST http://127.0.0.1:8765/api/upload \
  -F "files=@ads.csv" -F "files=@more_ads.json"
```

Response (per-file failures do not fail the request):

```json
{
  "ads": [ /* ad objects from all valid files */ ],
  "errors": [ { "file": "notes.txt", "error": "Use CSV, JSON, JSONL, XLSX or XLS" } ]
}
```

#### `GET /api/sample`

Returns the six demo ads from `data/sample_ads.json`, normalized: `{"ads": [ ... ]}`.

### Analysis and saving

#### `POST /api/analyze`

Filters, groups and scores ads. **Does not use the database.**

| Field | Type | Default | Notes |
|---|---|---|---|
| `ads` | ad[] | `[]` | Complete ad objects (see above). |
| `exclude_non_product` | boolean | `true` | Drops app/game/novel/adult-content ads. |
| `threshold` | number | `0.32` | Dedup similarity threshold. |

```bash
curl -s http://127.0.0.1:8765/api/sample > sample.json
curl -X POST http://127.0.0.1:8765/api/analyze \
  -H "Content-Type: application/json" \
  -d "$(jq '{ads: .ads, threshold: 0.25}' sample.json)"
```

Response (abridged):

```json
{
  "ads_loaded": 6,
  "excluded_count": 0,
  "excluded_breakdown": [],
  "excluded_ads": [],
  "products": [
    {
      "product": "Portable Blender",
      "ads": 3,
      "platforms": ["Facebook", "Instagram", "TikTok"],
      "platform_count": 3,
      "advertisers": ["Demo Brand", "Kitchen Co"],
      "reach": 795000.0,
      "engagement": 41920.0,
      "persistence_ads": 3,
      "urls": ["https://example.com/a", "https://example.com/b", "https://example.com/c"],
      "top_ad": {
        "platform": "TikTok",
        "advertiser": "Demo Brand",
        "title": "Portable Blender",
        "url": "https://example.com/a",
        "url_type": "landing",
        "ad_id": "tt1",
        "engagement": 22800.0
      },
      "trend_score": 85.5
    }
  ],
  "score_weights": {
    "reach": 0.33, "engagement": 0.24, "ad_repetition": 0.18,
    "cross_platform": 0.15, "persistence": 0.1
  }
}
```

- `ads_loaded` is the number of ads **kept** after filtering.
- `excluded_ads` items: `{"title", "platform", "reason", "url"}`; `excluded_breakdown` items: `{"reason", "count"}`. Reasons: `Mobile/Web App`, `Story/Novel App`, `Mobile Game`, `Adult/18+ Content`, `App store / deep link URL`.
- `products` is sorted by `trend_score` descending. `persistence_ads` may vary with the current date, since it depends on ad age.

#### `POST /api/save`

Runs the same pipeline as `/api/analyze` (same request body) and **persists** the result to Supabase. Requires `DATABASE_URL`.

```bash
curl -X POST http://127.0.0.1:8765/api/save \
  -H "Content-Type: application/json" \
  -d "$(jq '{ads: .ads}' sample.json)"
```

Response:

```json
{
  "summary": {
    "products_new": 2,
    "products_matched": 0,
    "ads_new": 6,
    "ads_updated": 0,
    "creatives_new": 6
  },
  "creative_type_breakdown": [
    { "creative_type": "Unclassified", "count": 6 }
  ]
}
```

Errors: `400` `{"error": "No products to save — run analysis first."}` when nothing is left to save.

Saving the same ads again updates them (`ads_updated`) instead of duplicating them and adds a new snapshot to each product's history.

### Tracked products and ad intelligence (database)

All endpoints in this section and the next require `DATABASE_URL`.

#### `GET /api/tracked?limit=100`

Latest state of every tracked product, best Trend Score first. `limit` defaults to `100`.

```json
{
  "products": [
    {
      "id": 1,
      "name": "Portable Blender",
      "first_seen": "2026-09-27T17:40:00+00:00",
      "last_seen": "2026-09-29T09:10:00+00:00",
      "trend_score": 85.5,
      "reach": 795000.0,
      "engagement": 41920.0,
      "ads_count": 3,
      "platform_count": 3,
      "times_tracked": 2,
      "days_tracked": 1.6,
      "direction": "📈 RISING"
    }
  ]
}
```

`direction` is one of `🆕 NEW`, `📈 RISING`, `📉 DECLINING`, `🟢 STABLE`, or `—`.

#### `GET /api/products/{product_id}/intelligence`

Full profile for one product, built from stored data.

```json
{
  "product": "Portable Blender",
  "advertisers": ["Demo Brand", "Kitchen Co"],
  "platforms": ["Facebook", "Instagram", "TikTok"],
  "countries": ["DZ"],
  "ad_count": 3,
  "lifespan_days": 4.2,
  "creative_count": 3,
  "creative_diversity": 1.0,
  "main_angle": "Unclassified",
  "main_cta": "Shop Now",
  "offers_seen": [],
  "trend_score": 85.5
}
```

Errors: `404` `{"error": "not found"}`.

#### `GET /api/products/{product_id}/ads`

Per-ad breakdown: `{"ads": [ ... ]}`, newest first. Each item:

```json
{
  "id": 12, "ad_id": "tt1", "platform": "TikTok", "url": "https://example.com/a",
  "first_seen": "…", "last_seen": "…", "advertiser": "Demo Brand",
  "engagement": 22800.0, "lifespan_days": 1.6,
  "creative_type": "Unclassified", "angle": "Unclassified", "cta": "Shop Now",
  "offer": "", "hook": "Portable blender for smoothies", "media_type": "unknown"
}
```

Returns an empty list for an unknown product.

#### `GET /api/products/{product_id}/history`

Snapshots, oldest first (one per save): `{"history": [{"captured_at", "trend_score", "reach", "engagement", "ads_count", "platform_count"}]}`.

#### `GET /api/products/{product_id}/expansions?days=7`

Countries the product started appearing in within the last `days` days (default `7`): `{"new_markets": ["MA", "TN"]}`. Returns an empty list if the product itself is newer than `days` days.

### Competitor intelligence (database)

#### `GET /api/advertisers?query=&limit=25`

Case-insensitive name search, most ads first. With an empty `query`, lists all advertisers. `limit` defaults to `25`.

```json
{
  "advertisers": [
    { "id": 1, "name": "Demo Brand", "first_seen": "…", "last_seen": "…", "total_ads": 2 }
  ]
}
```

#### `GET /api/advertisers/{advertiser_id}`

```json
{
  "advertiser": "Demo Brand",
  "first_seen": "…",
  "last_seen": "…",
  "total_ads": 2,
  "active_ads": 2,
  "products_count": 1,
  "platforms": ["Instagram", "TikTok"],
  "countries": ["DZ"],
  "creative_variations": 2,
  "top_products": [ { "id": 1, "name": "Portable Blender", "ads_count": 2 } ],
  "top_angles": [ { "angle": "Social Proof", "count": 1 } ]
}
```

`active_ads` counts ads last confirmed within the past 14 days. There is no on/off signal from the source platforms, so recency is used as a proxy. Errors: `404` `{"error": "not found"}`.

## Database schema

Created automatically by `app/db.py` (`CREATE TABLE IF NOT EXISTS`). Timestamps are stored as ISO-8601 text (UTC).

| Table | Purpose |
|---|---|
| `products` | One row per tracked product; `tokens` is a name signature used to match products across runs (overlap ≥ 0.45). |
| `advertisers` | Unique advertiser names (matched case- and whitespace-insensitively). |
| `ads` | One row per `(ad_id, platform)`, upserted on each save. |
| `product_snapshots` | One row per product per saved run: trend score, reach, engagement, ad and platform counts. |
| `creatives` | Per-ad creative details: media type, format, angle, CTA, offer, hook. |
| `ad_countries` | Countries where each ad was confirmed active (one ad can have several). |

You can browse all of these in Supabase's Table Editor.

## Project structure

```
product_spy/
├── app/
│   ├── server.py        # Flask app: JSON API + static file serving
│   ├── core.py          # Normalization, filtering, dedup, Trend Score
│   ├── apify.py         # Apify Actor calls, retries, 7-day disk cache
│   ├── db.py            # Supabase/Postgres schema and queries
│   ├── check_db.py      # Standalone DATABASE_URL connectivity check
│   ├── io_utils.py      # Legacy file loaders (not used by the Flask server)
│   └── static/          # index.html, app.js, styles.css (frontend)
├── data/
│   ├── sample_ads.json  # Demo data for "Load sample data"
│   └── cache/           # Created at runtime: Apify result cache (git-ignored)
├── tests/test_core.py   # Unit tests for normalization and grouping
├── connectors/          # Currently empty
├── exports/             # Currently empty
├── .env.example         # Environment variable template
├── requirements.txt
├── run.sh               # macOS/Linux launcher
├── run_windows.bat      # Windows launcher
└── LICENSE
```

## Testing

The tests use [pytest](https://pytest.org), which is not in `requirements.txt`:

```bash
pip install pytest
python -m pytest
```

Run this from the project root (the tests import `app.core`). The bundled tests cover `normalize_row` (including `10K` parsing) and `build_products` cross-platform grouping. They do not need Apify or a database.

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| Top bar says no Apify token, or messages show `No Apify token configured` | `APIFY_TOKEN` is not set in the environment of the process running the server. Set it and restart. `setx` only affects new terminals. `.env` is not auto-loaded. |
| `No Supabase/Postgres connection string found` | Set `DATABASE_URL` (or `SUPABASE_DB_URL`) and restart. |
| `check_db.py` fails with a network/host error | You probably used the **Direct connection** string. Use the **Session pooler** string. |
| `password authentication failed` | Wrong database password, or special characters not URL-encoded. |
| Save / Tracked tabs return a server error | Database not reachable; run `python app/check_db.py` to see the exact error. |
| `TikTok error: …` or `Meta error: …` in the messages | The Actor failed, its schema changed, or your Apify account lacks access or credit. Check the Actor on Apify and try another Actor ID. The other source is unaffected. |
| A search returns instantly with old data | It was served from the 7-day cache. Tick **Force refresh** (or delete `data/cache/`). |
| The same product appears several times | Lower the **Duplicate similarity threshold**. |
| Unrelated products merged together | Raise the threshold. |
| Legitimate ads removed as "non-product" | The filter is keyword-based. Review **excluded ads**, or untick the exclusion option. |
| `.xls` upload fails | Install `xlrd` (`pip install xlrd`) or convert to `.xlsx`. |
| Port 8765 already in use | Stop the other process, or change `port` in `app/server.py`. |
| Browser doesn't open | Open <http://127.0.0.1:8765> manually. |

## Cost, limitations and legal notes

- **Cost:** live searches run paid/community Apify Actors, so charges depend on the Actor and the number of results. Start with 20–50 ads per source. Check the Actor's current pricing and input schema before large runs.
- **Signals, not sales:** Trend Scores reflect public ad and trend signals only. TikTok and Meta expose different fields; a missing metric (reach, spend, comments…) may simply not be published, and is stored as `0`.
- **Regional coverage:** with **Algeria (`DZ`)** selected, the app passes `DZ` to both Actors, and both Actor schemas listed `DZ` at the time of writing. Country support differs between Actors and can change. Results are regional ad intelligence, not a guarantee of sales in that country.
- **Heuristics:** creative type, angle, CTA, offer, hook and non-product filtering are keyword rules, not trained classifiers. They will miss some ads and occasionally misclassify others.
- **Data source:** only public ad-library / Creative Center data obtained through Apify. The app does not collect passwords, authentication tokens or private account data.
- **Actor changes:** Actor schemas can change. Actor IDs are editable in the sidebar so you can switch providers without changing the analysis layer.
- **Single operator:** the Apify token is read only from the server's environment and is billed to that account; there is no per-user token entry in the UI. If you plan to offer this to customers, review Apify's and the underlying platforms' terms of use (ideally with a lawyer) and make sure your own terms reflect who runs the Actors.
- **Security:** the API has no authentication and the app is designed to run locally. Do not expose it publicly without adding your own access control.

## License

Copyright (c) 2026 Sifo. All Rights Reserved. This is proprietary software: no copying, distribution, modification or use is permitted without prior written permission. See [LICENSE](LICENSE).



##Donate
If you like this project, you can support it with USDT:

Network: TRC20 (Tron)
Address:TFpnSSCCZhmdW9963GVHmwHxE9iTJixAih
