from __future__ import annotations

import re
import math
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

PLATFORM_ALIASES = {
    'tiktok': 'TikTok', 'tiktok shop': 'TikTok',
    'facebook': 'Facebook', 'meta': 'Facebook',
    'instagram': 'Instagram', 'ig': 'Instagram',
}

STOPWORDS = set('the a an and or for to of in on with from this that your you our new best sale buy shop free'.split())

# --- Non-product ad filtering ------------------------------------------------
# TikTok's "Top Ads" and Meta's Ad Library return whatever is popular in a
# country, not just physical/e-commerce products — mobile apps, games,
# story/novel-reading apps, and (especially on Meta, which is less strictly
# moderated than TikTok) adult/18+ content are all common there and would
# otherwise pollute build_products(). This is a keyword + URL heuristic, not
# a trained classifier: it will miss some ads and occasionally flag a
# legitimate product ad that happens to share wording with an excluded
# category — reason_label lets the UI show what was excluded and why, so
# miscalls are easy to spot and the keyword lists easy to adjust.
_EXCLUDED_URL_DOMAINS = (
    # app install / deep-link redirectors
    'apps.apple.com', 'itunes.apple.com', 'play.google.com',
    'onelink.me', 'app.link', 'bnc.lt', 'adj.st', 'adjust.com',
    # adult content platforms
    'onlyfans.com', 'chaturbate.com', 'livejasmin.com', 'stripchat.com',
    'bongacams.com', 'xvideos.com', 'xnxx.com', 'pornhub.com',
)

_NON_PRODUCT_RULES: list[tuple[str, tuple[str, ...]]] = [
    ('Mobile/Web App', (
        'download the app', 'install now', 'get the app', 'open in app',
        'available on the app store', 'get it on google play', 'app store', 'play store',
        'application mobile', "téléchargez l'application", 'télécharger l\u2019application',
        'حمل التطبيق', 'نزّل التطبيق', 'نزل التطبيق', 'تطبيق مجاني', 'قم بتنزيل التطبيق',
    )),
    ('Story/Novel App', (
        'read the full story', 'continue reading', 'read now', 'read the ending',
        'webtoon', 'manga', 'audiobook', 'new chapter', 'read chapter',
        'رواية', 'روايات', 'قصة كاملة', 'اقرأ القصة', 'اقرأ الآن', 'قصص حصرية', 'فصل جديد',
    )),
    ('Mobile Game', (
        'play now', 'download the game', 'play this game', 'idle game',
        'strategy game', 'puzzle game', 'match-3',
        'لعبة', 'العب الآن', 'حمل اللعبة', 'نزل اللعبة',
    )),
    ('Adult/18+ Content', (
        '18+', 'xxx', 'nsfw', 'adults only', 'adult content', 'adult dating',
        'cam girls', 'live cams', 'hot singles near you', 'meet singles tonight',
        'hookup tonight', 'sexy singles', 'onlyfans',
        'للبالغين فقط', 'محتوى للكبار', 'دردشة كبار', 'تعارف كبار',
    )),
]


def is_non_product_ad(text: str, title: str, url: str) -> tuple[bool, str]:
    """Returns (should_exclude, reason_label). reason_label is one of the
    _NON_PRODUCT_RULES labels, 'App store / deep link URL', or '' when kept."""
    u = (url or '').lower()
    if any(d in u for d in _EXCLUDED_URL_DOMAINS):
        return True, 'App store / deep link URL' if 'apps.apple.com' in u or 'play.google.com' in u or 'onelink' in u or 'app.link' in u or 'adjust' in u or 'bnc.lt' in u else 'Adult/18+ Content'
    s = f"{text or ''} {title or ''}".lower()
    for label, keywords in _NON_PRODUCT_RULES:
        if any(k in s for k in keywords):
            return True, label
    return False, ''

