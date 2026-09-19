from __future__ import annotations
import os
from pathlib import Path

import pandas as pd
import streamlit as st
from io_utils import load_uploaded
from core import build_products, normalize_row, is_non_product_ad
from apify import get_dataset, search_social_ads, DEFAULT_TIKTOK_ACTOR, DEFAULT_META_ACTOR
import db

st.set_page_config(page_title="Product Spy", page_icon="🕵️", layout="wide")
st.title("🕵️ Product Spy")
st.caption("TikTok + Facebook + Instagram → Product Extraction → Deduplication → Trend Score")

with st.sidebar:
    st.header("🌍 Search region")

    # Countries grouped by region, so switching region narrows the country
    # list instead of scrolling one giant dropdown. All 27 European Union
    # member states are included under "European Union".
    REGION_COUNTRIES: dict[str, dict[str, str]] = {
        "🕌 MENA": {
            "🇩🇿 Algeria": "DZ", "🇲🇦 Morocco": "MA", "🇹🇳 Tunisia": "TN",
            "🇸🇦 Saudi Arabia": "SA", "🇦🇪 UAE": "AE", "🇪🇬 Egypt": "EG",
        },
        "🇪🇺 European Union": {
            "🇦🇹 Austria": "AT", "🇧🇪 Belgium": "BE", "🇧🇬 Bulgaria": "BG",
            "🇭🇷 Croatia": "HR", "🇨🇾 Cyprus": "CY", "🇨🇿 Czechia": "CZ",
            "🇩🇰 Denmark": "DK", "🇪🇪 Estonia": "EE", "🇫🇮 Finland": "FI",
            "🇫🇷 France": "FR", "🇩🇪 Germany": "DE", "🇬🇷 Greece": "GR",
            "🇭🇺 Hungary": "HU", "🇮🇪 Ireland": "IE", "🇮🇹 Italy": "IT",
            "🇱🇻 Latvia": "LV", "🇱🇹 Lithuania": "LT", "🇱🇺 Luxembourg": "LU",
            "🇲🇹 Malta": "MT", "🇳🇱 Netherlands": "NL", "🇵🇱 Poland": "PL",
            "🇵🇹 Portugal": "PT", "🇷🇴 Romania": "RO", "🇸🇰 Slovakia": "SK",
            "🇸🇮 Slovenia": "SI", "🇪🇸 Spain": "ES", "🇸🇪 Sweden": "SE",
        },
        "🌎 Other": {
            "🇬🇧 United Kingdom": "GB", "🇺🇸 United States": "US",
        },
        "🌍 Global": {"🌍 Global (all countries)": "ALL"},
    }

    region = st.selectbox("Region", list(REGION_COUNTRIES), index=0)
    country_options = REGION_COUNTRIES[region]
    selected = st.selectbox("Country", list(country_options), index=0)
    country = country_options[selected]
    st.caption("Pick a region to narrow the country list — all 27 EU member states are under 🇪🇺 European Union. For Algeria, the search uses country code DZ where the selected source supports it.")

    st.header("🔎 Product search")
    keyword_text = st.text_input(
        "Keywords (optional)",
        placeholder="مثال: skincare, chaussures, téléphone, منتج",
        help="Separate multiple keywords with commas. Leaving it blank uses broad product/commerce terms for Meta and country-level Top Ads on TikTok.",
    )
    keywords = [x.strip() for x in keyword_text.split(",") if x.strip()]
    period = st.selectbox("TikTok period", ["7", "30", "180"], index=1)
    max_results = st.slider("Max ads per source", 10, 200, 50, 10)
    include_details = st.checkbox("Request enriched details when supported", value=False)

    st.header("⚙️ Analysis")
    threshold = st.slider("Duplicate similarity threshold", 0.20, 0.70, 0.32, 0.01)
    top_n = st.number_input("Top products", 5, 100, 20)
    exclude_non_product = st.checkbox(
        "Exclude app/game/novel/adult-content ads",
        value=True,
        help="TikTok Top Ads and the Meta Ad Library surface whatever is popular in a "
             "country, including mobile apps, games, story/novel-reading apps, and "
             "adult/18+ content — not just physical products. This filters those out by "
             "keyword/URL before scoring. It's a heuristic, so uncheck it if you want to "
             "see everything raw.",
    )

    st.header("☁️ Apify")
    server_token_set = bool(os.getenv("APIFY_TOKEN", "").strip())
    if server_token_set:
        st.caption("✅ Apify token loaded from server .env — nothing to enter here.")
    else:
        st.caption(
            "⚠️ No Apify token found. Set APIFY_TOKEN in your .env file "
            "(see .env.example) and restart the app."
        )

    apify_dataset = st.text_input("Existing Dataset ID (optional)")
    tiktok_actor = st.text_input("TikTok Actor", DEFAULT_TIKTOK_ACTOR)
    meta_actor = st.text_input("Meta Actor", DEFAULT_META_ACTOR)
    force_refresh = st.checkbox(
        "Force refresh (skip cache, re-run Apify)",
        value=False,
        help="Repeats of the same country/keyword search are served from a local "
             "7-day cache by default, to avoid paying for Apify twice. Check this "
             "only when you specifically need fresh data.",
    )

    run_search = st.button(
        "🚀 Search TikTok + Facebook + Instagram",
        type="primary",
        use_container_width=True,
        disabled=not server_token_set,
    )
    uploaded = st.file_uploader("Or import CSV / JSON / JSONL / XLSX", type=["csv", "json", "jsonl", "xlsx", "xls"], accept_multiple_files=True)

