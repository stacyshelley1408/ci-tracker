import os
import re

CATEGORIES = [
    "pricing",
    "messaging",
    "product",
    "hiring",
    "partnership",
    "funding",
    "leadership",
    "customer",
    "threat_intel",
    "community",
    "review",
    "competitive_positioning",
    "press",
]

# Keyword rules for fallback classification when Anthropic API is unavailable
KEYWORD_RULES = {
    "funding": ["funding", "raises", "series a", "series b", "series c", "investment", "venture", "valuation", "ipo", "acquisition", "acquires", "acquired", "merger"],
    "pricing": ["pricing", "price", "plan", "tier", "subscription", "cost", "free trial", "enterprise plan", "per user", "per month"],
    "product": ["launch", "release", "new feature", "product update", "introducing", "announce", "general availability", "ga ", "beta", "integration"],
    "hiring": ["hiring", "job", "role", "position", "opening", "career", "head of", "vp of", "director of", "engineer", "manager"],
    "leadership": ["ceo", "cto", "cmo", "cro", "cfo", "chief", "joins as", "appointed", "named ", "steps down", "departs", "resigns", "executive"],
    "partnership": ["partner", "integration", "alliance", "collaboration", "works with", "powered by", "certified"],
    "customer": ["customer", "case study", "client", "wins", "deployed", "chose", "selected"],
    "competitive_positioning": ["vs ", "versus", "compare", "comparison", "alternative", "switch from", "better than", "unlike"],
    "threat_intel": ["threat", "malware", "ransomware", "phishing", "vulnerability", "cve", "exploit", "attack", "breach", "advisory"],
    "press": ["award", "recognized", "gartner", "forrester", "idc", "analyst", "magic quadrant", "wave", "peer insights", "named a"],
    "review": ["review", "rating", "stars", "feedback", "g2", "capterra", "trustradius", "gartner peer"],
    "community": ["reddit", "forum", "community", "r/msp", "r/sysadmin"],
    "messaging": ["messaging", "positioning", "homepage", "tagline", "value prop", "headline", "rebrand"],
}

SCORE_RULES = {
    "funding": 5,
    "leadership": 5,
    "pricing": 4,
    "product": 4,
    "competitive_positioning": 4,
    "partnership": 4,
    "hiring": 3,
    "customer": 3,
    "press": 3,
    "messaging": 3,
    "threat_intel": 3,
    "review": 3,
    "community": 2,
}


def _classify_by_keywords(signal_type: str, text: str) -> tuple[str, int]:
    text_lower = text.lower()

    # Signal type gives a strong prior for some categories
    signal_priors = {
        "job_posting": "hiring",
        "review": "review",
        "reddit": "community",
        "news": None,
        "messaging_diff": "messaging",
    }
    prior = signal_priors.get(signal_type)

    best_category = prior or "messaging"
    best_score = SCORE_RULES.get(best_category, 2)

    for category, keywords in KEYWORD_RULES.items():
        if any(kw in text_lower for kw in keywords):
            candidate_score = SCORE_RULES.get(category, 2)
            if candidate_score > best_score:
                best_category = category
                best_score = candidate_score

    return best_category, best_score


def _classify_with_anthropic(signal_type: str, text: str, watch_keywords: list[str]) -> tuple[str, int]:
    import anthropic

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    categories_str = ", ".join(CATEGORIES)
    keywords_str = ", ".join(watch_keywords) if watch_keywords else "none"

    prompt = f"""You are a competitive intelligence analyst. Classify the following signal from a B2B cybersecurity company tracker.

Signal type: {signal_type}
Watch keywords (high-priority terms for this company): {keywords_str}

Signal content:
{text[:1500]}

Respond with exactly two lines:
CATEGORY: <one of: {categories_str}>
SCORE: <integer 1-5>

Scoring guide:
5 - Major move, act today (funding round, acquisition, product launch, pricing overhaul, exec departure)
4 - Significant, worth tracking (messaging shift, new integration, notable hire, partnership)
3 - Moderate signal, include in digest (new blog topic, minor page copy change, new review, job posting)
2 - Low signal, log only (footer tweak, minor wording change, low-engagement post)
1 - Noise, ignore (nav update, formatting change, spam)

If any watch keywords appear in the content, bias the score upward by 1."""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=50,
        messages=[{"role": "user", "content": prompt}],
    )

    response = message.content[0].text.strip()
    category = "messaging"
    score = 2

    for line in response.splitlines():
        if line.startswith("CATEGORY:"):
            raw = line.split(":", 1)[1].strip().lower()
            if raw in CATEGORIES:
                category = raw
        elif line.startswith("SCORE:"):
            match = re.search(r"\d", line)
            if match:
                score = max(1, min(5, int(match.group())))

    return category, score


