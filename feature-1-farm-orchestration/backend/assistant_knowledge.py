import math
import re
from collections import Counter
from typing import Dict, List


FEATURE_KNOWLEDGE = [
    {
        "name": "Fleet dashboard and soil check",
        "route": "index.html",
        "terms": "fleet dashboard soil check field health manual entry previous readings HM-10 Bluetooth sensor telemetry N P K pH EC moisture temperature crop recommendations run agent",
        "content": "The Fleet dashboard lets a farmer enter field and soil readings manually, reuse previous readings, or connect an HM-10 Bluetooth sensor. It displays soil telemetry and runs the farm-agent workflow to produce risk, planning, and orchestration output. Sensor values are only available when supplied or saved by the user.",
    },
    {
        "name": "Market Details / Mandi Prices",
        "route": "market.html",
        "terms": "market mandi prices crop commodity vegetable grain rates Agmarknet Data.gov.in GPS location nearest market arrival date",
        "content": "Market Details displays crop and commodity mandi records and can filter by commodity, category, state, and location. Built-in records are sample/reference data, not current prices. The optional Data.gov.in Agmarknet feed requires DATA_GOV_API_KEY. GPS Sync can resolve approximate district/state through a cached OpenStreetMap Nominatim locality lookup and filter records there, but Agmarknet has no verified market coordinates, so it cannot establish the nearest market by distance.",
    },
    {
        "name": "Farm Support",
        "route": "farm_support.html",
        "terms": "farm support locator fertilizer fertiliser seeds machinery tractor equipment mandi stores GPS nearby providers",
        "content": "Farm Support shows nearby categories such as markets, fertilizer suppliers, and machinery using browser location when allowed. The provider cards in this project are sample listings, not a verified live business directory. Confirm a business and contact details before travelling.",
    },
    {
        "name": "Emergency SOS",
        "route": "sos.html",
        "terms": "emergency SOS fire flood flooding animal intrusion incident voice recording voice note upload nearby farmer community alert emergency call 112",
        "content": "Emergency SOS lets a farmer record or upload an incident voice note and share it with this app instance along with GPS coordinates. The local prototype stores reports for up to seven days and filters the feed/audio to five kilometres. It does not notify other devices unless they can reach the same deployed backend, does not verify users, and does not contact emergency services. Call 112 directly in an emergency.",
    },
    {
        "name": "Yield calculator",
        "route": "yield_calculator.html",
        "terms": "yield calculator harvest previous latest crop kg kilograms per acre change revenue growth production",
        "content": "Yield Calculator compares previous and latest harvest amounts, calculates change and per-acre yield, and estimates revenue from the price supplied by the user. The result is a calculation based on entered values, not a market-price feed.",
    },
    {
        "name": "Plant growth tracker",
        "route": "plant_growth.html",
        "terms": "plant growth crop height measurement photo image compare history stage leaf health agronomy suggestion",
        "content": "Plant Growth records crop measurements and photos, compares measurements over time, and keeps recent analysis history in the browser. Advice depends on the crop and measurements provided by the farmer.",
    },
    {
        "name": "Farmer Subsidies",
        "route": "subsidies.html",
        "terms": "farmer subsidies government schemes PM Kisan PM-KISAN PM Kusum PM-KUSUM irrigation machinery eligibility application benefits",
        "content": "Farmer Subsidies provides a portal for browsing scheme information. Eligibility, amounts, deadlines, and application requirements can vary by state and change over time; verify them on the official government scheme portal before applying.",
    },
    {
        "name": "Water Stress AI",
        "route": "water_stress.html",
        "terms": "water stress irrigation soil moisture temperature humidity soil type days since watering CWSI water savings",
        "content": "Water Stress evaluates supplied soil moisture, temperature, humidity, soil type, days since watering, and acreage to estimate water stress and an irrigation requirement. Its output is a model estimate; check field conditions and local crop guidance before irrigating.",
    },
    {
        "name": "Farm Inspector",
        "route": "farm_inspector.html",
        "terms": "farm inspector upload image photo crop health weeds canopy moisture dry patches field image analysis",
        "content": "Farm Inspector accepts a farm image and crop selection. The current backend returns simulated example canopy, health, weed, and moisture results; these are not verified computer-vision findings from the uploaded image.",
    },
    {
        "name": "Input Cost",
        "route": "input_cost.html",
        "terms": "input cost tracker farm expenses fertilizer labor irrigation seed machinery budget per acre revenue profit ROI",
        "content": "Input Cost Tracker records farm expenses and can summarize costs by category and acreage. Revenue and return calculations require user-provided yield and selling price; they are estimates based on those inputs.",
    },
    {
        "name": "NPK Advisor",
        "route": "npk_advisor.html",
        "terms": "NPK advisor soil test nitrogen phosphorus potassium pH EC salinity moisture fertilizer manure crop stage dose recommendations sensor",
        "content": "NPK Advisor accepts crop, crop stage, and soil readings including N, P, K, pH, EC, moisture, and temperature, then compares readings with the app's crop ranges and produces fertilizer/advisory output. Its ranges and dose estimates are app guidance, not a laboratory soil test or a substitute for local agronomist and product-label recommendations.",
    },
    {
        "name": "Education",
        "route": "farmer_education.html",
        "terms": "education hub farmer learning courses guides resources training sustainable agriculture crop tutorials",
        "content": "Farmer Education contains agricultural learning resources and guides organized for browsing. Check the resource itself for its publisher, date, and applicability to the user's region.",
    },
    {
        "name": "News & Trends",
        "route": "agri_news.html",
        "terms": "news trends agriculture crop agricultural news latest updates market policy weather farming headlines",
        "content": "News & Trends displays agricultural news and updates available from the app's news source. A current or historical news answer must be based on returned articles and their publication dates, not model memory.",
    },
    {
        "name": "Orchestration",
        "route": "dashboard2.html",
        "terms": "orchestration agents planner risks farm pipeline candidate plans execution strategy telemetry report",
        "content": "Orchestration displays farm-agent pipeline reports, including ingested inputs, risk detection, candidate plans, constraint optimization, and execution scheduling. Reports depend on a pipeline run and its supplied farm data.",
    },
    {
        "name": "Weather & Alerts",
        "route": "weather.html",
        "terms": "weather alerts forecast rain temperature humidity wind Open-Meteo GPS farm location irrigation spray",
        "content": "Weather & Alerts requests current conditions and a seven-day forecast from Open-Meteo. The page uses GPS when allowed and otherwise its displayed default location. Forecasts are location- and time-dependent and should be retrieved before answering current weather questions.",
    },
    {
        "name": "Farm Finance",
        "route": "finance.html",
        "terms": "farm finance loan bank branches nearby agriculture financial planning credit rates fees repayment",
        "content": "Farm Finance provides educational finance guidance and can list nearby bank locations from OpenStreetMap. Bank data may be incomplete; confirm details with the branch. This page is not a loan offer or personal financial advice.",
    },
]


