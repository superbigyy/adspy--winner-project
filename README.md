# Product Spy — Windows MVP v2

Local Streamlit product-research dashboard:

**TikTok + Facebook + Instagram → product extraction → deduplication → Trend Score 0–100 → Top Products**

## One-click Apify search

The sidebar now has a **Search TikTok + Facebook + Instagram** button. It runs two configurable Apify Actors and merges their datasets automatically:

- TikTok default: `burbn/tiktok-top-ads-spy`
- Meta default: `webdatalabs/meta-ad-library-scraper`

Both support country-level filtering; `DZ` is available for Algeria in the current Actor schemas. The Meta source returns Meta Ad Library data for Facebook/Instagram placements, while the TikTok source is TikTok Creative Center Top Ads. Check the Actor's current pricing/schema before large runs.

## Algeria

Select **🇩🇿 Algeria (DZ)**. The app passes `DZ` to both connectors. TikTok's available country editions differ by Actor; the bundled TikTok Actor explicitly lists `DZ`. Meta's current Actor schema also lists `DZ`.

This means the result is **regional ad/trend intelligence**, not a guarantee of actual Algerian sales. Public ad libraries do not expose exact commercial sales for ordinary ads.

## Setup on Windows

Requires Python 3.11+ recommended.

1. Install Python.
2. Double-click `run_windows.bat` (or run the manual commands below).
3. In the app sidebar, paste your own Apify API token under **☁️ Apify**
   (get one free at console.apify.com/account/integrations). The token is
   kept only in your browser session — it is never written to disk and is
   sent only to Apify's API. All Actor runs are billed to your own Apify
   account.
4. Select **Algeria** and press **Search TikTok + Facebook + Instagram**.

Or manually:

```powershell
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
streamlit run app\app.py
```

### For resellers / multi-customer deployments

Each customer pastes their own Apify token in the sidebar, so their Actor
usage is billed to their own Apify account rather than yours. This keeps
you positioned as a tool/interface provider rather than the party
re-distributing scraped ad-library data, which is the safer posture if you
plan to license or sell access to this dashboard. You are still
responsible for making sure your own terms of service with customers
reflect that they, not you, are the ones running the Apify Actors and
agreeing to Apify's and the underlying platforms' terms of use — review
those terms yourself (or with a lawyer) before selling this commercially.

An `APIFY_TOKEN` environment variable is still supported as a fallback for
local, single-user use (see `.env.example`), but it is not used when a
customer has entered their own token in the sidebar.

## Cost

The search runs paid/community Apify Actors. Apify charges depend on the Actor and results returned. Start with 20–50 ads per source. The UI warns that a run may incur Apify usage charges.

## Important limitations

- A high Trend Score means **stronger public ad/trend signals**, not verified sales.
- TikTok and Meta expose different fields. Missing reach, spend, comments, etc. are not automatically zero in reality; they may simply not be published.
- The project uses public ad-library/creative-center data through Apify and does not collect passwords, authentication tokens, or private account data.
- Actor schemas can change. The Actor IDs are editable in the sidebar so you can switch providers without changing the analysis layer.
