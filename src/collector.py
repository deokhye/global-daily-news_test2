"""
collector.py
------------
Global Daily News 대시보드용 데이터 수집기 (v2 — 공식 기관 데이터 소스 연동판).

수집 항목과 출처
  1) 국가 기본 프로필
       - 총 인구 / 연간 GDP           : World Bank Open API
       - 인플레이션(CPI YoY) / 실업률  : 미국 노동통계국(BLS) API
                                         → (백업) FRED API → (백업) World Bank 연간 지표
       - 연방/테네시 최저임금          : 통계청·DOL 고시 기준 상수값 (자주 변하지 않는 법정 수치)
  2) USD/KRW 환율 (실시간 + 최근 12개월 월별 종가) : Yahoo Finance (yfinance, 'KRW=X')
  3) 현지 주요 뉴스 (당일 실시간 핫뉴스)             : Google News RSS (당일 = when:1d)
       - TOP NEWS / BUSINESS / WORLD  : Google News 토픽 피드
       - SOCIETY                      : Google News 검색 피드
  4) 산업 및 비즈니스 동향 (4대 핵심 분야)            : Google News RSS 키워드 검색 (최근 7일)
       - AUTO MARKET / HR & LABOR / ECONOMY / MANAGEMENT

모든 외부 호출은 개별적으로 예외 처리하며, 실패 시
  최신 API → 대체 API → data/latest.json 캐시 → 안전한 기본값
순으로 폴백한다. 따라서 일부 소스가 죽어도 파이프라인 전체는 중단되지 않는다.

결과는 data/latest.json 으로 저장되고, build_site.py 가 이를 읽어
template/template.html 에 주입해 docs/index.html 을 생성한다.
"""

import os
import re
import json
import logging
from datetime import datetime

import requests
import feedparser
import pytz

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("collector")

KST = pytz.timezone("Asia/Seoul")
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
CACHE_PATH = os.path.join(DATA_DIR, "latest.json")

MONTH_KR = ["1월", "2월", "3월", "4월", "5월", "6월",
            "7월", "8월", "9월", "10월", "11월", "12월"]

# 선택적 API 키 (없어도 파이프라인은 정상 동작 — 폴백 체인으로 처리)
BLS_API_KEY = os.environ.get("BLS_API_KEY", "")     # https://www.bls.gov/developers/
FRED_API_KEY = os.environ.get("FRED_API_KEY", "")   # https://fred.stlouisfed.org/docs/api/api_key.html

REQUEST_TIMEOUT = 10


# ---------------------------------------------------------------------------
# 0. 캐시 유틸
# ---------------------------------------------------------------------------
def load_cache() -> dict:
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log.warning(f"캐시 로드 실패: {e}")
    return {}