ads = []

if run_search:
    if country == "ALL":
        st.warning("Global mode uses source-specific global data. For regional product research, choose a country such as Algeria (DZ).")
    with st.spinner(f"Searching {selected}… this can take a few minutes and may incur Apify usage charges."):
        searched_country = "US" if country == "ALL" else country
        raw_rows, messages = search_social_ads(
            country=searched_country,
            keywords=keywords,
            max_results=max_results,
            period=period,
            include_details=include_details,
            tiktok_actor=tiktok_actor.strip(),
            meta_actor=meta_actor.strip(),
            use_cache=not force_refresh,
        )
    for msg in messages:
        st.info(msg)
    ads = [normalize_row(x) for x in raw_rows]
    for ad in ads:
        if not ad.country:
            ad.country = searched_country

if uploaded:
    for f in uploaded:
        try:
            ads.extend(load_uploaded(f))
        except Exception as e:
            st.error(f"{f.name}: {e}")

if apify_dataset:
    try:
        ads.extend(normalize_row(x) for x in get_dataset(apify_dataset.strip()))
    except Exception as e:
        st.error(f"Apify Dataset: {e}")

if not ads:
    st.info("Choose a country, then click the search button. You can also import an exported dataset.")
    if st.button("Load sample data"):
        import json
        rows = json.loads(Path(__file__).resolve().parents[1].joinpath("data/sample_ads.json").read_text(encoding="utf-8"))
        ads = [normalize_row(x) for x in rows]