# V2.1/V2.2: lightweight keyword heuristic for creative_type — NOT a real
# classifier, just a rough first pass so the creatives table isn't empty
# while it waits for a proper model in V2.3. Order matters: first match wins,
# most-specific patterns first.
_CREATIVE_TYPE_RULES: list[tuple[str, tuple[str, ...]]] = [
    ('Unboxing', ('unbox', 'unboxing')),
    ('Before/After', ('before and after', 'before & after', 'before/after')),
    ('Testimonial', ('testimonial', 'customer review', 'verified buyer', 'i bought this', "i've been using")),
    ('Comparison', (' vs ', 'versus', 'compared to', 'better than')),
    ('Founder Video', ('founder', 'ceo of', 'i started this company', 'i created this')),
    ('Offer/Discount', ('% off', 'discount', 'promo code', 'limited offer', 'sale ends', 'coupon')),
    ('Problem/Solution', ('tired of', 'struggling with', 'stop wasting', 'sick of', 'finally a solution')),
    ('UGC', ('pov:', 'pov ', 'storytime', 'get ready with me', 'day in my life')),
]


def guess_creative_type(text: str) -> str:
    """Best-effort creative_type label from ad copy, using keyword rules.
    Returns 'Unclassified' when nothing matches — a real classifier (V2.3)
    replaces this; this just keeps the creatives table populated meanwhile."""
    s = (text or '').lower()
    for label, keywords in _CREATIVE_TYPE_RULES:
        if any(k in s for k in keywords):
            return label
    return 'Unclassified'


# "angle" is the messaging strategy, distinct from creative_type (the
# *format*). An ad can be a Testimonial (format) built on a Social Proof
# angle, for instance. Same caveat as creative_type: keyword heuristic only.
_ANGLE_RULES: list[tuple[str, tuple[str, ...]]] = [
    ('Urgency/Scarcity', ('limited time', 'only today', 'last chance', 'while supplies last', 'selling fast', 'almost sold out', 'ends soon')),
    ('Social Proof', ('thousands of', 'bestseller', 'best seller', '#1', 'rated', 'reviews', 'customers love', 'join over')),
    ('Problem/Solution', ('tired of', 'struggling with', 'stop wasting', 'sick of', 'finally a solution', 'say goodbye to')),
    ('Curiosity/Hook', ("you won't believe", 'this is why', 'the secret', 'nobody tells you', "here's why")),
    ('Direct Offer', ('% off', 'discount', 'free shipping', 'buy 1 get', 'bundle', 'today only')),
]


def guess_angle(text: str) -> str:
    s = (text or '').lower()
    for label, keywords in _ANGLE_RULES:
        if any(k in s for k in keywords):
            return label
    return 'Unclassified'


# CTA phrases, checked as whole phrases (order = most-specific first),
# across the languages this project's ads are most likely to appear in.
_CTA_PHRASES = [
    'shop now', 'buy now', 'order now', 'get yours', 'get it now', 'learn more',
    'sign up now', 'sign up', 'install now', 'download now', 'swipe up', 'book now',
    'contact us', 'send message', 'commander maintenant', 'acheter maintenant',
    'profitez-en', 'en savoir plus', 'اطلب الآن', 'تسوق الآن', 'احصل عليه الآن',
]


def extract_cta(text: str) -> str:
    """Best-effort call-to-action phrase found in the ad copy."""
    s = (text or '').lower()
    for phrase in _CTA_PHRASES:
        if phrase in s:
            return phrase.title()
    return ''


_OFFER_PATTERN = re.compile(
    r'(\d{1,3}\s?%\s?(?:off|discount|r\u00e9duction)|buy\s?1\s?get\s?1|bogo|free shipping|'
    r'livraison gratuite|promo\s?code|coupon|bundle\s?deal)',
    re.I,
)


def extract_offer(text: str) -> str:
    """Best-effort offer/promotion snippet (e.g. '20% off') found in the ad
    copy, or '' if nothing in this narrow pattern set matches."""
    m = _OFFER_PATTERN.search(text or '')
    return m.group(0).strip() if m else ''


