from __future__ import annotations
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

DEFAULT_TIKTOK_ACTOR = "burbn/tiktok-top-ads-spy"
DEFAULT_META_ACTOR = "webdatalabs/meta-ad-library-scraper"

# Single-operator model: the token lives only on this machine/server, in the
# environment. There is no per-customer token entry anywhere in this app —
# customers (at the manual-report stage) never touch this code at all, and
# later, in the SaaS backend, this stays the only place a token is read from.
_ENV_TOKEN_VAR = "APIFY_TOKEN"

# --- Local result cache -----------------------------------------------------
# "Collect once, reuse" starts here: identical searches (same actor + input)
# within CACHE_TTL_SECONDS are served from disk instead of re-billing Apify.
# This is intentionally a flat JSON cache, not a database — it just needs to
# survive between weekly-report runs on one machine. The real products/ads/
# creatives database (V2.2) replaces this later; it doesn't need to replace
# it today for this to be useful.
CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "cache"
CACHE_TTL_SECONDS = 7 * 24 * 3600  # 7 days — matches a weekly report cadence
_RETRYABLE_ERRORS = (TimeoutError, ConnectionError)


def _cache_key(actor_id: str, run_input: dict[str, Any]) -> str:
    payload = json.dumps({"actor": actor_id, "input": run_input}, sort_keys=True, default=str)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:24]


def _cache_read(key: str) -> list[dict[str, Any]] | None:
    path = CACHE_DIR / f"{key}.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if time.time() - payload.get("cached_at", 0) > CACHE_TTL_SECONDS:
        return None
    return payload.get("items")


def _cache_write(key: str, items: list[dict[str, Any]]) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = CACHE_DIR / f"{key}.json"
        path.write_text(json.dumps({"cached_at": time.time(), "items": items}), encoding="utf-8")
    except OSError:
        pass  # cache is a cost-saving nicety, never a hard requirement


def _client():
    """Build an Apify client using the server-side token only.

    There is no per-caller token argument anymore: at this stage there is one
    operator (you) running the app, and later, behind a real backend, the
    token still only ever lives on the server. It is never entered, stored,
    or displayed in any UI.
    """
    token = os.getenv(_ENV_TOKEN_VAR, "").strip()
    if not token:
        raise RuntimeError(
            f"No Apify token configured. Set {_ENV_TOKEN_VAR} in your .env "
            "(see .env.example) — this app no longer accepts a token from the UI."
        )
    try:
        from apify_client import ApifyClient
    except ImportError as e:
        raise RuntimeError("Install apify-client first") from e
    return ApifyClient(token)


def get_dataset(dataset_id: str) -> list[dict[str, Any]]:
    return _client().dataset(dataset_id).list_items().items


def run_actor(
    actor_id: str,
    run_input: dict[str, Any],
    timeout_secs: int = 900,
    use_cache: bool = True,
    max_retries: int = 2,
) -> list[dict[str, Any]]:
    """Run an Apify Actor and return its default dataset rows.

    Checks the local cache first (unless `use_cache=False`, e.g. an explicit
    "force refresh" from the UI), retries transient network errors a couple
    of times, and writes a successful result back to the cache.
    """
    key = _cache_key(actor_id, run_input)
    if use_cache:
        cached = _cache_read(key)
        if cached is not None:
            return cached

    client = _client()
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            result = client.actor(actor_id).call(run_input=run_input, timeout_secs=timeout_secs)
            dataset_id = result.get("defaultDatasetId") if isinstance(result, dict) else None
            if not dataset_id:
                raise RuntimeError(f"Actor {actor_id} finished without a dataset ID")
            items = client.dataset(dataset_id).list_items().items
            _cache_write(key, items)
            return items
        except _RETRYABLE_ERRORS as e:
            last_error = e
            if attempt < max_retries:
                time.sleep(2 ** attempt)  # 1s, 2s backoff
                continue
            raise
        except Exception:
            raise
    raise last_error or RuntimeError(f"Actor {actor_id} failed with no result")


def search_social_ads(
    country: str,
    keywords: list[str] | None = None,
    max_results: int = 50,
    period: str = "30",
    include_details: bool = False,
    tiktok_actor: str = DEFAULT_TIKTOK_ACTOR,
    meta_actor: str = DEFAULT_META_ACTOR,
    use_cache: bool = True,
) -> tuple[list[dict[str, Any]], list[str]]:
    """One-click search for TikTok + Meta (Facebook/Instagram) ads.

    Uses public Ad Library/Creative Center data through Apify Actors, billed
    to the server's own Apify token (see _client). Repeats of the same
    search are served from the local cache unless `use_cache=False`.
    Country is an ISO-2 code, e.g. DZ for Algeria.
    """
    country = country.strip().upper()
    terms = [x.strip() for x in (keywords or []) if x.strip()]
    messages: list[str] = []
    rows: list[dict[str, Any]] = []

    # TikTok Top Ads actor supports DZ and is designed for country-level Creative Center research.
    tik_input: dict[str, Any] = {
        "period": str(period),
        "page": 1,
        "country_code": country,
        "order_by": "ctr",
        "maxResults": int(max_results),
        "limit": min(20, int(max_results)),
    }
    if terms:
        # The Actor accepts one keyword. Run one query using the first term; users can
        # change the query in the UI. Broad country discovery is used when blank.
        tik_input["keyword"] = terms[0]
    if include_details:
        # Supported by some TikTok actors; harmless only if the configured Actor accepts it.
        # The default Actor does not require it, so we omit it there.
        pass

    try:
        tik_rows = run_actor(tiktok_actor, tik_input, use_cache=use_cache)
        for r in tik_rows:
            r = dict(r)
            r.setdefault("platform", "TikTok")
            rows.append(r)
        messages.append(f"TikTok: {len(tik_rows)} ads")
    except Exception as e:
        messages.append(f"TikTok error: {e}")

    # Meta actor supports country filtering; results include both Facebook and Instagram
    # placements in the returned platform field.
    meta_input: dict[str, Any] = {
        "searchQueries": terms or ["produit", "produits", "livraison", "achat", "promo", "offre", "منتج", "منتجات", "تخفيض", "عرض"],
        "country": country,
        "maxResults": int(max_results),
    }
    try:
        meta_rows = run_actor(meta_actor, meta_input, use_cache=use_cache)
        for r in meta_rows:
            r = dict(r)
            # Preserve Meta's placement information when available; normalization maps it.
            r.setdefault("platform", r.get("platforms", "Meta"))
            rows.append(r)
        messages.append(f"Meta (Facebook/Instagram): {len(meta_rows)} ads")
    except Exception as e:
        messages.append(f"Meta error: {e}")

    return rows, messages
