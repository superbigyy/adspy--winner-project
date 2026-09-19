"""SQLite persistence for Product Spy (V2.2 — Ad Database).

Turns the output of core.build_products() from a one-off, in-memory result
into a growing local database, so:
  - the same product found in two different weekly searches is recognized as
    ONE product (matched by name similarity), not duplicated;
  - the same ad (same ad_id + platform) is stored once and just updated,
    instead of re-inserted every run;
  - every run leaves a snapshot row, so later we can answer "has this
    product's trend score been rising over the last 3 weeks?" (V2.5) without
    redesigning anything — the history is already being collected.

This is intentionally a flat SQLite file (stdlib `sqlite3`, no new
dependency), matching the "close to zero cost" principle. It gets replaced
by PostgreSQL only when/if the SaaS backend stage actually needs it.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core import NormalizedAd, tokenize, guess_creative_type, guess_angle, extract_hook, extract_cta, extract_offer, guess_media_type

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "product_spy.db"

# Two product names are considered the "same" product across runs when the
# overlap between their token sets is at least this high. Deliberately a bit
# stricter than build_products()'s default 0.32 ad-to-ad threshold, because
# here we're matching short product *names* against other product *names*,
# not full ad text against ad text — short strings overlap more easily by
# chance, so the bar needs to be a little higher to avoid false merges.
PRODUCT_MATCH_THRESHOLD = 0.45


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_connection(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS advertisers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            tokens TEXT NOT NULL,          -- space-joined signature used for cross-run matching
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS ads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ad_id TEXT NOT NULL,
            platform TEXT NOT NULL,
            product_id INTEGER NOT NULL REFERENCES products(id),
            advertiser_id INTEGER REFERENCES advertisers(id),
            url TEXT,
            url_type TEXT,
            title TEXT,
            ad_text TEXT,
            started_at TEXT,
            impressions REAL DEFAULT 0,
            likes REAL DEFAULT 0,
            comments REAL DEFAULT 0,
            shares REAL DEFAULT 0,
            views REAL DEFAULT 0,
            spend REAL DEFAULT 0,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            UNIQUE(ad_id, platform)
        );

        CREATE TABLE IF NOT EXISTS product_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL REFERENCES products(id),
            captured_at TEXT NOT NULL,
            trend_score REAL,
            reach REAL,
            engagement REAL,
            ads_count INTEGER,
            platform_count INTEGER,
            persistence_ads INTEGER
        );

        CREATE TABLE IF NOT EXISTS creatives (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ad_id INTEGER NOT NULL REFERENCES ads(id),
            creative_url TEXT,
            media_type TEXT,        -- 'video' | 'image' | 'unknown'
            creative_type TEXT,     -- FORMAT heuristic, see core.guess_creative_type (V2.3 replaces this)
            angle TEXT,             -- MESSAGING heuristic, see core.guess_angle (V2.3 replaces this)
            cta TEXT,
            offer TEXT,
            hook TEXT,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            UNIQUE(ad_id, creative_url)
        );

        -- V2.4: one ad can be confirmed active in several countries (rediscovered
        -- by different country searches) — a single column on `ads` would lose
        -- every country but the most recent, so this is a proper join table.
        CREATE TABLE IF NOT EXISTS ad_countries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ad_id INTEGER NOT NULL REFERENCES ads(id),
            country TEXT NOT NULL,     -- ISO-2, e.g. 'DZ'
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            UNIQUE(ad_id, country)
        );

        CREATE INDEX IF NOT EXISTS idx_ads_product ON ads(product_id);
        CREATE INDEX IF NOT EXISTS idx_ads_advertiser ON ads(advertiser_id);
        CREATE INDEX IF NOT EXISTS idx_snapshots_product ON product_snapshots(product_id, captured_at);
        CREATE INDEX IF NOT EXISTS idx_creatives_ad ON creatives(ad_id);
        CREATE INDEX IF NOT EXISTS idx_ad_countries_ad ON ad_countries(ad_id);
        """
    )
    # Migration for databases created before angle/cta/offer existed —
    # ALTER TABLE ... ADD COLUMN has no "IF NOT EXISTS" in SQLite, so check first.
    existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(creatives)")}
    for col in ("angle", "cta", "offer"):
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE creatives ADD COLUMN {col} TEXT")
    conn.commit()