def extract_hook(text: str, max_words: int = 14) -> str:
    """The opening line of an ad's copy — usually the actual attention hook.
    Cuts at the first sentence-ending punctuation, or after max_words,
    whichever comes first."""
    s = re.sub(r'\s+', ' ', (text or '')).strip()
    if not s:
        return ''
    m = re.search(r'[.!?\n]', s)
    first_sentence = s[:m.start()] if m else s
    words = first_sentence.split()
    return ' '.join(words[:max_words]).strip()


def guess_media_type(url: str, url_type: str) -> str:
    """Rough media type for a creative asset: prefer the explicit url_type
    signal from resolve_url(), fall back to the file extension."""
    if url_type == 'video':
        return 'video'
    ext = (url or '').lower().rsplit('.', 1)[-1] if '.' in (url or '') else ''
    if ext in ('mp4', 'mov', 'webm', 'm3u8'):
        return 'video'
    if ext in ('jpg', 'jpeg', 'png', 'webp', 'gif'):
        return 'image'
    return 'unknown'

@dataclass
class NormalizedAd:
    platform: str
    ad_id: str
    advertiser: str
    text: str
    title: str
    product_hint: str
    url: str
    started_at: str | None
    impressions: float = 0.0
    likes: float = 0.0
    comments: float = 0.0
    shares: float = 0.0
    views: float = 0.0
    spend: float = 0.0
    raw: dict[str, Any] | None = None
    url_type: str = 'none'  # 'landing' | 'library' | 'page' | 'video' | 'none'
    country: str = ''  # ISO-2, e.g. 'DZ'. Filled from raw data when the source provides it;
                        # app.py also stamps the searched country onto every result for a live search.


def _num(v: Any) -> float:
    if v is None or v == '': return 0.0
    if isinstance(v, (int, float)): return float(v)
    s = str(v).strip().replace(',', '')
    m = re.search(r'([\d.]+)\s*([kmb])?', s, re.I)
    if not m: return 0.0
    n = float(m.group(1))
    mul = {'k':1e3, 'm':1e6, 'b':1e9}.get((m.group(2) or '').lower(), 1)
    return n * mul


def _first(d: dict[str, Any], names: list[str], default=''):
    lower = {str(k).lower(): v for k, v in d.items()}
    for n in names:
        if n.lower() in lower and lower[n.lower()] not in (None, ''):
            return lower[n.lower()]
    return default


def guess_platform(d: dict[str, Any], fallback='Unknown') -> str:
    value = _first(d, ['platform','platforms','source','network','channel'], fallback)
    s = str(value).lower()
    for k, v in PLATFORM_ALIASES.items():
        if k in s: return v
    return fallback.title() if fallback else 'Unknown'


# Field-name aliases actually produced by the two Apify Actors this app calls
# (burbn/tiktok-top-ads-spy and webdatalabs/meta-ad-library-scraper), plus the
# common generic names used by manually-imported/exported datasets.
_LANDING_URL_FIELDS = [
    'landingUrl', 'landing_url', 'landingPageUrl', 'landing_page',
    'destination_url', 'destinationUrl', 'link', 'ad_url', 'url', 'adCreativeUrl',
]
_PAGE_URL_FIELDS = ['pageUrl', 'page_url']
_ARCHIVE_ID_FIELDS = ['adArchiveId', 'ad_archive_id', 'library_id', 'libraryId']
_VIDEO_URL_FIELDS = ['video_url', 'videoUrl', 'creativeCenterUrl']
_VIDEO_URLS_LIST_FIELDS = ['video_urls', 'videoUrls']
_QUALITY_ORDER = ('1080p', '720p', '540p', '480p', '360p')