def _classify_with_gemini(signal_type: str, text: str, watch_keywords: list[str]) -> tuple[str, int]:
    import google.generativeai as genai

    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    model_name = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash-lite")
    model = genai.GenerativeModel(model_name)

    categories_str = ", ".join(CATEGORIES)
    keywords_str = ", ".join(watch_keywords) if watch_keywords else "none"

    prompt = f"""You are a competitive intelligence analyst. Classify the following signal from a B2B enterprise software company tracker.

Signal type: {signal_type}
Watch keywords (high-priority terms for this company): {keywords_str}

Signal content:
{text[:1500]}

Respond with exactly two lines:
CATEGORY: <one of: {categories_str}>
SCORE: <integer 1-5>

Scoring guide:
5 - Major move, act today (funding round, acquisition, product launch, pricing overhaul, exec departure)
4 - Significant, worth tracking (messaging shift, new integration, notable hire, partnership)
3 - Moderate signal, include in digest (new blog topic, minor page copy change, new review, job posting)
2 - Low signal, log only (footer tweak, minor wording change, low-engagement post)
1 - Noise, ignore (nav update, formatting change, spam)

If any watch keywords appear in the content, bias the score upward by 1."""

    response = model.generate_content(prompt)
    text_response = response.text.strip()
    category = "messaging"
    score = 2

    for line in text_response.splitlines():
        if line.startswith("CATEGORY:"):
            raw = line.split(":", 1)[1].strip().lower()
            if raw in CATEGORIES:
                category = raw
        elif line.startswith("SCORE:"):
            match = re.search(r"\d", line)
            if match:
                score = max(1, min(5, int(match.group())))

    return category, score


def classify(event: dict, watch_keywords: list[str]) -> dict:
    """
    Adds haiku_category and haiku_score to an event dict.
    Priority: Gemini > Anthropic > keyword rules.
    Set GEMINI_API_KEY for the default free-tier classifier.
    Set ANTHROPIC_API_KEY to use Claude Haiku instead (or as fallback).
    If neither key is set, falls back to deterministic keyword rules.
    """
    text = event.get("raw_diff", "")
    signal_type = event.get("signal_type", "")

    if os.environ.get("GEMINI_API_KEY"):
        try:
            category, score = _classify_with_gemini(signal_type, text, watch_keywords)
        except Exception as e:
            print(f"[classify] Gemini failed, trying Anthropic: {e}")
            if os.environ.get("ANTHROPIC_API_KEY"):
                try:
                    category, score = _classify_with_anthropic(signal_type, text, watch_keywords)
                except Exception as e2:
                    print(f"[classify] Anthropic failed, falling back to keywords: {e2}")
                    category, score = _classify_by_keywords(signal_type, text)
            else:
                category, score = _classify_by_keywords(signal_type, text)
    elif os.environ.get("ANTHROPIC_API_KEY"):
        try:
            category, score = _classify_with_anthropic(signal_type, text, watch_keywords)
        except Exception as e:
            print(f"[classify] Anthropic failed, falling back to keywords: {e}")
            category, score = _classify_by_keywords(signal_type, text)
    else:
        category, score = _classify_by_keywords(signal_type, text)

    return {**event, "haiku_category": category, "haiku_score": score}