def _match_product(conn: sqlite3.Connection, name: str) -> int | None:
    """Find an existing product whose name-token signature overlaps enough
    with `name` to be considered the same product. Returns its id, or None."""
    incoming = tokenize(name)
    if not incoming:
        return None
    best_id, best_score = None, 0.0
    for row in conn.execute("SELECT id, tokens FROM products"):
        existing = set(row["tokens"].split())
        if not existing:
            continue
        score = len(incoming & existing) / max(1, len(incoming | existing))
        if score > best_score:
            best_id, best_score = row["id"], score
    if best_score >= PRODUCT_MATCH_THRESHOLD:
        return best_id
    return None


def _get_or_create_product(conn: sqlite3.Connection, name: str, when: str) -> int:
    existing_id = _match_product(conn, name)
    if existing_id is not None:
        conn.execute(
            "UPDATE products SET last_seen = ?, name = ? WHERE id = ? AND ? > last_seen",
            (when, name if len(name) > 0 else name, existing_id, when),
        )
        return existing_id
    tokens = " ".join(sorted(tokenize(name)))
    cur = conn.execute(
        "INSERT INTO products (name, tokens, first_seen, last_seen) VALUES (?, ?, ?, ?)",
        (name, tokens, when, when),
    )
    return cur.lastrowid


def _get_or_create_advertiser(conn: sqlite3.Connection, name: str, when: str) -> int | None:
    name = (name or "").strip()
    if not name:
        return None
    # Better advertiser identification (V2.1): match case/whitespace-insensitively
    # so "Brand X", "brand x", "  Brand X " don't become three separate advertisers.
    # The first-seen casing is kept as the canonical display name.
    norm = re.sub(r"\s+", " ", name).strip().lower()
    row = conn.execute(
        "SELECT id FROM advertisers WHERE lower(trim(name)) = ?", (norm,)
    ).fetchone()
    if row:
        conn.execute("UPDATE advertisers SET last_seen = ? WHERE id = ? AND ? > last_seen", (when, row["id"], when))
        return row["id"]
    cur = conn.execute(
        "INSERT INTO advertisers (name, first_seen, last_seen) VALUES (?, ?, ?)",
        (name, when, when),
    )
    return cur.lastrowid


def _upsert_ad(conn: sqlite3.Connection, ad: NormalizedAd, product_id: int, advertiser_id: int | None, when: str) -> tuple[int, bool]:
    """Insert the ad, or update it if already known. Returns (ad_row_id, is_new)."""
    existing = conn.execute(
        "SELECT id FROM ads WHERE ad_id = ? AND platform = ?", (ad.ad_id, ad.platform)
    ).fetchone()
    if existing:
        conn.execute(
            """UPDATE ads SET product_id=?, advertiser_id=?, url=?, url_type=?, title=?, ad_text=?,
               impressions=?, likes=?, comments=?, shares=?, views=?, spend=?, last_seen=?
               WHERE id=?""",
            (product_id, advertiser_id, ad.url, ad.url_type, ad.title, ad.text,
             ad.impressions, ad.likes, ad.comments, ad.shares, ad.views, ad.spend, when, existing["id"]),
        )
        return existing["id"], False
    cur = conn.execute(
        """INSERT INTO ads (ad_id, platform, product_id, advertiser_id, url, url_type, title, ad_text,
           started_at, impressions, likes, comments, shares, views, spend, first_seen, last_seen)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (ad.ad_id, ad.platform, product_id, advertiser_id, ad.url, ad.url_type, ad.title, ad.text,
         ad.started_at, ad.impressions, ad.likes, ad.comments, ad.shares, ad.views, ad.spend, when, when),
    )
    return cur.lastrowid, True


def _upsert_ad_country(conn: sqlite3.Connection, ad_row_id: int, country: str, when: str) -> None:
    """Record that this ad was confirmed active in `country` at `when`.
    Additive only — an ad already known in DZ that's now also found in MA
    ends up with two rows, not one overwritten row."""
    country = (country or "").strip().upper()
    if not country:
        return
    existing = conn.execute(
        "SELECT id FROM ad_countries WHERE ad_id = ? AND country = ?", (ad_row_id, country)
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE ad_countries SET last_seen = ? WHERE id = ? AND ? > last_seen",
            (when, existing["id"], when),
        )
        return
    conn.execute(
        "INSERT INTO ad_countries (ad_id, country, first_seen, last_seen) VALUES (?,?,?,?)",
        (ad_row_id, country, when, when),
    )


def _upsert_creative(conn: sqlite3.Connection, ad_row_id: int, ad: NormalizedAd, when: str) -> None:
    """One creative row per (ad, creative_url) — most ads have exactly one
    creative today; the schema allows several per ad for when an advertiser
    later runs multiple video variations under the same ad_id."""
    if not ad.url:
        return
    existing = conn.execute(
        "SELECT id FROM creatives WHERE ad_id = ? AND creative_url = ?", (ad_row_id, ad.url)
    ).fetchone()
    copy = ad.text or ad.title
    media_type = guess_media_type(ad.url, ad.url_type)
    creative_type = guess_creative_type(copy)
    angle = guess_angle(copy)
    cta = extract_cta(copy)
    offer = extract_offer(copy)
    hook = extract_hook(copy)
    if existing:
        conn.execute(
            """UPDATE creatives SET media_type=?, creative_type=?, angle=?, cta=?, offer=?, hook=?, last_seen=?
               WHERE id=?""",
            (media_type, creative_type, angle, cta, offer, hook, when, existing["id"]),
        )
        return
    conn.execute(
        """INSERT INTO creatives (ad_id, creative_url, media_type, creative_type, angle, cta, offer, hook, first_seen, last_seen)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (ad_row_id, ad.url, media_type, creative_type, angle, cta, offer, hook, when, when),
    )


