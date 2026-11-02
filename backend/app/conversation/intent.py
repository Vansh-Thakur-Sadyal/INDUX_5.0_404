"""Intent detection, query normalisation and entity extraction.

Rule-first by design: the keyword router runs in microseconds, costs nothing and
works when the LLM key is absent. When Mistral is configured the orchestrator may
ask it to confirm an ambiguous intent, but the rules remain the floor.

``normalise_query`` also maps common Hindi / Hinglish farm vocabulary to English
before retrieval. The knowledge base is written in English, so without this a
question typed as "mera khet me paani kab dena chahiye" retrieves almost nothing
when the offline embedder is in use.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# --------------------------------------------------------------- vocabulary
# Hindi / Hinglish -> English, limited to farm vocabulary this system reasons
# about. Values are appended to the query, never substituted, so the original
# wording is preserved for the LLM.
FARM_LEXICON: Dict[str, str] = {
    "paani": "water irrigation", "pani": "water irrigation", "sinchai": "irrigation",
    "khet": "field farm", "mitti": "soil", "zameen": "land soil",
    "khad": "fertiliser nutrient", "urvarak": "fertiliser", "urea": "urea nitrogen",
    "beej": "seed sowing", "bijai": "sowing", "boai": "sowing",
    "fasal": "crop", "fasl": "crop", "gehu": "wheat", "gehun": "wheat",
    "dhan": "rice paddy", "chawal": "rice", "sarson": "mustard", "chana": "chickpea",
    "makka": "maize", "ganna": "sugarcane", "kapas": "cotton", "aloo": "potato",
    "pyaaz": "onion", "pyaz": "onion", "kanda": "onion",
    "tamatar": "tomato", "tamaatar": "tomato",
    "bhindi": "okra", "baingan": "brinjal", "gobhi": "cauliflower",
    "patta gobhi": "cabbage", "matar": "peas", "lehsun": "garlic",
    "mirch": "chilli", "mirchi": "chilli", "haldi": "turmeric",
    "adrak": "ginger", "kela": "banana", "nariyal": "coconut",
    "arhar": "arhar", "tur": "tur", "moong": "moong", "urad": "urad",
    "masoor": "lentil", "soyabean": "soybean", "til": "sesame",
    "jau": "barley", "bajra": "bajra", "jowar": "jowar", "sorghum": "jowar",
    "keeda": "pest insect", "kida": "pest insect", "rog": "disease",
    "bimari": "disease", "patta": "leaf", "patte": "leaves", "peela": "yellow",
    "peele": "yellow", "peeli": "yellow", "pila": "yellow", "pile": "yellow",
    "sukh": "wilting drying", "sukhna": "wilting", "murjha": "wilting",
    "chhed": "holes", "ched": "holes", "dhabba": "spots", "dhabbe": "spots",
    "safed": "white powder", "powder": "white powder",
    "mudna": "curling", "mud": "curling", "sikud": "stunted",
    "sadan": "rotting", "sad": "rotting", "gal": "rotting",
    "kharpatwar": "weed", "ghas": "weed grass",
    "mandi": "market price", "bhav": "price rate", "daam": "price", "keemat": "price",
    "mausam": "weather", "barish": "rain rainfall", "baarish": "rain rainfall",
    "garmi": "heat temperature", "sardi": "cold frost", "paala": "frost",
    "nami": "moisture humidity", "upaj": "yield", "munafa": "profit",
    "lagat": "cost", "kharcha": "cost expense", "bech": "sell market",
    "parali": "crop residue straw", "bhusa": "straw residue",
    "sensor": "sensor reading", "jaanch": "test check",
}

# Devanagari -> English, as STEMS rather than whole words.
#
# This is the half that was missing, and it mattered more than it looks: the phone line
# transcribes spoken Hindi into Devanagari, so every spoken Hindi question scored no
# keywords at all, fell through to "general", and was answered as small talk. Romanised
# Hinglish typed on the dashboard worked fine, which is why it went unnoticed.
#
# Stems, because Hindi inflects: खेत becomes खेतों, फसल becomes फसलें, उगा becomes उगाने
# or उगाना. A prefix match covers the forms without listing each one.
DEVANAGARI_STEMS: Dict[str, str] = {
    "खेत": "field farm", "मिट्ट": "soil", "मिटट": "soil", "ज़मीन": "land soil",
    "जमीन": "land soil", "भूम": "land soil",
    "पानी": "water irrigation", "पान": "water irrigation", "सिंचाई": "irrigation",
    "सिचाई": "irrigation", "नमी": "moisture humidity",
    "खाद": "fertiliser nutrient", "उर्वरक": "fertiliser", "यूरिया": "urea nitrogen",
    "नाइट्रोजन": "nitrogen", "पोटाश": "potassium",
    "बीज": "seed sowing", "बुवाई": "sowing", "बुआई": "sowing", "बिजाई": "sowing",
    "बोन": "sowing", "रोप": "sowing transplant",
    "फसल": "crop", "फ़सल": "crop", "उगा": "grow crop selection",
    "लगा": "plant crop", "बोय": "sow crop",
    "गेहू": "wheat", "गेंहू": "wheat", "धान": "rice paddy", "चावल": "rice",
    "सरसो": "mustard", "सरसों": "mustard", "चना": "chickpea", "मक्का": "maize",
    "गन्ना": "sugarcane", "कपास": "cotton", "आलू": "potato", "प्याज": "onion",
    "टमाटर": "tomato", "सोयाबीन": "soybean", "बाजरा": "millet",
    "कीड़": "pest insect", "कीट": "pest insect", "रोग": "disease",
    "बीमार": "disease", "पत्त": "leaf", "पीला": "yellow", "पीले": "yellow",
    "धब्ब": "spots", "सूख": "drying wilting",
    "खरपतवार": "weed", "घास": "weed grass", "निराई": "weeding",
    "मंडी": "market price", "भाव": "price rate", "दाम": "price", "कीमत": "price",
    "बेच": "sell market", "मुनाफ": "profit", "लागत": "cost", "खर्च": "cost expense",
    "आमदन": "income profit",
    "मौसम": "weather", "बारिश": "rain rainfall", "बरसात": "rain rainfall",
    "गर्मी": "heat temperature", "तापमान": "temperature", "सर्दी": "cold frost",
    "पाला": "frost", "ओला": "hail", "हवा": "wind",
    "उपज": "yield", "पैदावार": "yield", "पराली": "crop residue straw",
    "भूसा": "straw residue", "जांच": "test check", "जाँच": "test check",
    "सेंसर": "sensor reading", "अवस्था": "stage growth", "बालियां": "grain stage",
    "छिड़क": "spray application", "दवा": "pesticide spray", "कीटनाशक": "pesticide",
}

INTENTS: Dict[str, Dict[str, object]] = {
    "soil_health": {
        "keywords": ["soil", "ph", "ec", "salinity", "organic matter", "soil test",
                     "mitti", "alkaline", "acidic", "soil health"],
        "topics": ["soil", "soil-health", "ph", "ec", "salinity"],
        "agents": [1, 2, 15],
    },
    "nutrient": {
        "keywords": ["fertiliser", "fertilizer", "urea", "dap", "npk", "nitrogen",
                     "phosphorus", "potassium", "potash", "nutrient", "khad", "mop",
                     "top dress", "basal", "manure", "compost"],
        "topics": ["nutrients", "npk", "fertiliser", "soil-health"],
        "agents": [1, 2, 4, 6, 15],
    },
    "irrigation": {
        "keywords": ["irrigate", "irrigation", "water", "watering", "moisture", "paani",
                     "sinchai", "drip", "sprinkler", "waterlogging", "how much water"],
        "topics": ["irrigation", "water", "soil-moisture"],
        "agents": [1, 2, 4, 5, 7, 15],
    },
    "crop_selection": {
        "keywords": ["which crop", "what crop", "crop should", "suitable crop", "sow next",
                     "crop selection", "rotation", "best crop", "kaun si fasal",
                     "कौन सी फसल", "कौनसी फसल", "क्या उगा", "क्या बोय", "फसल उगा",
                     "फसल लगा", "फसल चुन"],
        "topics": ["crop", "crop-selection", "agronomy"],
        "agents": [2, 3, 5, 10, 13, 15],
    },
    "crop_stage": {
        "keywords": ["stage", "tillering", "flowering", "sowing date", "growth", "days after",
                     "vegetative", "maturity", "harvest time"],
        "topics": ["stages", "agronomy", "crop"],
        "agents": [4, 5, 15],
    },
    "weather": {
        "keywords": ["weather", "rain", "forecast", "temperature", "frost", "heat", "wind",
                     "humidity", "mausam", "barish", "storm", "spray today"],
        "topics": ["weather", "risk"],
        "agents": [5, 15],
    },
    "pest_disease": {
        "keywords": ["pest", "disease", "insect", "fungus", "blight", "rust", "spot",
                     "wilt", "yellow", "caterpillar", "aphid", "borer", "keeda",
                     "rog", "infestation", "curl", "holes in", "powder", "stunted",
                     "symptom", "affected plants", "patches on"],
        "topics": ["pest", "disease", "ipm", "crop-protection"],
        "agents": [4, 5, 8, 11, 15],
    },
    "weed": {
        "keywords": ["weed", "weeds", "herbicide", "kharpatwar", "grass in field",
                     "phalaris", "weeding"],
        "topics": ["weeds", "crop-protection", "ipm"],
        "agents": [4, 9, 11, 15],
    },
    "seed_sowing": {
        "keywords": ["seed", "seed rate", "sowing", "sow", "spacing", "germination",
                     "nursery", "transplant", "variety", "beej"],
        "topics": ["seed", "sowing", "establishment"],
        "agents": [3, 5, 10, 15],
    },
    "crop_protection": {
        "keywords": ["spray", "pesticide", "insecticide", "fungicide", "dose", "dosage",
                     "label", "pre-harvest interval", "safe to spray"],
        "topics": ["crop-protection", "safety", "ipm"],
        "agents": [5, 8, 11, 15],
    },
    "market": {
        "keywords": ["price", "mandi", "market", "rate", "sell", "selling", "bhav",
                     "arrival", "quintal", "msp"],
        "topics": ["market", "prices", "economics"],
        "agents": [12, 13, 15],
    },
    "economics": {
        "keywords": ["profit", "cost", "income", "economics", "margin", "break even",
                     "investment", "loan", "munafa", "lagat"],
        "topics": ["economics", "cost", "profit"],
        "agents": [12, 13, 15],
    },
    # Three intents that are not agronomy. They route to no agent - the fifteen have
    # declared contracts and none of them is "government scheme" - and are answered
    # from the support service instead, which the orchestrator attaches to context.
    "scheme": {
        "keywords": ["scheme", "subsidy", "yojana", "sarkari", "government", "govt",
                     "pm kisan", "pmkisan", "kusum", "pmfby", "bima", "anudan",
                     "grant", "apply", "eligib"],
        "topics": ["policy"],
        "agents": [15],
    },
    "finance": {
        "keywords": ["loan", "credit", "kcc", "kisan credit", "interest", "byaj",
                     "insurance", "premium", "emi", "bank", "borrow", "karz",
                     "repay", "collateral"],
        "topics": ["economics", "cost"],
        "agents": [13, 15],
    },
    "support": {
        "keywords": ["helpline", "helpdesk", "contact", "phone number", "complaint",
                     "kisan call", "kvk", "officer", "madad", "shikayat", "where to go",
                     "whom to call"],
        "topics": ["safety", "policy"],
        "agents": [15],
    },
    # Money in and out of the farm, spoken or typed. Separate from "economics",
    # which is the break-even arithmetic rather than the record of what happened.
    "ledger": {
        "keywords": ["spent", "spend", "expense", "kharcha", "kharch", "cost me",
                     "paid", "bill", "record", "note down", "write down", "sold",
                     "earned", "income", "kamai", "total spend", "how much have i"],
        "topics": ["economics", "cost"],
        "agents": [13, 15],
    },
    # Booking the call bot to ring back later, or to watch for something. This has to
    # out-score the topic it mentions: "tell me the wheat price in 30 minutes" is a
    # booking, not a price question, and answering it now is the wrong reply.
    "task": {
        "keywords": ["remind me", "remind", "call me", "call back", "ring me",
                     "after 30", "later", "yaad dila", "yaad dilana", "baad mein",
                     "baad me", "call karna", "batana", "phone karna",
                     "\u092f\u093e\u0926 \u0926\u093f\u0932\u093e", "\u092c\u093e\u0926 \u092e\u0947\u0902", "\u092c\u0924\u093e\u0928\u093e", "\u0915\u0949\u0932 \u0915\u0930\u0928\u093e",
                     "set a reminder", "schedule a call", "wake me", "alert me",
                     "let me know when", "tell me when", "tell me if",
                     # A time unit next to a "later" marker is scheduling language and
                     # nothing else, whatever topic the rest of the sentence names.
                     "minute baad", "minutes baad", "mint baad", "ghante baad",
                     "ghanta baad", "din baad", "baad batana", "baad call",
                     "baad phone", "\u092e\u093f\u0928\u091f \u092c\u093e\u0926", "\u0918\u0902\u091f\u0947 \u092c\u093e\u0926", "\u0918\u0902\u091f\u093e \u092c\u093e\u0926",
                     "\u0926\u093f\u0928 \u092c\u093e\u0926", "\u092c\u093e\u0926 \u092c\u0924\u093e\u0928\u093e", "\u092c\u093e\u0926 \u092e\u0947\u0902 \u092c\u0924\u093e"],
        "topics": ["reminder", "callback"],
        "agents": [15],
    },
    "residue": {
        "keywords": ["residue", "stubble", "parali", "straw", "burning", "mulch",
                     "composting", "sustainab"],
        "topics": ["residue", "sustainability", "soil-health"],
        "agents": [14, 15],
    },
    "sensor": {
        "keywords": ["sensor", "device", "reading", "arduino", "ble", "hm-10", "probe",
                     "fetch", "bluetooth"],
        "topics": ["sensor", "data-quality"],
        "agents": [1, 2, 15],
    },
    "general": {
        "keywords": [],
        "topics": [],
        "agents": [1, 2, 4, 5, 15],
    },
    # Greetings, thanks and goodbyes. No agents at all: running the soil, stage
    # and weather chain over "hello" is what produced a full farm advisory in
    # answer to a greeting, complete with a moisture reading nobody asked for.
    "small_talk": {
        "keywords": [],
        "topics": [],
        "agents": [],
    },
}

# Said on its own, each of these is a greeting rather than a question. Matched
# against the whole message, not searched inside it, so "hello, my wheat is
# yellowing" is still a crop question.
GREETINGS = {
    "hi", "hii", "hiii", "hey", "helo", "hello", "hlo", "yo",
    "good morning", "good afternoon", "good evening", "good day",
    "namaste", "namaskar", "namaskaar", "pranam", "ram ram", "radhe radhe",
    "salaam", "salam", "assalamualaikum", "sat sri akal", "vanakkam", "nomoshkar",
    "नमस्ते", "नमस्कार", "प्रणाम", "राम राम", "सलाम",
}

THANKS = {
    "thanks", "thank you", "thanku", "thankyou", "ty", "shukriya", "dhanyavad",
    "dhanyawad", "bahut dhanyavad", "thanks a lot", "thank you so much",
    "धन्यवाद", "शुक्रिया",
}

FAREWELLS = {
    "bye", "goodbye", "good bye", "see you", "alvida", "phir milenge", "ok bye",
    "theek hai", "thik hai", "ok", "okay", "achha", "acha",
    "अलविदा", "ठीक है",
}

# "How are you", and the reply to it. These carry no question about the farm,
# and a chain run over them produces an advisory nobody asked for.
PLEASANTRIES = {
    "how are you", "how r u", "how are you doing", "hope you are well",
    "kaise ho", "kaise hain", "kaise hain aap", "aap kaise hain", "kaisi ho",
    "kya haal hai", "kya hal hai", "sab theek", "sab thik",
    "i am fine", "im fine", "fine", "good", "all good", "theek hoon",
    "main theek hoon", "haan ji", "haan", "ji haan", "ji",
    "कैसे हैं", "कैसे हैं आप", "कैसे हो", "क्या हाल है",
    "ठीक हूँ", "ठीक हूं", "मैं ठीक हूँ", "हाँ जी", "हां जी", "हाँ",
    "हाँ जी ठीक हूँ", "ठीक है", "सब ठीक",
}

# Asked of Saarthi rather than of the farm; answered without touching the chain.
ABOUT_SAARTHI = {
    "who are you", "what are you", "what can you do", "what do you do",
    "how can you help", "help", "what is saarthi", "aap kaun ho", "tum kaun ho",
    "kya kar sakte ho", "आप कौन हैं", "तुम कौन हो", "क्या कर सकते हो",
}


def small_talk_kind(text: str) -> Optional[str]:
    """Which kind of pleasantry this is, or None if it is a real question.

    Deliberately strict: the whole message has to be the pleasantry. A farmer
    who writes "hello, when should I irrigate" is asking about irrigation, and
    treating that as a greeting would be worse than the bug this fixes.
    """
    cleaned = (text or "").strip().lower()
    cleaned = re.sub(r"[!?.,;:\-]+$", "", cleaned).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned or len(cleaned) > 40:
        return None
    # "hi saarthi", "hello ji" - an address, still a greeting.
    stripped = re.sub(r"\b(saarthi|sarthi|ji|sir|madam|bhai|please|pls)\b", "",
                      cleaned).strip()
    stripped = re.sub(r"\s+", " ", stripped)
    for candidate in (cleaned, stripped):
        if candidate in GREETINGS:
            return "greeting"
        if candidate in THANKS:
            return "thanks"
        if candidate in FAREWELLS:
            return "farewell"
        if candidate in ABOUT_SAARTHI:
            return "about"
        if candidate in PLEASANTRIES:
            return "greeting"

    # A greeting with a pleasantry after it - "namaste, kaise hain aap" - is
    # still a greeting. Only when what follows is itself small talk: "namaste,
    # when should I irrigate" has to stay an irrigation question.
    for candidate in (cleaned, stripped):
        for greet in sorted(GREETINGS, key=len, reverse=True):
            if not candidate.startswith(greet):
                continue
            rest = candidate[len(greet):].strip(" ,.!")
            if not rest or rest in PLEASANTRIES or rest in THANKS or rest in FAREWELLS:
                return "greeting"
    return None

CROPS = [
    "wheat", "rice", "paddy", "maize", "cotton", "sugarcane", "mustard", "chickpea",
    "gram", "lentil", "barley", "potato", "onion", "tomato", "soybean", "groundnut",
    "bajra", "jowar", "millet", "sesame", "pulses",
]

STAGE_WORDS = {
    "sowing": "sowing", "germination": "germination", "seedling": "seedling",
    "tillering": "tillering", "vegetative": "vegetative", "jointing": "jointing",
    "booting": "booting", "flowering": "flowering", "anthesis": "flowering",
    "grain filling": "grain_filling", "milk": "grain_filling", "dough": "grain_filling",
    "maturity": "maturity", "harvest": "harvest",
}

URGENT_WORDS = ["poison", "poisoning", "swallowed", "hospital", "emergency", "fire",
                "snake bite", "unconscious", "zeher"]


@dataclass
class IntentResult:
    intent: str
    confidence: float
    topics: List[str] = field(default_factory=list)
    agents: List[int] = field(default_factory=list)
    entities: Dict[str, str] = field(default_factory=dict)
    normalised_query: str = ""
    urgent: bool = False
    scores: Dict[str, float] = field(default_factory=dict)


def normalise_query(text: str) -> str:
    """Append English equivalents of Hindi/Hinglish farm words to the query."""
    lowered = (text or "").lower()
    extras: List[str] = []
    for token in re.findall(r"[a-zऀ-ॿ]+", lowered):
        mapped = FARM_LEXICON.get(token)
        if mapped is None and _is_devanagari(token):
            mapped = _devanagari_meaning(token)
        if mapped and mapped not in extras:
            extras.append(mapped)
    if not extras:
        return text or ""
    return (text or "") + " " + " ".join(extras)


def _is_devanagari(token: str) -> bool:
    return any("ऀ" <= ch <= "ॿ" for ch in token)


def _devanagari_meaning(token: str) -> Optional[str]:
    """The English behind a Devanagari word, matched on its stem.

    Longest stem wins, so खेती does not match a shorter stem that happens to share its
    opening letters. Matching the stem anywhere in the token rather than only at the
    start catches the postposition forms ("खेतों", "फसलें") that spoken Hindi is full of.
    """
    best: Optional[str] = None
    best_len = 0
    for stem, meaning in DEVANAGARI_STEMS.items():
        if len(stem) > best_len and token.startswith(stem):
            best, best_len = meaning, len(stem)
    return best


def extract_entities(text: str) -> Dict[str, str]:
    lowered = (text or "").lower()
    entities: Dict[str, str] = {}
    for crop in CROPS:
        if re.search(r"\b" + crop + r"\b", lowered):
            entities["crop"] = "rice" if crop == "paddy" else crop
            break
    for word, stage in STAGE_WORDS.items():
        if word in lowered:
            entities["stage"] = stage
            break
    acres = re.search(r"(\d+(?:\.\d+)?)\s*(acre|acres|bigha|hectare|ha)\b", lowered)
    if acres:
        entities["area"] = acres.group(0)
    phone = re.search(r"(?:\+91[\s-]?)?[6-9]\d{9}\b", text or "")
    if phone:
        entities["phone"] = phone.group(0)
    return entities


_KEYWORD_CACHE: Dict[str, "re.Pattern[str]"] = {}


def _keyword_pattern(keyword: str) -> "re.Pattern[str]":
    """Whole-word match with a tolerant plural/participle suffix.

    Substring matching was wrong in a way that was easy to miss: 'heat' fired on
    'wheat', so every question mentioning wheat scored as a weather question.
    """
    if keyword not in _KEYWORD_CACHE:
        _KEYWORD_CACHE[keyword] = re.compile(
            r"(?<![a-z])" + re.escape(keyword) + r"(s|es|ing|ed)?(?![a-z])")
    return _KEYWORD_CACHE[keyword]


# Words that name the kind of help being asked for, not the topic it is about. They
# carry extra weight so a question like "subsidy for a drip set" is read as a question
# about money, which is what it is, rather than one about irrigation.
DECISIVE: Dict[str, float] = {
    "subsidy": 2.5, "scheme": 2.5, "yojana": 2.5, "anudan": 2.5, "sarkari": 2.0,
    "pm kisan": 3.0, "pmkisan": 3.0, "kusum": 2.5, "pmfby": 2.5,
    "kisan credit": 3.0, "kcc": 2.5, "loan": 2.0, "karz": 2.0, "byaj": 2.0,
    "insurance": 2.0, "bima": 2.0, "premium": 1.5,
    "spent": 2.5, "expense": 2.5, "kharcha": 2.5, "how much have i": 3.0,
    "total spend": 3.0, "earned": 2.0, "income": 2.0,
    "helpline": 3.0, "helpdesk": 2.5, "kisan call": 3.0, "shikayat": 2.0,
    "complaint": 2.0, "kvk": 2.5,
}


def detect(text: str) -> IntentResult:
    """Score every intent by weighted keyword hits; longer keywords weigh more."""
    # Checked first and on the raw words: normalise_query() appends the English
    # behind Hindi farm words, which can only add farm keywords to a greeting.
    kind = small_talk_kind(text)
    if kind:
        return IntentResult(
            intent="small_talk",
            confidence=0.95,
            topics=[],
            agents=[],
            entities={"small_talk": kind},
            normalised_query=(text or "").strip(),
            urgent=False,
            scores={},
        )

    normalised = normalise_query(text)
    lowered = normalised.lower()
    scores: Dict[str, float] = {}
    for name, spec in INTENTS.items():
        keywords: List[str] = list(spec["keywords"])          # type: ignore[arg-type]
        hit = 0.0
        for keyword in keywords:
            if _keyword_pattern(keyword).search(lowered):
                # multi-word and longer keywords are stronger evidence
                weight = 1.0 + 0.35 * keyword.count(" ") + min(len(keyword), 12) / 40.0
                hit += weight * DECISIVE.get(keyword, 1.0)
        if hit:
            scores[name] = round(hit, 3)
    if not scores:
        best, confidence = "general", 0.3
    else:
        best = max(scores, key=lambda k: scores[k])
        top = scores[best]
        total = sum(scores.values())
        confidence = round(min(0.95, 0.45 + 0.5 * (top / total)), 3)
    spec = INTENTS[best]
    return IntentResult(
        intent=best,
        confidence=confidence,
        topics=list(spec["topics"]),                           # type: ignore[arg-type]
        agents=list(spec["agents"]),                           # type: ignore[arg-type]
        # Entities off the NORMALISED query, not the raw text. The crop
        # names in CROPS are English; spoken Hindi arrives as Devanagari, so
        # reading the raw text found no crop in "टमाटर का भाव" - and for a price
        # question the crop is the whole question.
        entities=extract_entities(normalised),
        normalised_query=normalised,
        urgent=any(word in lowered for word in URGENT_WORDS),
        scores=scores,
    )


def needs_clarification(result: IntentResult, text: str) -> Optional[str]:
    """One short question when the request is too thin to act on."""
    words = len((text or "").split())
    if words <= 2 and result.intent == "general":
        return "Could you tell me a little more about what you need help with on your farm?"
    if result.intent in ("pest_disease", "weed") and "crop" not in result.entities:
        return "Which crop is affected, and what do you see on the plant?"
    if result.intent == "market" and "crop" not in result.entities:
        return "Which crop's price would you like, and which market?"
    return None