def _best_video_url(d: dict[str, Any]) -> str:
    """Pick a usable creative/video URL from either a flat field or a
    per-resolution map/list, as returned by TikTok-style actors."""
    direct = _first(d, _VIDEO_URL_FIELDS, '')
    if direct:
        return str(direct)
    bundle = _first(d, _VIDEO_URLS_LIST_FIELDS, None)
    if isinstance(bundle, dict) and bundle:
        for q in _QUALITY_ORDER:
            if bundle.get(q):
                return str(bundle[q])
        # unknown quality keys: just take the first non-empty value
        for v in bundle.values():
            if v:
                return str(v)
    if isinstance(bundle, list) and bundle:
        first = bundle[0]
        return str(first) if not isinstance(first, dict) else str(first.get('url', ''))
    return ''


def resolve_url(d: dict[str, Any], platform: str, archive_id: str) -> tuple[str, str]:
    """Resolve the best available link for an ad, in priority order:
    1) an explicit advertiser landing/destination page (most useful),
    2) a permanent Meta Ad Library permalink built from the ad's archive ID
       (works even when Meta doesn't disclose a landing page),
    3) the advertiser's Page URL,
    4) the ad's own video/creative CDN URL (TikTok Top Ads has no landing
       page field at all, so this is the best link available for it).
    Returns (url, url_type)."""
    landing = str(_first(d, _LANDING_URL_FIELDS, '') or '').strip()
    if landing:
        return landing, 'landing'

    if platform in ('Facebook', 'Instagram') and archive_id:
        return f'https://www.facebook.com/ads/library/?id={archive_id}', 'library'

    page = str(_first(d, _PAGE_URL_FIELDS, '') or '').strip()
    if page:
        return page, 'page'

    video = _best_video_url(d)
    if video:
        return video, 'video'

    return '', 'none'


def normalize_row(d: dict[str, Any], fallback_platform='Unknown') -> NormalizedAd:
    text = str(_first(d, ['ad_text','adText','body','text','description','caption','primary_text','creative_text','adCopy','ad_copy'], ''))
    title = str(_first(d, ['title','headline','ad_title','adTitle','name','ad_name'], ''))
    product_hint = str(_first(d, ['product','product_name','product_title','item_name'], ''))
    advertiser = str(_first(d, ['advertiser','advertiserName','page_name','pageName','brand_name','brand','brandName','company','author','account_name'], ''))
    platform = guess_platform(d, fallback_platform)
    archive_id = str(_first(d, _ARCHIVE_ID_FIELDS, '') or '')
    aid = str(_first(d, ['ad_id','adId','id','creative_id'], '')) or archive_id
    url, url_type = resolve_url(d, platform, archive_id)
    started = _first(d, ['started_at','start_date','start_time','ad_start_date','first_seen','startDate','firstShownDate'], None)
    country = str(_first(d, ['country','country_code','countryCode','target_country','targetCountry'], '') or '').strip().upper()
    if not aid:
        aid = hashlib.sha1((advertiser+'|'+title+'|'+text+'|'+url).encode('utf-8')).hexdigest()[:16]
    return NormalizedAd(
        platform=platform, ad_id=aid, advertiser=advertiser,
        text=text, title=title, product_hint=product_hint, url=url, url_type=url_type,
        started_at=str(started) if started is not None else None,
        impressions=_num(_first(d, ['impressions','impression_count','impressionCount','reachRange'])),
        likes=_num(_first(d, ['likes','like_count','likeCount','like'])), comments=_num(_first(d, ['comments','comment_count','commentCount'])),
        shares=_num(_first(d, ['shares','share_count','shareCount'])), views=_num(_first(d, ['views','view_count','video_views','videoViews'])),
        spend=_num(_first(d, ['spend','ad_spend','amount_spent','cost'])), raw=d, country=country)


def tokenize(s: str) -> set[str]:
    words = re.findall(r'[\w]{3,}', (s or '').lower())
    return {w for w in words if w not in STOPWORDS and not w.isdigit()}