if ads:
    if exclude_non_product:
        kept, excluded = [], []
        for ad in ads:
            drop, reason = is_non_product_ad(ad.text, ad.title, ad.url)
            (excluded if drop else kept).append((ad, reason))
        ads = kept
        excluded_ads = excluded
        if excluded_ads:
            from collections import Counter
            reason_counts = Counter(r for _, r in excluded_ads)
            st.caption(
                f"🚫 Filtered out {len(excluded_ads)} non-product ad(s): "
                + ", ".join(f"{label} ({n})" for label, n in reason_counts.most_common())
            )
            with st.expander(f"See the {len(excluded_ads)} filtered-out ads (check for false positives)"):
                ex_df = pd.DataFrame(
                    [{"title": a.title or a.text[:60], "platform": a.platform, "reason": r, "url": a.url} for a, r in excluded_ads]
                )
                st.dataframe(ex_df, use_container_width=True, hide_index=True)

    st.metric("Ads loaded", len(ads))
    products = build_products(ads, threshold=threshold)
    st.subheader("🏆 Top products")
    out = pd.DataFrame(products[: int(top_n)])
    if not out.empty:
        out["platforms"] = out["platforms"].apply(lambda x: ", ".join(x))
        out["advertisers"] = out["advertisers"].apply(lambda x: ", ".join(x[:3]))
        st.dataframe(out[["product", "trend_score", "platforms", "ads", "advertisers", "reach", "engagement"]], use_container_width=True, hide_index=True)
        csv = out.to_csv(index=False).encode("utf-8-sig")
        st.download_button("Download CSV", csv, "top_products.csv", "text/csv")

        st.subheader("💾 Database")
        st.caption(
            "Saving does not re-run Apify. It stores this run's products/ads locally "
            "so the same product found again next week updates its history instead "
            "of duplicating — this is what later powers rising/declining trends."
        )
        if st.button("Save this run to database"):
            summary = db.save_run(products)
            st.success(
                f"Products: {summary['products_new']} new, {summary['products_matched']} matched to existing · "
                f"Ads: {summary['ads_new']} new, {summary['ads_updated']} updated · "
                f"Creatives: {summary['creatives_new']} new"
            )
            breakdown = db.creative_type_breakdown()
            if breakdown:
                st.caption("Creative types across the whole database (rough keyword heuristic, not a real classifier yet):")
                st.dataframe(pd.DataFrame(breakdown), use_container_width=True, hide_index=True)

        st.subheader("🔗 Most-engaging ad link per product")
        LINK_LABELS = {
            "landing": "🔗 Product/landing page",
            "library": "📚 Meta Ad Library entry (no landing page disclosed)",
            "page": "📄 Advertiser's page",
            "video": "🎬 Ad video/creative link",
        }
        for p in products[: int(top_n)]:
            top_ad = p.get("top_ad")
            if not top_ad:
                continue
            header = (
                f"**{p['product']}** — [{top_ad['platform']}] {top_ad['advertiser']} "
                f"(engagement: {int(top_ad['engagement'])})"
            )
            url = top_ad.get("url")
            url_type = top_ad.get("url_type", "none")
            if url:
                label = LINK_LABELS.get(url_type, "🔗 Link")
                st.markdown(f"{header}  \n{label}: [{url}]({url})")
            else:
                st.markdown(f"{header}  \n_no link available for this ad_")

    st.subheader("Score components")
    st.write("Reach 33% • Engagement 24% • Ad repetition 18% • Cross-platform presence 15% • Persistence 10%.")
    st.caption("Trend Score is an internal ranking signal, not proof of actual sales. Regional accuracy depends on what each source publishes for the selected country.")

st.divider()
st.subheader("📚 Tracked products (database)")
st.caption("Every product ever saved, across all past runs — not just this search.")
tracked = db.top_products(limit=100)
if tracked:
    tdf = pd.DataFrame(tracked)
    tdf["days_tracked"] = tdf["days_tracked"].round(1)
    tdf["first_seen"] = pd.to_datetime(tdf["first_seen"]).dt.strftime("%Y-%m-%d")
    tdf["last_seen"] = pd.to_datetime(tdf["last_seen"]).dt.strftime("%Y-%m-%d")
    st.dataframe(
        tdf[["name", "trend_score", "reach", "engagement", "times_tracked", "days_tracked", "first_seen", "last_seen"]]
        .rename(columns={"name": "product", "times_tracked": "times seen"}),
        use_container_width=True, hide_index=True,
    )
else:
    st.info("No products saved yet — run a search above and click \"Save this run to database\".")

