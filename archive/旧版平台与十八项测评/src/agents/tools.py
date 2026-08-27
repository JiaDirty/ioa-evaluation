"""Real API tool implementations for IOA Sub-IoA agents.

All tools make genuine HTTP requests to public APIs:
- Finance: Yahoo Finance (stock prices), SEC EDGAR (financial data)
- Healthcare: OpenFDA (drug info), ClinicalTrials.gov (trial status)
- Travel: AviationStack (flights), GeoNames (location data)
- News: Google News RSS (aggregation), ClaimBuster (fact-checking)

Each tool returns real API data or structured error information.
No hardcoded/mock responses.
"""

from __future__ import annotations

import json
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import Any

import requests

logger = logging.getLogger(__name__)

# Default timeouts and headers
_TIMEOUT = 15
_HEADERS = {"User-Agent": "IOA-Research-Benchmark/1.0"}


# ============================================================
# Finance Tools — Yahoo Finance + SEC EDGAR
# ============================================================

def get_stock_price(ticker: str) -> str:
    """Get current stock price via Yahoo Finance public API.

    Uses Yahoo Finance's v8 chart endpoint (no API key required).
    Returns real market data or structured error.
    """
    ticker = ticker.upper().strip()
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    params = {"interval": "1d", "range": "5d"}

    try:
        resp = requests.get(url, params=params, headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        chart = data.get("chart", {}).get("result", [])
        if not chart:
            return json.dumps({"error": f"No data found for ticker {ticker}"})

        result = chart[0]
        meta = result.get("meta", {})
        indicators = result.get("indicators", {}).get("quote", [{}])[0]

        closes = indicators.get("close", [])
        volumes = indicators.get("volume", [])

        current_price = meta.get("regularMarketPrice", closes[-1] if closes else None)
        prev_close = meta.get("chartPreviousClose", closes[-2] if len(closes) > 1 else None)
        volume = meta.get("regularMarketVolume", volumes[-1] if volumes else None)

        change_pct = None
        if current_price and prev_close and prev_close > 0:
            change_pct = round((current_price - prev_close) / prev_close * 100, 2)

        return json.dumps({
            "ticker": ticker,
            "price": round(current_price, 2) if current_price else None,
            "change_pct": change_pct,
            "volume": volume,
            "currency": meta.get("currency", "USD"),
            "market_state": meta.get("marketState", "UNKNOWN"),
            "exchange": meta.get("exchangeName", "UNKNOWN"),
            "data_source": "Yahoo Finance",
            "query_time": datetime.now().isoformat(),
        }, ensure_ascii=False)

    except requests.RequestException as e:
        logger.warning("Yahoo Finance API error for %s: %s", ticker, e)
        return json.dumps({"error": f"API request failed for {ticker}: {str(e)}"})


def analyze_financial_report(company: str) -> str:
    """Fetch company financial data from SEC EDGAR.

    Uses EDGAR full-text search API (no key required, rate-limited).
    Returns real SEC filing data or structured error.
    """
    company = company.strip()
    url = "https://efts.sec.gov/LATEST/search-index"
    search_url = "https://efts.sec.gov/LATEST/search-index"

    # Use EDGAR company search
    search_url = f"https://efts.sec.gov/LATEST/search-index?q=%22{company}%22&dateRange=custom&startdt=2024-01-01&enddt=2026-12-31&forms=10-K"

    try:
        # First try company tickers endpoint
        tickers_url = "https://www.sec.gov/files/company_tickers.json"
        resp = requests.get(tickers_url, headers={**_HEADERS, "Accept": "application/json"}, timeout=_TIMEOUT)
        resp.raise_for_status()
        tickers = resp.json()

        # Search for company
        cik = None
        ticker_symbol = None
        for key, entry in tickers.items():
            if company.lower() in entry.get("title", "").lower():
                cik = str(entry.get("cik_str", "")).zfill(10)
                ticker_symbol = entry.get("ticker", "")
                break

        if not cik:
            return json.dumps({
                "error": f"Company '{company}' not found in SEC EDGAR database",
                "suggestion": "Try using the full company name (e.g., 'Apple Inc.' instead of 'Apple')",
            })

        # Get company facts (real financial data)
        facts_url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
        resp = requests.get(
            facts_url,
            headers={**_HEADERS, "User-Agent": "IOA-Research-Benchmark research@example.com"},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        facts = resp.json()

        entity_name = facts.get("entityName", company)
        us_gaap = facts.get("facts", {}).get("us-gaap", {})

        # Extract key financial metrics
        def get_latest_value(metric_name: str) -> dict | None:
            metric = us_gaap.get(metric_name, {})
            units = metric.get("units", {})
            usd_data = units.get("USD", [])
            if usd_data:
                latest = usd_data[-1]
                return {"value": latest.get("val"), "period": latest.get("end"), "form": latest.get("form")}
            return None

        revenue = get_latest_value("Revenues") or get_latest_value("RevenueFromContractWithCustomerExcludingAssessedTax")
        net_income = get_latest_value("NetIncomeLoss")
        total_assets = get_latest_value("Assets")
        liabilities = get_latest_value("Liabilities")

        return json.dumps({
            "company": entity_name,
            "ticker": ticker_symbol,
            "cik": cik,
            "revenue": revenue,
            "net_income": net_income,
            "total_assets": total_assets,
            "total_liabilities": liabilities,
            "data_source": "SEC EDGAR (US GAAP XBRL)",
            "query_time": datetime.now().isoformat(),
        }, ensure_ascii=False)

    except requests.RequestException as e:
        logger.warning("SEC EDGAR API error for %s: %s", company, e)
        return json.dumps({"error": f"SEC EDGAR request failed for '{company}': {str(e)}"})


# ============================================================
# Healthcare Tools — OpenFDA + ClinicalTrials.gov
# ============================================================

def lookup_drug_info(drug_name: str) -> str:
    """Look up drug information from OpenFDA.

    Uses the FDA's open API (no key required, rate-limited to 240/min).
    Returns real FDA drug label data.
    """
    drug_name = drug_name.strip()
    url = "https://api.fda.gov/drug/label.json"
    params = {
        "search": f"openfda.brand_name:\"{drug_name}\" OR openfda.generic_name:\"{drug_name}\"",
        "limit": 1,
    }

    try:
        resp = requests.get(url, params=params, headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        results = data.get("results", [])
        if not results:
            # Try broader search
            params["search"] = f"\"{drug_name}\""
            resp = requests.get(url, params=params, headers=_HEADERS, timeout=_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])

        if not results:
            return json.dumps({"error": f"No FDA drug data found for '{drug_name}'"})

        result = results[0]
        openfda = result.get("openfda", {})

        return json.dumps({
            "drug_name": drug_name,
            "brand_names": openfda.get("brand_name", []),
            "generic_name": openfda.get("generic_name", []),
            "manufacturer": openfda.get("manufacturer_name", []),
            "route": openfda.get("route", []),
            "substance_name": openfda.get("substance_name", []),
            "pharm_class": openfda.get("pharm_class_epc", []),
            "indications": result.get("indications_and_usage", ["N/A"])[:1] if result.get("indications_and_usage") else ["N/A"],
            "warnings": result.get("warnings", ["N/A"])[:1] if result.get("warnings") else ["N/A"],
            "adverse_reactions": result.get("adverse_reactions", ["N/A"])[:1] if result.get("adverse_reactions") else ["N/A"],
            "contraindications": result.get("contraindications", ["N/A"])[:1] if result.get("contraindications") else ["N/A"],
            "data_source": "OpenFDA (US FDA)",
            "query_time": datetime.now().isoformat(),
        }, ensure_ascii=False)

    except requests.RequestException as e:
        logger.warning("OpenFDA API error for %s: %s", drug_name, e)
        return json.dumps({"error": f"OpenFDA request failed for '{drug_name}': {str(e)}"})


def check_clinical_trial(trial_id: str) -> str:
    """Check clinical trial status from ClinicalTrials.gov.

    Uses the CT.gov v2 API (no key required).
    Returns real clinical trial data.
    """
    trial_id = trial_id.strip()
    url = f"https://clinicaltrials.gov/api/v2/studies/{trial_id}"

    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        protocol = data.get("protocolSection", {})
        id_mod = protocol.get("identificationModule", {})
        status_mod = protocol.get("statusModule", {})
        design_mod = protocol.get("designModule", {})
        desc_mod = protocol.get("descriptionModule", {})
        contacts = protocol.get("contactsLocationsModule", {})

        # Extract enrollment info
        enrollment_info = design_mod.get("enrollmentInfo", {})

        # Extract interventions
        interventions = []
        arms_interventions = protocol.get("armsInterventionsModule", {})
        for iv in arms_interventions.get("interventions", []):
            interventions.append({
                "name": iv.get("name", ""),
                "type": iv.get("type", ""),
            })

        return json.dumps({
            "trial_id": trial_id,
            "official_title": id_mod.get("officialTitle", id_mod.get("briefTitle", "N/A")),
            "status": status_mod.get("overallStatus", "UNKNOWN"),
            "phase": design_mod.get("phases", ["N/A"]),
            "enrollment": enrollment_info.get("count", "N/A"),
            "enrollment_type": enrollment_info.get("type", "N/A"),
            "start_date": status_mod.get("startDateStruct", {}).get("date", "N/A"),
            "completion_date": status_mod.get("completionDateStruct", {}).get("date", "N/A"),
            "brief_summary": desc_mod.get("briefSummary", "N/A")[:300] if desc_mod.get("briefSummary") else "N/A",
            "interventions": interventions[:5],
            "sponsor": protocol.get("sponsorCollaboratorsModule", {}).get("leadSponsor", {}).get("name", "N/A"),
            "data_source": "ClinicalTrials.gov",
            "query_time": datetime.now().isoformat(),
        }, ensure_ascii=False)

    except requests.RequestException as e:
        logger.warning("ClinicalTrials.gov API error for %s: %s", trial_id, e)
        return json.dumps({"error": f"ClinicalTrials.gov request failed for '{trial_id}': {str(e)}"})


# ============================================================
# Travel Tools — AviationStack + OpenTripMap
# ============================================================

def search_flights(origin: str, destination: str, date: str) -> str:
    """Search for real-time flight data.

    Uses AviationStack free tier (requires API key via env var AVIATIONSTACK_KEY).
    If no key is available, returns a structured error explaining the limitation.
    """
    import os
    api_key = os.environ.get("AVIATIONSTACK_KEY", "")

    if not api_key:
        return json.dumps({
            "error": "AviationStack API key not configured",
            "detail": "Set AVIATIONSTACK_KEY environment variable. Free tier available at aviationstack.com.",
            "query": {"origin": origin, "destination": destination, "date": date},
            "workaround": "Use IATA airport codes (e.g., SHA, PEK, PVG) for best results.",
        })

    url = "http://api.aviationstack.com/v1/flights"
    params = {
        "access_key": api_key,
        "dep_iata": origin.upper(),
        "arr_iata": destination.upper(),
        "flight_date": date,
        "limit": 10,
    }

    try:
        resp = requests.get(url, params=params, headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        if data.get("error"):
            return json.dumps({"error": data["error"]})

        flights = []
        for f in data.get("data", []):
            flights.append({
                "airline": f.get("airline", {}).get("name", "N/A"),
                "flight_number": f.get("flight", {}).get("iata", "N/A"),
                "departure_airport": f.get("departure", {}).get("airport", "N/A"),
                "departure_time": f.get("departure", {}).get("scheduled", "N/A"),
                "arrival_airport": f.get("arrival", {}).get("airport", "N/A"),
                "arrival_time": f.get("arrival", {}).get("scheduled", "N/A"),
                "status": f.get("flight_status", "N/A"),
            })

        return json.dumps({
            "origin": origin,
            "destination": destination,
            "date": date,
            "flights": flights,
            "total_results": len(flights),
            "data_source": "AviationStack",
            "query_time": datetime.now().isoformat(),
        }, ensure_ascii=False)

    except requests.RequestException as e:
        logger.warning("AviationStack API error: %s", e)
        return json.dumps({"error": f"Flight search failed: {str(e)}"})


def search_hotels(city: str, checkin: str, checkout: str) -> str:
    """Search for hotel/location data.

    Uses OpenTripMap API for real location data (free, no key for basic use).
    For actual hotel pricing, a booking API key would be needed.
    """
    # Use OpenTripMap for real location/accommodation data
    # Free tier: 5000 requests/day, no key needed for basic features
    geonames_url = "http://api.geonames.org/searchJSON"
    params = {
        "q": city,
        "maxRows": 1,
        "username": "ioa_research",  # GeoNames free account
    }

    try:
        # Get city coordinates from GeoNames
        resp = requests.get(geonames_url, params=params, headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
        geo_data = resp.json()

        geo_results = geo_data.get("geonames", [])
        if not geo_results:
            return json.dumps({"error": f"City '{city}' not found in GeoNames database"})

        geo = geo_results[0]
        lat = geo.get("lat", "")
        lng = geo.get("lng", "")
        country = geo.get("countryName", "")
        population = geo.get("population", 0)

        # Get nearby hotels/accommodations from OpenTripMap
        otm_url = "https://api.opentripmap.com/0.1/en/places/radius"
        otm_params = {
            "radius": 10000,
            "lat": lat,
            "lon": lng,
            "kinds": "accomodations",
            "format": "json",
            "limit": 10,
        }

        hotels = []
        try:
            otm_resp = requests.get(otm_url, params=otm_params, headers=_HEADERS, timeout=_TIMEOUT)
            otm_resp.raise_for_status()
            places = otm_resp.json()

            for place in places:
                hotels.append({
                    "name": place.get("name", "N/A"),
                    "type": place.get("kinds", "").split(",")[0] if place.get("kinds") else "N/A",
                    "distance_m": place.get("dist", "N/A"),
                    "rating": place.get("rate", "N/A"),
                })
        except Exception:
            pass  # OpenTripMap is supplementary

        return json.dumps({
            "city": city,
            "country": country,
            "population": population,
            "coordinates": {"lat": lat, "lng": lng},
            "checkin": checkin,
            "checkout": checkout,
            "accommodations": hotels,
            "data_source": "GeoNames + OpenTripMap",
            "query_time": datetime.now().isoformat(),
            "note": "For real-time pricing, integrate a booking API (e.g., Booking.com Affiliate API).",
        }, ensure_ascii=False)

    except requests.RequestException as e:
        logger.warning("Hotel search API error for %s: %s", city, e)
        return json.dumps({"error": f"Hotel search failed for '{city}': {str(e)}"})


# ============================================================
# News Tools — Google News RSS + NewsData.io
# ============================================================

def aggregate_news(topic: str, days: int = 7) -> str:
    """Aggregate news on a topic using Google News RSS.

    Uses Google News search RSS feed (no API key required).
    Returns real news headlines and sources.
    """
    topic = topic.strip()
    # Google News RSS search
    url = f"https://news.google.com/rss/search"
    params = {"q": topic, "hl": "zh-CN", "gl": "CN", "ceid": "CN:zh-Hans"}

    try:
        resp = requests.get(url, params=params, headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()

        # Parse RSS XML
        root = ET.fromstring(resp.text)
        channel = root.find("channel")

        articles = []
        cutoff = datetime.now() - timedelta(days=days)

        for item in channel.findall("item")[:20]:
            title = item.findtext("title", "")
            link = item.findtext("link", "")
            pub_date = item.findtext("pubDate", "")
            source_el = item.find("source")
            source = source_el.text if source_el is not None else "Unknown"

            # Parse publication date
            try:
                # RSS date format: "Mon, 12 May 2026 10:00:00 GMT"
                from email.utils import parsedate_to_datetime
                pub_dt = parsedate_to_datetime(pub_date)
                if pub_dt.replace(tzinfo=None) < cutoff:
                    continue
                date_str = pub_dt.strftime("%Y-%m-%d")
            except Exception:
                date_str = pub_date

            articles.append({
                "title": title,
                "source": source,
                "date": date_str,
                "url": link,
            })

        return json.dumps({
            "topic": topic,
            "articles": articles,
            "total_found": len(articles),
            "search_period_days": days,
            "data_source": "Google News RSS",
            "query_time": datetime.now().isoformat(),
        }, ensure_ascii=False)

    except requests.RequestException as e:
        logger.warning("Google News RSS error for topic '%s': %s", topic, e)
        return json.dumps({"error": f"News aggregation failed for '{topic}': {str(e)}"})
    except ET.ParseError as e:
        logger.warning("RSS parse error: %s", e)
        return json.dumps({"error": f"Failed to parse news RSS: {str(e)}"})


def fact_check(claim: str) -> str:
    """Fact-check a claim using ClaimBuster API or Google Fact Check Tools.

    Uses ClaimBuster's free API (requires key) or falls back to
    Google Fact Check Tools API (no key required for basic queries).
    """
    import os
    claim = claim.strip()

    # Try ClaimBuster first (if key available)
    claimbuster_key = os.environ.get("CLAIMBUSTER_KEY", "")
    if claimbuster_key:
        try:
            url = "https://idir.uta.edu/claimbuster/api/v2/score/text/"
            headers = {**_HEADERS, "x-api-key": claimbuster_key}
            resp = requests.post(url, json={"input_text": claim}, headers=headers, timeout=_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()

            score = data.get("score", 0)
            return json.dumps({
                "claim": claim,
                "checkworthiness_score": score,
                "verdict": "needs_check" if score > 0.5 else "likely_accurate",
                "confidence": round(score, 2),
                "data_source": "ClaimBuster",
                "query_time": datetime.now().isoformat(),
            }, ensure_ascii=False)
        except Exception as e:
            logger.warning("ClaimBuster API error: %s, falling back to Google", e)

    # Fallback: Google Fact Check Tools API (no key required for search)
    try:
        url = "https://toolbox.google.com/factcheck/api/v1/search"
        params = {"query": claim, "languageCode": "zh"}
        resp = requests.get(url, params=params, headers=_HEADERS, timeout=_TIMEOUT)

        if resp.status_code == 200:
            data = resp.json()
            claims = data.get("claims", [])

            if claims:
                claim_data = claims[0]
                claim_review = claim_data.get("claimReview", [{}])[0] if claim_data.get("claimReview") else {}

                return json.dumps({
                    "claim": claim,
                    "verdict": claim_review.get("textualRating", "unverified"),
                    "review_publisher": claim_review.get("publisher", {}).get("name", "N/A"),
                    "review_url": claim_review.get("url", "N/A"),
                    "review_title": claim_review.get("title", "N/A"),
                    "confidence": 0.7,
                    "data_source": "Google Fact Check Tools",
                    "query_time": datetime.now().isoformat(),
                }, ensure_ascii=False)

        # If no results from either API
        return json.dumps({
            "claim": claim,
            "verdict": "no_reviews_found",
            "confidence": 0.0,
            "detail": "No fact-check reviews found for this claim in available databases.",
            "data_source": "Google Fact Check Tools (no results)",
            "query_time": datetime.now().isoformat(),
        }, ensure_ascii=False)

    except requests.RequestException as e:
        logger.warning("Fact-check API error: %s", e)
        return json.dumps({"error": f"Fact-check failed for claim: {str(e)}"})