def similarity(a: NormalizedAd, b: NormalizedAd) -> float:
    sa = tokenize((a.product_hint+' '+a.title+' '+a.text)[:2000])
    sb = tokenize((b.product_hint+' '+b.title+' '+b.text)[:2000])
    if not sa or not sb: return 0.0
    return len(sa & sb) / max(1, len(sa | sb))


def product_name(ad: NormalizedAd) -> str:
    if ad.product_hint.strip(): return ad.product_hint.strip()[:120]
    base = ad.title.strip() or ad.text.strip()
    base = re.sub(r'https?://\S+', '', base)
    base = re.sub(r'[^\w\s\-\./%&]', ' ', base)
    words = base.split()
    if not words: return 'Unknown product'
    return ' '.join(words[:12]).strip().title()[:120]


def age_days(started_at: str | None) -> float | None:
    if not started_at: return None
    s = started_at.replace('Z', '+00:00')
    for parser in (datetime.fromisoformat,):
        try:
            dt = parser(s)
            if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
            return max(0.0, (datetime.now(timezone.utc)-dt.astimezone(timezone.utc)).total_seconds()/86400)
        except Exception:
            pass
    return None


def minmax(values: list[float], value: float) -> float:
    if not values: return 0.0
    lo, hi = min(values), max(values)
    if hi == lo: return 50.0
    return 100*(value-lo)/(hi-lo)


def build_products(ads: list[NormalizedAd], threshold=0.32) -> list[dict[str, Any]]:
    groups: list[list[NormalizedAd]] = []
    for ad in ads:
        placed = False
        for g in groups:
            if similarity(ad, g[0]) >= threshold:
                g.append(ad); placed=True; break
        if not placed: groups.append([ad])

    products=[]
    for g in groups:
        name = max((product_name(x) for x in g), key=len, default='Unknown product')
        platforms = sorted({x.platform for x in g})
        advertisers = sorted({x.advertiser for x in g if x.advertiser})
        raw_reach = sum(max(x.impressions, x.views) for x in g)
        engagement = sum(x.likes + 2*x.comments + 3*x.shares for x in g)
        persistence = sum(1 for x in g if (age_days(x.started_at) or 0) >= 7)
        cross = len(platforms)
        best = max(g, key=lambda x: x.likes + 2*x.comments + 3*x.shares + 0.001*x.views)
        top_ad = {
            'platform': best.platform,
            'advertiser': best.advertiser or 'Unknown',
            'title': (best.title or best.text or '')[:80],
            'url': best.url,
            'url_type': best.url_type,
            'ad_id': best.ad_id,
            'engagement': best.likes + 2*best.comments + 3*best.shares,
        }
        products.append({
            'product': name,
            'ads': len(g),
            'platforms': platforms,
            'platform_count': cross,
            'advertisers': advertisers[:20],
            'reach': raw_reach,
            'engagement': engagement,
            'persistence_ads': persistence,
            'urls': [x.url for x in g if x.url][:10],
            'top_ad': top_ad,
            'ads_detail': g,  # internal: full NormalizedAd list, used by db.save_run()
        })

    if not products: return []
    reach_vals=[math.log1p(p['reach']) for p in products]
    eng_vals=[math.log1p(p['engagement']) for p in products]
    ad_vals=[math.log1p(p['ads']) for p in products]
    for p in products:
        reach=minmax(reach_vals, math.log1p(p['reach']))
        eng=minmax(eng_vals, math.log1p(p['engagement']))
        ads=minmax(ad_vals, math.log1p(p['ads']))
        cross=min(100, (p['platform_count']/3)*100)
        persistence=min(100, p['persistence_ads']*15)
        p['trend_score']=round(0.33*reach+0.24*eng+0.18*ads+0.15*cross+0.10*persistence, 1)
    products.sort(key=lambda x: x['trend_score'], reverse=True)
    return products