st.divider()
st.subheader("🔍 Ad Intelligence")
st.caption(
    "Per-product profile built from the database — lifespan, main angle, CTA and offers. "
    "Runs no new Apify calls; uses what's already saved."
)
if tracked:
    id_by_name = {row["name"]: row["id"] for row in tracked}
    chosen_name = st.selectbox("Pick a tracked product", list(id_by_name.keys()))
    if chosen_name:
        profile = db.product_intelligence(id_by_name[chosen_name])
        if profile:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Trend Score", f"{profile['trend_score']:.0f}/100" if profile["trend_score"] is not None else "—")
            c2.metric("Ad lifespan", f"{profile['lifespan_days']:.0f} days")
            c3.metric("Creatives", profile["creative_count"])
            c4.metric("Platforms", len(profile["platforms"]))

            st.markdown(
                f"**Advertisers:** {', '.join(profile['advertisers']) or '—'}  \n"
                f"**Countries/Platforms:** {', '.join(profile['platforms']) or '—'}  \n"
                f"**Main angle:** {profile['main_angle']} &nbsp;|&nbsp; **Main CTA:** {profile['main_cta'] or '—'}  \n"
                f"**Offers seen:** {', '.join(profile['offers_seen']) or 'None detected'}"
            )
            st.caption(
                "Angle/CTA/offer/creative_type are a keyword heuristic (see core.py), not a trained "
                "classifier — treat them as a rough first read, not ground truth."
            )

            st.markdown("**Per-ad breakdown**")
            ads_df = pd.DataFrame(db.ad_intelligence(id_by_name[chosen_name]))
            if not ads_df.empty:
                ads_df["lifespan_days"] = ads_df["lifespan_days"].round(1)
                st.dataframe(
                    ads_df[["platform", "advertiser", "lifespan_days", "creative_type", "angle", "cta", "offer", "engagement", "url"]],
                    use_container_width=True, hide_index=True,
                )
else:
    st.caption("Save at least one search to the database to see Ad Intelligence profiles.")

st.divider()
st.subheader("🏢 Competitor Intelligence")
st.caption(
    "Search any advertiser name saved in the database — active vs total ads, products, "
    "countries, creative variations, and their most-used messaging angles. Built from "
    "what's already saved; runs no new Apify calls."
)
brand_query = st.text_input("Search brand / advertiser name", placeholder="e.g. Brand X")
matches = db.search_advertisers(brand_query, limit=25) if brand_query.strip() else db.list_advertisers(limit=25)
if not matches:
    st.info("No advertisers saved yet — run and save a search above first.")
else:
    label_by_id = {f"{m['name']} ({m['total_ads']} ads)": m["id"] for m in matches}
    chosen_label = st.selectbox(
        "Matching advertisers" if brand_query.strip() else "All tracked advertisers", list(label_by_id.keys())
    )
    if chosen_label:
        cp = db.advertiser_profile(label_by_id[chosen_label])
        if cp:
            st.markdown(f"### {cp['advertiser']}")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Active Ads", cp["active_ads"], help=f"Seen in the last {db.ACTIVE_WINDOW_DAYS} days")
            c2.metric("Total Ads", cp["total_ads"])
            c3.metric("Products", cp["products_count"])
            c4.metric("Creative Variations", cp["creative_variations"])

            st.markdown(
                f"**Countries:** {', '.join(cp['countries']) or '—'}  \n"
                f"**Platforms:** {', '.join(cp['platforms']) or '—'}  \n"
                f"**Tracked since:** {cp['first_seen'][:10]} · **Last seen:** {cp['last_seen'][:10]}"
            )

            col_a, col_b = st.columns(2)
            with col_a:
                st.markdown("**Top Products**")
                if cp["top_products"]:
                    st.dataframe(
                        pd.DataFrame(cp["top_products"])[["name", "ads_count"]].rename(columns={"name": "product"}),
                        use_container_width=True, hide_index=True,
                    )
                else:
                    st.caption("No products linked yet.")
            with col_b:
                st.markdown("**Top Creative Angles**")
                if cp["top_angles"]:
                    st.dataframe(pd.DataFrame(cp["top_angles"]), use_container_width=True, hide_index=True)
                else:
                    st.caption("No classified angles yet (or all Unclassified).")