def save_run(products: list[dict[str, Any]], conn: sqlite3.Connection | None = None) -> dict[str, int]:
    """Persist one build_products() run: match/create products, upsert their
    ads, and record one snapshot per (database) product for this point in time.

    Note: build_products() groups ads by strict text similarity, so it can
    sometimes split what is really one product into two same-named groups
    (e.g. two very differently-worded ads for the same item). Those groups
    get matched to the SAME database product here (name matching is looser
    on purpose), so their metrics are combined into a single snapshot rather
    than recorded twice — otherwise "times tracked" would over-count.

    Returns a small summary dict for the UI: counts of new vs matched
    products, new vs updated ads, and new creatives.
    """
    owns_conn = conn is None
    conn = conn or get_connection()
    when = _now()
    summary = {"products_new": 0, "products_matched": 0, "ads_new": 0, "ads_updated": 0, "creatives_new": 0}
    # product_id -> combined metrics for this run, written as ONE snapshot each at the end
    pending_snapshots: dict[int, dict[str, float]] = {}
    try:
        for p in products:
            existing_id = _match_product(conn, p["product"])
            product_id = _get_or_create_product(conn, p["product"], when)
            summary["products_matched" if existing_id is not None else "products_new"] += 1

            for ad in p.get("ads_detail", []):
                advertiser_id = _get_or_create_advertiser(conn, ad.advertiser, when)
                ad_row_id, is_new = _upsert_ad(conn, ad, product_id, advertiser_id, when)
                summary["ads_new" if is_new else "ads_updated"] += 1
                _upsert_ad_country(conn, ad_row_id, ad.country, when)
                had_creative = conn.execute(
                    "SELECT 1 FROM creatives WHERE ad_id = ? AND creative_url = ?", (ad_row_id, ad.url)
                ).fetchone()
                _upsert_creative(conn, ad_row_id, ad, when)
                if not had_creative and ad.url:
                    summary["creatives_new"] += 1

            agg = pending_snapshots.setdefault(product_id, {
                "reach": 0.0, "engagement": 0.0, "ads_count": 0,
                "platform_count": 0, "persistence_ads": 0,
                "trend_weighted": 0.0, "weight": 0.0,
            })
            n_ads = p.get("ads", 0) or 0
            agg["reach"] += p.get("reach") or 0
            agg["engagement"] += p.get("engagement") or 0
            agg["ads_count"] += n_ads
            agg["platform_count"] = max(agg["platform_count"], p.get("platform_count") or 0)
            agg["persistence_ads"] += p.get("persistence_ads") or 0
            agg["trend_weighted"] += (p.get("trend_score") or 0) * max(1, n_ads)
            agg["weight"] += max(1, n_ads)

        for product_id, agg in pending_snapshots.items():
            trend_score = agg["trend_weighted"] / agg["weight"] if agg["weight"] else None
            conn.execute(
                """INSERT INTO product_snapshots
                   (product_id, captured_at, trend_score, reach, engagement, ads_count, platform_count, persistence_ads)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (product_id, when, trend_score, agg["reach"], agg["engagement"],
                 agg["ads_count"], agg["platform_count"], agg["persistence_ads"]),
            )
        conn.commit()
    finally:
        if owns_conn:
            conn.close()
    return summary


def top_products(limit: int = 50, conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    """Latest known state of every tracked product, most recent snapshot's
    trend_score first, plus how long each has been tracked (first_seen →
    last_seen) — the seed of V2.5's history view."""
    owns_conn = conn is None
    conn = conn or get_connection()
    try:
        rows = conn.execute(
            """
            SELECT pr.id, pr.name, pr.first_seen, pr.last_seen,
                   s.trend_score, s.reach, s.engagement, s.ads_count, s.platform_count,
                   (SELECT COUNT(*) FROM product_snapshots WHERE product_id = pr.id) AS times_tracked,
                   CAST(julianday(pr.last_seen) - julianday(pr.first_seen) AS REAL) AS days_tracked
            FROM products pr
            JOIN product_snapshots s ON s.id = (
                SELECT id FROM product_snapshots WHERE product_id = pr.id ORDER BY captured_at DESC LIMIT 1
            )
            ORDER BY s.trend_score DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        if owns_conn:
            conn.close()


def creative_type_breakdown(conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    """Count of stored creatives per creative_type, most common first —
    a first, rough answer to "what creative formats are advertisers using?"
    (V2.3's real question), built on today's keyword heuristic."""
    owns_conn = conn is None
    conn = conn or get_connection()
    try:
        rows = conn.execute(
            "SELECT creative_type, COUNT(*) AS count FROM creatives GROUP BY creative_type ORDER BY count DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        if owns_conn:
            conn.close()


def ad_intelligence(product_id: int, conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    """V2.3: per-ad breakdown for one product — platform, advertiser, how
    long the ad has been running (first_seen → last_seen, in days), and its
    creative's format/angle/CTA/offer/hook. This is the row-level data the
    product-level summary below (product_intelligence) is built from."""
    owns_conn = conn is None
    conn = conn or get_connection()
    try:
        rows = conn.execute(
            """
            SELECT a.id, a.ad_id, a.platform, a.url, a.first_seen, a.last_seen,
                   COALESCE(adv.name, 'Unknown') AS advertiser,
                   (a.likes + 2*a.comments + 3*a.shares) AS engagement,
                   CAST(julianday(a.last_seen) - julianday(a.first_seen) AS REAL) AS lifespan_days,
                   c.creative_type, c.angle, c.cta, c.offer, c.hook, c.media_type
            FROM ads a
            LEFT JOIN advertisers adv ON adv.id = a.advertiser_id
            LEFT JOIN creatives c ON c.ad_id = a.id
            WHERE a.product_id = ?
            ORDER BY a.last_seen DESC
            """,
            (product_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        if owns_conn:
            conn.close()


def product_intelligence(product_id: int, conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
    """V2.3: one product's full intelligence profile — the same shape as the
    PRODUCT / ADVERTISER / COUNTRIES / AD LIFESPAN / MAIN ANGLE / CTA / TREND
    / SCORE example from the project's own vision doc, built entirely from
    what's already in the database (no new Apify calls)."""
    owns_conn = conn is None
    conn = conn or get_connection()
    try:
        product = conn.execute(
            "SELECT id, name, first_seen, last_seen FROM products WHERE id = ?", (product_id,)
        ).fetchone()
        if not product:
            return None
        ads = ad_intelligence(product_id, conn)
        latest_snap = conn.execute(
            "SELECT trend_score FROM product_snapshots WHERE product_id = ? ORDER BY captured_at DESC LIMIT 1",
            (product_id,),
        ).fetchone()

        def _mode(values: list[str]) -> str:
            values = [v for v in values if v and v != "Unclassified"]
            if not values:
                return "Unclassified"
            return max(set(values), key=values.count)

        distinct_types = {a["creative_type"] for a in ads if a["creative_type"]}
        lifespan_days = max((a["lifespan_days"] or 0) for a in ads) if ads else 0.0
        countries = conn.execute(
            """SELECT DISTINCT ac.country FROM ad_countries ac
               JOIN ads a ON a.id = ac.ad_id WHERE a.product_id = ? ORDER BY ac.country""",
            (product_id,),
        ).fetchall()
        return {
            "product": product["name"],
            "advertisers": sorted({a["advertiser"] for a in ads if a["advertiser"] and a["advertiser"] != "Unknown"}),
            "platforms": sorted({a["platform"] for a in ads if a["platform"]}),
            "countries": [r["country"] for r in countries],
            "ad_count": len(ads),
            "lifespan_days": round(lifespan_days, 1),
            "creative_count": len({a["url"] for a in ads if a["url"]}),
            "creative_diversity": round(len(distinct_types) / max(1, len({a["creative_type"] for a in ads})), 2) if ads else 0,
            "main_angle": _mode([a["angle"] for a in ads]),
            "main_cta": _mode([a["cta"] for a in ads]),
            "offers_seen": sorted({a["offer"] for a in ads if a["offer"]}),
            "trend_score": latest_snap["trend_score"] if latest_snap else None,
        }
    finally:
        if owns_conn:
            conn.close()


# --- V2.4: Competitor Intelligence ------------------------------------------
# "Active" here means an advertiser's ad was last confirmed running within
# this many days — we have no direct on/off signal from the source platforms,
# so recency of last_seen is the best available proxy. Same caveat as every
# other heuristic in this file: a real "status" field from the source (if one
# becomes available) should replace this.
ACTIVE_WINDOW_DAYS = 14


def search_advertisers(query: str, limit: int = 20, conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    """Brand search: advertisers whose name contains `query` (case-insensitive
    for ASCII, SQLite's default), most total ads first."""
    owns_conn = conn is None
    conn = conn or get_connection()
    try:
        rows = conn.execute(
            """
            SELECT adv.id, adv.name, adv.first_seen, adv.last_seen,
                   (SELECT COUNT(*) FROM ads WHERE advertiser_id = adv.id) AS total_ads
            FROM advertisers adv
            WHERE adv.name LIKE ?
            ORDER BY total_ads DESC
            LIMIT ?
            """,
            (f"%{query.strip()}%", limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        if owns_conn:
            conn.close()


def list_advertisers(limit: int = 50, conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    """All known advertisers, most total ads first — for browsing without a
    search term (e.g. an initial competitor-list view)."""
    return search_advertisers("", limit=limit, conn=conn)


def advertiser_profile(advertiser_id: int, active_days: int = ACTIVE_WINDOW_DAYS, conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
    """V2.4: one competitor's full profile — active/total ads, products,
    countries, creative variations, top products, and top messaging angles.
    Matches the project's own vision-doc example (Brand X / Active Ads /
    Products / Countries / Creative Variations / Top Products / Top Angles),
    built entirely from what's already in the database."""
    owns_conn = conn is None
    conn = conn or get_connection()
    try:
        adv = conn.execute(
            "SELECT id, name, first_seen, last_seen FROM advertisers WHERE id = ?", (advertiser_id,)
        ).fetchone()
        if not adv:
            return None

        active_cutoff = (datetime.now(timezone.utc) - timedelta(days=active_days)).isoformat()

        total_ads = conn.execute(
            "SELECT COUNT(*) AS n FROM ads WHERE advertiser_id = ?", (advertiser_id,)
        ).fetchone()["n"]
        active_ads = conn.execute(
            "SELECT COUNT(*) AS n FROM ads WHERE advertiser_id = ? AND last_seen >= ?",
            (advertiser_id, active_cutoff),
        ).fetchone()["n"]
        products_count = conn.execute(
            "SELECT COUNT(DISTINCT product_id) AS n FROM ads WHERE advertiser_id = ?", (advertiser_id,)
        ).fetchone()["n"]
        platforms = [r["platform"] for r in conn.execute(
            "SELECT DISTINCT platform FROM ads WHERE advertiser_id = ? ORDER BY platform", (advertiser_id,)
        )]
        countries = [r["country"] for r in conn.execute(
            """SELECT DISTINCT ac.country FROM ad_countries ac
               JOIN ads a ON a.id = ac.ad_id WHERE a.advertiser_id = ? ORDER BY ac.country""",
            (advertiser_id,),
        )]
        creative_variations = conn.execute(
            """SELECT COUNT(DISTINCT c.creative_url) AS n FROM creatives c
               JOIN ads a ON a.id = c.ad_id WHERE a.advertiser_id = ?""",
            (advertiser_id,),
        ).fetchone()["n"]
        top_products = [dict(r) for r in conn.execute(
            """SELECT p.id, p.name, COUNT(*) AS ads_count
               FROM ads a JOIN products p ON p.id = a.product_id
               WHERE a.advertiser_id = ? GROUP BY p.id ORDER BY ads_count DESC LIMIT 5""",
            (advertiser_id,),
        )]
        top_angles = [dict(r) for r in conn.execute(
            """SELECT c.angle, COUNT(*) AS count FROM creatives c
               JOIN ads a ON a.id = c.ad_id
               WHERE a.advertiser_id = ? AND c.angle IS NOT NULL AND c.angle != 'Unclassified'
               GROUP BY c.angle ORDER BY count DESC LIMIT 5""",
            (advertiser_id,),
        )]

        return {
            "advertiser": adv["name"],
            "first_seen": adv["first_seen"],
            "last_seen": adv["last_seen"],
            "total_ads": total_ads,
            "active_ads": active_ads,
            "products_count": products_count,
            "platforms": platforms,
            "countries": countries,
            "creative_variations": creative_variations,
            "top_products": top_products,
            "top_angles": top_angles,
        }
    finally:
        if owns_conn:
            conn.close()