def _tokens(text: str) -> List[str]:
    return re.findall(r"[a-z0-9]+", text.casefold())


_DOCUMENT_TOKENS = [
    _tokens(f"{item['name']} {item['terms']} {item['content']}")
    for item in FEATURE_KNOWLEDGE
]
_DOCUMENT_FREQUENCIES = Counter(
    token for document in _DOCUMENT_TOKENS for token in set(document)
)
_AVERAGE_DOCUMENT_LENGTH = sum(map(len, _DOCUMENT_TOKENS)) / len(_DOCUMENT_TOKENS)
_DOCUMENT_VECTORS = []
for _document in _DOCUMENT_TOKENS:
    _frequencies = Counter(_document)
    _DOCUMENT_VECTORS.append({
        token: (1 + math.log(frequency))
        * math.log(1 + (len(_DOCUMENT_TOKENS) + 1) / (_DOCUMENT_FREQUENCIES[token] + 1))
        for token, frequency in _frequencies.items()
    })


def list_feature_knowledge() -> List[Dict[str, str]]:
    return [
        {
            "name": item["name"],
            "route": item["route"],
            "content": item["content"],
        }
        for item in FEATURE_KNOWLEDGE
    ]


def retrieve_feature_knowledge(query: str, limit: int = 4) -> List[Dict[str, str]]:
    """Retrieve app knowledge with local sparse TF-IDF vectors and BM25 ranking."""
    query_tokens = _tokens(query)
    if not query_tokens:
        return []

    query_counts = Counter(query_tokens)
    query_vector = {
        token: (1 + math.log(frequency))
        * math.log(1 + (len(_DOCUMENT_TOKENS) + 1) / (_DOCUMENT_FREQUENCIES[token] + 1))
        for token, frequency in query_counts.items()
        if _DOCUMENT_FREQUENCIES[token]
    }
    query_norm = math.sqrt(sum(weight * weight for weight in query_vector.values())) or 1.0
    scored = []

    for item, tokens, vector in zip(FEATURE_KNOWLEDGE, _DOCUMENT_TOKENS, _DOCUMENT_VECTORS):
        counts = Counter(tokens)
        length = len(tokens)
        bm25_score = 0.0
        for token, query_frequency in query_counts.items():
            frequency = counts[token]
            if not frequency:
                continue
            inverse_document_frequency = math.log(
                1 + (len(_DOCUMENT_TOKENS) - _DOCUMENT_FREQUENCIES[token] + 0.5)
                / (_DOCUMENT_FREQUENCIES[token] + 0.5)
            )
            denominator = frequency + 1.5 * (
                1 - 0.75 + 0.75 * length / _AVERAGE_DOCUMENT_LENGTH
            )
            term_frequency_score = frequency * 2.5 / denominator
            query_frequency_weight = 1 + math.log(query_frequency)
            bm25_score += inverse_document_frequency * term_frequency_score * query_frequency_weight

        dot_product = sum(
            weight * vector.get(token, 0.0)
            for token, weight in query_vector.items()
        )
        document_norm = math.sqrt(sum(weight * weight for weight in vector.values())) or 1.0
        cosine_score = dot_product / (query_norm * document_norm)
        if bm25_score > 0 or cosine_score > 0:
            scored.append((bm25_score, cosine_score, item))

    maximum_bm25 = max((score[0] for score in scored), default=1.0) or 1.0
    scored.sort(
        key=lambda result: (0.6 * result[0] / maximum_bm25) + (0.4 * result[1]),
        reverse=True
    )
    return [
        {
            "name": item["name"],
            "route": item["route"],
            "content": item["content"],
        }
        for _, _, item in scored[:limit]
    ]