def save_cache(data: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


def _shorten(text: str, max_len: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= max_len else text[:max_len].rstrip() + "…"


# ---------------------------------------------------------------------------
# 1. 국가 기본 프로필 — 인구 / GDP (World Bank Open API)
# ---------------------------------------------------------------------------
WB_INDICATORS = {"population": "SP.POP.TOTL", "gdp": "NY.GDP.MKTP.CD"}


def _wb_latest_value(indicator: str):
    url = f"https://api.worldbank.org/v2/country/US/indicator/{indicator}?format=json&per_page=5"
    resp = requests.get(url, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    payload = resp.json()
    for row in payload[1]:
        if row.get("value") is not None:
            return float(row["value"]), row["date"]
    raise ValueError("유효 데이터 없음")


def get_population_gdp(cache: dict) -> dict:
    cached = cache.get("country_profile", {})
    out = {}
    try:
        pop, _ = _wb_latest_value(WB_INDICATORS["population"])
        out["population"] = f"약 {pop / 1e8:.2f}억 명"
    except Exception as e:
        log.warning(f"[World Bank] 인구 수집 실패 → 캐시 사용: {e}")
        out["population"] = cached.get("population", "약 3.42억 명")

    try:
        gdp, _ = _wb_latest_value(WB_INDICATORS["gdp"])
        out["gdp"] = f"약 {gdp / 1e12:.1f}조 USD"
    except Exception as e:
        log.warning(f"[World Bank] GDP 수집 실패 → 캐시 사용: {e}")
        out["gdp"] = cached.get("gdp", "약 30.5조 USD")

    return out


# ---------------------------------------------------------------------------
# 2. 국가 기본 프로필 — 인플레이션(CPI YoY) / 실업률
#    1순위 BLS → 2순위 FRED → 3순위 World Bank(연간) → 4순위 캐시
# ---------------------------------------------------------------------------
def _bls_series(series_ids: list, start_year: int, end_year: int) -> dict:
    """BLS Public Data API v2 (키 없어도 동작, 키가 있으면 호출 한도 상향)."""
    url = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
    payload = {
        "seriesid": series_ids,
        "startyear": str(start_year),
        "endyear": str(end_year),
    }
    if BLS_API_KEY:
        payload["registrationkey"] = BLS_API_KEY
    resp = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    body = resp.json()
    if body.get("status") != "REQUEST_SUCCEEDED":
        raise ValueError(body.get("message", ["BLS API 오류"])[0] if body.get("message") else "BLS API 오류")
    return {s["seriesID"]: s["data"] for s in body["Results"]["series"]}


def _labor_stats_from_bls() -> dict:
    this_year = datetime.now(KST).year
    series = {"unemployment": "LNS14000000", "cpi": "CUUR0000SA0"}  # 전국 실업률 / CPI-U (비계절조정)
    data = _bls_series(list(series.values()), this_year - 1, this_year)

    unemployment = float(data[series["unemployment"]][0]["value"])

    cpi_points = data[series["cpi"]]  # 최신순 정렬
    latest = cpi_points[0]
    prior = next(
        p for p in cpi_points
        if p["period"] == latest["period"] and int(p["year"]) == int(latest["year"]) - 1
    )
    inflation = (float(latest["value"]) - float(prior["value"])) / float(prior["value"]) * 100

    return {
        "inflation": f"{inflation:.1f}%",
        "unemployment": f"{unemployment:.1f}%",
        "stats_source": "BLS",
        "stats_asof": f"{latest['year']}-{latest['period'].replace('M', '')}",
    }


def _fred_observations(series_id: str, limit: int) -> list:
    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id": series_id,
        "api_key": FRED_API_KEY,
        "file_type": "json",
        "sort_order": "desc",
        "limit": limit,
    }
    resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()["observations"]


def _labor_stats_from_fred() -> dict:
    if not FRED_API_KEY:
        raise RuntimeError("FRED_API_KEY 미설정")

    unemployment = float(_fred_observations("UNRATE", 1)[0]["value"])

    cpi_obs = _fred_observations("CPIAUCSL", 13)  # 최근 13개월 (전년 동월 대비 계산용)
    latest_cpi = float(cpi_obs[0]["value"])
    year_ago_cpi = float(cpi_obs[-1]["value"])
    inflation = (latest_cpi - year_ago_cpi) / year_ago_cpi * 100

    return {
        "inflation": f"{inflation:.1f}%",
        "unemployment": f"{unemployment:.1f}%",
        "stats_source": "FRED",
        "stats_asof": cpi_obs[0]["date"][:7],
    }


def _labor_stats_from_world_bank() -> dict:
    infl, infl_date = _wb_latest_value("FP.CPI.TOTL.ZG")
    unemp, unemp_date = _wb_latest_value("SL.UEM.TOTL.ZS")
    return {
        "inflation": f"{infl:.1f}%",
        "unemployment": f"{unemp:.1f}%",
        "stats_source": "World Bank",
        "stats_asof": infl_date,
    }


def get_labor_stats(cache: dict) -> dict:
    """CPI YoY / 실업률을 BLS → FRED → World Bank → 캐시 순으로 폴백 수집."""
    for label, fn in (("BLS", _labor_stats_from_bls),
                       ("FRED", _labor_stats_from_fred),
                       ("World Bank", _labor_stats_from_world_bank)):
        try:
            return fn()
        except Exception as e:
            log.warning(f"[{label}] 물가/고용 지표 수집 실패: {e}")

    cached = cache.get("country_profile", {})
    log.warning("모든 공식 API 실패 → 캐시/기본값 사용")
    return {
        "inflation": cached.get("inflation", "3.2%"),
        "unemployment": cached.get("unemployment", "4.3%"),
        "stats_source": cached.get("stats_source", "캐시"),
        "stats_asof": cached.get("stats_asof", "-"),
    }


# ---------------------------------------------------------------------------
# 3. 국가 기본 프로필 — 최저임금 (법정 고시 기준값)
#    연방/주 최저임금은 API로 매일 바뀌는 값이 아니라 DOL 고시 기준 상수로 관리한다.
#    시행일이 바뀌면 이 상수만 갱신하면 된다.
# ---------------------------------------------------------------------------
STATUTORY_MIN_WAGE = {
    "federal": "$7.25 / h (연방, 2009-07-24 발효)",
    "tennessee": "주 별도 기준 없음 → 연방 기준 $7.25/h 적용",
}


def get_country_profile(cache: dict) -> dict:
    profile = {"capital": "Washington, D.C."}
    profile.update(get_population_gdp(cache))
    profile.update(get_labor_stats(cache))
    profile["min_wage_federal"] = STATUTORY_MIN_WAGE["federal"]
    profile["min_wage_tn"] = STATUTORY_MIN_WAGE["tennessee"]
    return profile


# ---------------------------------------------------------------------------
# 4. USD/KRW 환율 (Yahoo Finance)
# ---------------------------------------------------------------------------
def get_exchange_rate(cache: dict) -> dict:
    cached = cache.get("exchange_rate", {})
    try:
        import yfinance as yf

        ticker = yf.Ticker("KRW=X")

        current_rate = float(ticker.fast_info["lastPrice"])

        # 최근 13개월 월봉 → 12개 종가 산출 (여유분 1개월 포함)
        hist = ticker.history(period="13mo", interval="1mo").dropna(subset=["Close"]).tail(12)
        history_labels = [MONTH_KR[idx.month - 1] for idx in hist.index]
        history_values = [round(float(v), 2) for v in hist["Close"]]

        # 전일 대비 등락률 (최근 일봉 2개)
        daily = ticker.history(period="5d", interval="1d").dropna(subset=["Close"])
        if len(daily) >= 2:
            prev_close = float(daily["Close"].iloc[-2])
            change_pct = round((current_rate - prev_close) / prev_close * 100, 2)
        else:
            change_pct = 0.0

        if not history_labels:
            raise ValueError("월별 히스토리 조회 실패")

        return {
            "current_rate": current_rate,
            "change_pct": change_pct,
            "history_labels": history_labels,
            "history_values": history_values,
            "source": "Yahoo Finance",
        }
    except Exception as e:
        log.warning(f"[Yahoo Finance] 환율 수집 실패 → 캐시/기본값 사용: {e}")
        if cached:
            return cached
        return {
            "current_rate": 1380.00,
            "change_pct": 0.0,
            "history_labels": MONTH_KR,
            "history_values": [1380.0] * 12,
            "source": "기본값(수집 실패)",
        }


# ---------------------------------------------------------------------------
# 5. 현지 주요 뉴스 (Local Headlines) — 당일 실시간 핫뉴스
# ---------------------------------------------------------------------------
def _google_news_topic_url(topic: str) -> str:
    return f"https://news.google.com/rss/headlines/section/topic/{topic}?hl=en-US&gl=US&ceid=US:en"


def _google_news_search_url(query: str) -> str:
    return f"https://news.google.com/rss/search?q={requests.utils.quote(query)}&hl=en-US&gl=US&ceid=US:en"


def _parse_entry(entry) -> dict:
    source = ""
    if hasattr(entry, "source"):
        source = getattr(entry.source, "title", "") or ""
    elif " - " in entry.title:
        source = entry.title.split(" - ")[-1]
    summary = _strip_html(getattr(entry, "summary", ""))
    return {
        "title": entry.title.split(" - ")[0] if " - " in entry.title else entry.title,
        "source": source or "Google News",
        "link": entry.link,
        "summary": _shorten(summary, 70) if summary else "",
    }


def _fetch_feed(url: str, limit: int = 1) -> list:
    feed = feedparser.parse(url)
    return [_parse_entry(e) for e in feed.entries[:limit]]


HEADLINE_SOURCES = [
    {"category": "TOP NEWS", "label_class": "text-rose-600", "kind": "topic", "value": "NATION"},
    {"category": "BUSINESS", "label_class": "text-emerald-600", "kind": "topic", "value": "BUSINESS"},
    {"category": "WORLD", "label_class": "text-blue-700", "kind": "topic", "value": "WORLD"},
    {"category": "SOCIETY", "label_class": "text-purple-600", "kind": "search", "value": "US society news when:1d"},
]


def get_local_headlines(cache: dict) -> list:
    """당일(when:1d) 기준 미국 현지 핫뉴스: TOP NEWS / BUSINESS / WORLD / SOCIETY."""
    cached_by_category = {h.get("category"): h for h in cache.get("headlines", [])}
    headlines = []

    for spec in HEADLINE_SOURCES:
        try:
            if spec["kind"] == "topic":
                url = _google_news_topic_url(spec["value"])
            else:
                url = _google_news_search_url(spec["value"])

            item = _fetch_feed(url, limit=1)[0]
            headlines.append({
                "category": spec["category"],
                "label_class": spec["label_class"],
                "source": item["source"],
                "title": item["title"],
                "summary": item["summary"],
                "link": item["link"],
            })
        except Exception as e:
            log.warning(f"[Google News] '{spec['category']}' 헤드라인 수집 실패: {e}")
            cached = cached_by_category.get(spec["category"])
            if cached:
                headlines.append(cached)

    return headlines


# ---------------------------------------------------------------------------
# 6. 산업 및 비즈니스 동향 (Industry, Auto & HR Intelligence) — 4대 핵심 분야
# ---------------------------------------------------------------------------
INDUSTRY_SOURCES = [
    {
        "category": "AUTO MARKET",
        "tag": "완성차·타이어 시장",
        "tag_class": "bg-rose-100 text-rose-700",
        "query": "US auto OEM production EV tire demand when:7d",
    },
    {
        "category": "HR & LABOR",
        "tag": "노동법·인력",
        "tag_class": "bg-indigo-100 text-indigo-700",
        "query": "Tennessee labor law non-compete manufacturing wages when:7d",
    },
    {
        "category": "ECONOMY",
        "tag": "경기·금리",
        "tag_class": "bg-emerald-100 text-emerald-700",
        "query": "Federal Reserve interest rate manufacturing PMI outlook when:7d",
    },
    {
        "category": "MANAGEMENT",
        "tag": "관세·공급망",
        "tag_class": "bg-amber-100 text-amber-700",
        "query": "US tariff trade policy supply chain shipping cost when:7d",
    },
]


def get_industry_trends(cache: dict) -> list:
    """당사 비즈니스 직결 4대 분야: AUTO MARKET / HR & LABOR / ECONOMY / MANAGEMENT."""
    cached_by_category = {t.get("category"): t for t in cache.get("hr_trends", [])}
    trends = []

    for spec in INDUSTRY_SOURCES:
        try:
            item = _fetch_feed(_google_news_search_url(spec["query"]), limit=1)[0]
            trends.append({
                "category": spec["category"],
                "tag": spec["tag"],
                "tag_class": spec["tag_class"],
                "title": _shorten(item["title"], 46),
                "desc": item["summary"] or _shorten(item["title"], 70),
                "source": item["source"],
                "link": item["link"],
            })
        except Exception as e:
            log.warning(f"[Google News] '{spec['category']}' 산업 동향 수집 실패: {e}")
            cached = cached_by_category.get(spec["category"])
            if cached:
                trends.append(cached)

    return trends


# ---------------------------------------------------------------------------
# 메인 실행
# ---------------------------------------------------------------------------
def main() -> dict:
    cache = load_cache()

    log.info("환율 데이터 수집 중 (Yahoo Finance)...")
    exchange_rate = get_exchange_rate(cache)

    log.info("현지 주요 뉴스 수집 중 (Google News, when:1d)...")
    headlines = get_local_headlines(cache)

    log.info("산업/비즈니스 동향 수집 중 (Google News, when:7d)...")
    hr_trends = get_industry_trends(cache)

    log.info("국가 기본 프로필 수집 중 (World Bank / BLS / FRED)...")
    country_profile = get_country_profile(cache)

    now_kst = datetime.now(KST)
    data = {
        "generated_at": now_kst.isoformat(),
        "generated_at_display": now_kst.strftime("%Y-%m-%d %H:%M KST"),
        "exchange_rate": exchange_rate,
        "headlines": headlines,
        "hr_trends": hr_trends,
        "country_profile": country_profile,
    }

    save_cache(data)
    log.info(f"수집 완료 → {CACHE_PATH}")
    return data


if __name__ == "__main__":
    main()
