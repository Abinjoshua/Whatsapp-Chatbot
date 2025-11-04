import os
import re
import json
import random
from datetime import datetime, timedelta
from dotenv import load_dotenv
from openai import OpenAI
import dateparser

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# -------------------------------
# Category & Subcategory Mapping
# -------------------------------
CATEGORY_MAP = {
    "care at home": [
        "nurse visit", "physiotherapy", "elderly care", "post surgery care"
    ],
    "medicine delivery": [
        "regular medicines", "urgent medicines", "upload prescription"
    ],
    "lab test": [
        "blood test", "urine test", "covid test", "full body checkup"
    ]
}

# BUTTON mappings used by main.py
BUTTON_MAPPINGS = {
    "date_today": "today",
    "date_tomorrow": "tomorrow",
    "date_pick": "pick",
    "time_morning": "morning",
    "time_afternoon": "afternoon",
    "time_evening": "evening",
    "care_at_home": "care at home",
    "medicine_delivery": "medicine delivery",
    "lab_test": "lab test",
    "nurse_visit": "nurse visit",
    "physiotherapy": "physiotherapy",
    "elderly_care": "elderly care",
    "post_surgery_care": "post surgery care",
    "regular_meds": "regular medicines",
    "urgent_meds": "urgent medicines",
    "prescription_upload": "upload prescription",
    "blood_test": "blood test",
    "urine_test": "urine test",
    "covid_test": "covid test",
    "full_body_checkup": "full body checkup",
    "share_location": "share_location",
    "type_address": "type_address",
    "confirm_yes": "confirm_yes",
    "confirm_no": "confirm_no"
}

# -------------------------------
# Humanization templates & helpers
# -------------------------------
REPLY_VARIANTS = {
    "greeting": [
        "Hey {name}! 👋 How can I help you today?",
        "Hi {name}! 👋 Warmy here — how can I assist?",
        "Hello {name}! I’m here for you. What would you like to do today?"
    ],
    "ack_location": [
        "Thanks — got your address: {location}. I’ll assign the nearest staff.",
        "Perfect, I’ve noted {location}. I’ll find someone nearby for you.",
        "Thanks! Location saved: {location}. We’ll route the nearest staff."
    ],
    "confirm_summary": [
        "✅ Here’s your appointment summary:\n{summary}\nWould you like me to confirm this now? (Yes / No)",
        "Looks good — here’s what I have:\n{summary}\nShall I lock this in for you?"
    ],
    "confirmation_yes": [
        "✅ All set — your appointment is confirmed. If you need anything else, just ask!",
        "Done! ✅ Appointment confirmed. Anything more I can help with?"
    ],
    "confirmation_no": [
        "Okay — I’ve cancelled that. Would you like to book something else?",
        "No worries — it’s cancelled. Want to start a new booking?"
    ],
    "fallback": [
        "I didn’t quite get that — could you say it another way?",
        "Hmm, I might’ve missed that. Can you rephrase it for me?"
    ],
    "friendly_ack": [
        "Got it — {summary}.",
        "Perfect — {summary}.",
        "Thanks, noted: {summary}."
    ],
    "empathetic": [
        "I’m sorry you’re dealing with that. I’ll help however I can.",
        "That sounds tough — I’ll do my best to help."
    ]
}

def humanize_response(seed_text: str = "", kind: str = None, name: str = None, emotion: str = None, **kwargs):
    """
    Return a friendly, variable response. If `kind` is set, pick a template.
    Otherwise lightly wrap the original text for human tone.
    """
    try:
        if kind in REPLY_VARIANTS:
            template = random.choice(REPLY_VARIANTS[kind])
            if name:
                kwargs.setdefault("name", str(name).split()[0])
            return template.format(**kwargs).strip()
        # emotion-aware lightweight wrappers for generic replies
        prefix_by_emotion = {
            "happy": ["🙂", "😊"],
            "neutral": [""],
            "sad": ["I’m sorry you’re going through that.", "I’m here to help."],
            "angry": ["I hear you.", "I’ll fix this together with you."],
            "urgent": ["I’ve got you.", "Let’s sort this quickly."],
        }
        choices = prefix_by_emotion.get((emotion or "neutral").lower(), [""])
        chosen = random.choice(choices)
        if chosen and chosen in {"🙂", "😊"}:
            return f"{chosen} {seed_text}".strip()
        if chosen:
            return f"{chosen} {seed_text}".strip()
        # neutral fallbacks
        wrappers = [
            lambda t: t,
            lambda t: f"Sure — {t}",
            lambda t: f"Got it. {t}",
        ]
        return random.choice(wrappers)(seed_text)
    except Exception:
        return seed_text

# -------------------------------
# Sanitizers / helpers
# -------------------------------
def sanitize_text_value(s):
    if s is None:
        return None
    s = re.sub(r'[\u200B-\u200F\uFEFF]', '', str(s))
    s = re.sub(r'\s+', ' ', s).strip()
    return s

def _normalize_keys(d: dict) -> dict:
    clean = {}
    for k, v in (d or {}).items():
        try:
            key = str(k).strip()
        except Exception:
            key = k
        clean[key] = v
    return clean

def normalize_category_for_compare(cat):
    if not cat:
        return ""
    return str(cat).replace("_", " ").strip().lower()

# -------------------------------
# Date/time normalization
# -------------------------------
def normalize_date_time(date_str, time_str=None):
    friendly_times = {
        "morning": "09:00",
        "afternoon": "15:00",
        "evening": "18:00",
        "night": "20:00"
    }
    if not date_str and not time_str:
        return None, None
    if time_str and isinstance(time_str, str) and time_str.lower() in friendly_times:
        norm_date = None
        if date_str:
            try:
                dt = dateparser.parse(date_str)
                norm_date = dt.strftime("%Y-%m-%d") if dt else None
            except Exception:
                norm_date = None
        return norm_date, friendly_times[time_str.lower()]
    if date_str and isinstance(date_str, str):
        parts = date_str.lower().split()
        for token in friendly_times.keys():
            if token in parts:
                date_only = " ".join([p for p in parts if p != token])
                try:
                    dt = dateparser.parse(date_only) if date_only.strip() else None
                except Exception:
                    dt = None
                norm_date = dt.strftime("%Y-%m-%d") if dt else None
                return norm_date, friendly_times[token]
    if date_str and time_str:
        dt = dateparser.parse(f"{date_str} {time_str}")
        if not dt:
            return date_str, time_str
        return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")
    if date_str and not time_str:
        dt = dateparser.parse(date_str)
        if not dt:
            return date_str, None
        return dt.strftime("%Y-%m-%d"), None
    if time_str and not date_str:
        dt = dateparser.parse(time_str)
        if not dt:
            return None, time_str
        return None, dt.strftime("%H:%M")
    return None, None

# -------------------------------
# Fast rule-based extractor (first pass)
# -------------------------------
def rule_based_extract(user_text: str, previous_entities: dict):
    """Quick extraction for common, explicit phrasings (fast path)."""
    if not user_text or not isinstance(user_text, str):
        return None
    ut = user_text.lower().strip()
    detected_category = None
    detected_sub = None

    # keywords
    nurse_keywords = ["home nurse", "nurse visit", "nurse at home", "nurse"]
    # dentist/dental intents should map to care at home; we default sub to nurse visit for routing
    dentist_keywords = ["dentist", "dental", "toothache", "tooth pain", "teeth cleaning", "tooth cleaning", "tooth extraction", "root canal"]
    care_at_home_keywords = ["care at home", "home care", "at-home care", "careathome"]
    medicine_keywords = ["medicine delivery", "deliver meds", "deliver medicine", "delivery", "medicine", "meds"]
    lab_keywords = ["lab test", "blood test", "urine test", "covid test", "full body checkup", "full-body"]

    if any(k in ut for k in nurse_keywords) or any(k in ut for k in care_at_home_keywords) or any(k in ut for k in dentist_keywords):
        detected_category = "care at home"
        if any(k in ut for k in nurse_keywords) or any(k in ut for k in dentist_keywords):
            detected_sub = "nurse visit"
        elif "physio" in ut or "physiotherapy" in ut:
            detected_sub = "physiotherapy"
    elif any(k in ut for k in medicine_keywords):
        detected_category = "medicine delivery"
        if "urgent" in ut:
            detected_sub = "urgent medicines"
        elif "refill" in ut or "regular" in ut:
            detected_sub = "regular medicines"
    elif any(k in ut for k in lab_keywords):
        detected_category = "lab test"
        for sub in CATEGORY_MAP.get("lab test", []):
            if sub in ut:
                detected_sub = sub
                break

    detected_date = None
    detected_time = None

    # time words
    if re.search(r"\bmorning\b", ut):
        detected_time = "09:00"
    elif re.search(r"\bafternoon\b", ut):
        detected_time = "15:00"
    elif re.search(r"\bevening\b", ut):
        detected_time = "18:00"
    else:
        # explicit times like 3pm, 15:00 etc
        m = re.search(r"(\b\d{1,2}(:\d{2})?\s*(am|pm)\b)|\b\d{1,2}(:\d{2})\b", ut)
        if m:
            ts = m.group(0)
            dt = dateparser.parse(ts)
            if dt:
                detected_time = dt.strftime("%H:%M")

    # date detection (prefer future)
    dt = dateparser.parse(ut, settings={"PREFER_DATES_FROM": "future"})
    if dt:
        # accept if explicit day words or numeric dates
        if any(tok in ut for tok in ["tomorrow", "today", "next", "on", "monday", "tuesday",
                                     "wednesday", "thursday", "friday", "saturday", "sunday"]) or re.search(r"\b\d{1,2}\b", ut):
            detected_date = dt.strftime("%Y-%m-%d")
        else:
            if dt.date() != datetime.now().date():
                detected_date = dt.strftime("%Y-%m-%d")

    entities = previous_entities.copy() if previous_entities else {}
    changed = False
    if detected_category and not entities.get("category"):
        entities["category"] = detected_category
        changed = True
    if detected_sub and not entities.get("sub_category"):
        entities["sub_category"] = detected_sub
        changed = True
    if detected_date and not entities.get("date"):
        entities["date"] = detected_date
        changed = True
    if detected_time and not entities.get("time"):
        entities["time"] = detected_time
        changed = True

    if not changed:
        return None

    # short humanized confirmation fragment
    parts = []
    if entities.get("sub_category"):
        parts.append(entities["sub_category"].title())
    elif entities.get("category"):
        parts.append(entities["category"].title())
    if entities.get("date"):
        parts.append(f"on {entities['date']}")
    if entities.get("time"):
        parts.append(f"at {entities['time']}")
    resp_text = " ".join(parts) if parts else "Got it — noted."
    return {
        "intent": "appointment_request",
        "sentiment": "neutral",
        "entities": entities,
        "response": resp_text
    }

# -------------------------------
# Conversational fallback (LLM)
# -------------------------------
def conversational_answer(user_text: str, previous_entities: dict):
    """
    Short, empathetic reply for general queries or when extraction didn't apply.
    """
    try:
        qa_prompt = f"""
You are Warmy, a warm and friendly healthcare assistant on WhatsApp.
User said: "{user_text}"
Answer in 1-2 short sentences, empathetically and clearly. If it's a booking request, ask for the missing info.
Keep tone human, use emojis sparingly.
"""
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are Warmy, a warm and friendly healthcare assistant."},
                {"role": "user", "content": qa_prompt}
            ],
            temperature=0.35
        )
        reply = resp.choices[0].message.content.strip()
        return {
            "intent": "general_query",
            "sentiment": None,
            "entities": previous_entities,
            "response": reply
        }
    except Exception as e:
        print("❌ conversational_answer LLM error:", e)
        return {
            "intent": "general_query",
            "sentiment": None,
            "entities": previous_entities,
            "response": "Sorry — I’m having a little trouble. Could you rephrase?"
        }

# -------------------------------
# Small LLM-based emotion/sentiment probe
# -------------------------------
def analyze_emotion_and_sentiment(user_text: str):
    """
    Ask the LLM to return a simple 'emotion' label and sentiment.
    Returns: (emotion:str, sentiment:str) where emotion ∈ {happy, neutral, sad, angry, urgent}
    sentiment ∈ {positive, neutral, negative}
    """
    try:
        probe = f"""
Classify the emotional tone of this message in one word (choose from: happy, neutral, sad, angry, urgent)
and give a sentiment label (positive, neutral, negative).
Return JSON only like: {{ "emotion": "...", "sentiment": "..." }}
Message: \"{user_text}\"
"""
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"system","content":"You are a short-label classifier."},
                      {"role":"user","content":probe}],
            temperature=0.0
        )
        txt = resp.choices[0].message.content.strip()
        # try to extract JSON
        first = txt.find("{")
        last = txt.rfind("}")
        if first != -1 and last != -1 and last>first:
            j = txt[first:last+1]
            parsed = json.loads(j)
            return parsed.get("emotion","neutral"), parsed.get("sentiment","neutral")
        # fallback simple heuristics
        t = txt.lower()
        if "urgent" in t or "emergency" in t or "asap" in t:
            return "urgent","negative"
        return "neutral","neutral"
    except Exception as e:
        print("❌ emotion probe error:", e)
        return "neutral","neutral"

# -------------------------------
# Main LLM extraction + logic
# -------------------------------
def process_user_message(user_text, previous_entities=None):
    """
    Returns:
    {
      "intent": "...",
      "sentiment": "...",
      "emotion": "...",
      "entities": {...},
      "response": "..."
    }
    """
    # map button ids -> labels only if exact match (avoid mapping generic text)
    if isinstance(user_text, str) and user_text in BUTTON_MAPPINGS:
        user_text = BUTTON_MAPPINGS[user_text]

    user_text = str(user_text).strip()
    # default session template
    if previous_entities is None:
        previous_entities = {
            "name": None, "age": None, "date": None, "time": None,
            "category": None, "sub_category": None, "location": None,
            "location_coords": None, "location_address": None,
            "awaiting_address": False, "confirmed": False, "greeted": False, "state": None
        }

    # quick greeting/restart handling
    lower = user_text.lower()
    if lower in ["hi", "hello", "hey", "hey warmy"]:
        previous_entities["greeted"] = True
        resp = humanize_response("", kind="greeting", name=previous_entities.get("name") or "")
        return {"intent":"greeting","sentiment":"neutral","emotion":"neutral","entities":previous_entities,"response":resp}

    if any(kw in lower for kw in ["start over","restart","book new","book another","new appointment"]):
        previous_entities = {
            "name": None, "age": None, "date": None, "time": None,
            "category": None, "sub_category": None, "location": None,
            "location_coords": None, "location_address": None,
            "awaiting_address": False, "confirmed": False, "greeted": True, "state": None
        }
        return {"intent":"start_over","sentiment":"neutral","emotion":"neutral","entities":previous_entities,"response":"No problem! Let’s start fresh. What service would you like to book today?"}

    # 1) fast rule-based extraction (used as a booster, not an early return)
    rb = rule_based_extract(user_text, previous_entities)
    rb_entities = None
    rb_response = None
    if rb:
        # normalize date/time from RB
        date_in, time_in = rb["entities"].get("date"), rb["entities"].get("time")
        nd, nt = normalize_date_time(date_in, time_in)
        rb["entities"]["date"], rb["entities"]["time"] = nd, nt
        for k in ["name","age","date","time","category","sub_category","location","location_coords","location_address","awaiting_address","confirmed","greeted","state"]:
            rb["entities"].setdefault(k, None if k not in ["confirmed","greeted","awaiting_address"] else False)
        rb_entities = rb["entities"]
        rb_response = rb.get("response", "")

    # 2) LLM extraction (if rule path didn't fill anything)
    # supply allowed subcategories hint when category pre-known
    allowed_subcats_text = ""
    if previous_entities.get("category"):
        cat_norm = normalize_category_for_compare(previous_entities.get("category"))
        allowed = CATEGORY_MAP.get(cat_norm)
        if allowed:
            allowed_subcats_text = f"\nAllowed subcategories for '{cat_norm}': {', '.join(allowed)}. If you extract sub_category, use one of them."

    prompt = f"""
You are Warmy, a helpful healthcare assistant on WhatsApp.

Known info (existing values MUST be preserved, only fill missing):
{json.dumps(previous_entities, indent=2)}

User message: \"{user_text}\"

Your task:
- Extract appointment details: date, time, category, sub_category, location, name, age.
- Interpret relative phrases: today/tomorrow/next <day>. Map times of day: morning=09:00, afternoon=15:00, evening=18:00, night=20:00.
- Map intents like \"home nurse\", \"at home nurse\", \"nurse at home\" → category="care at home" and sub_category="nurse visit".
- Map dental-at-home phrases (\"dentist\", \"dental\", \"toothache\", \"teeth cleaning\") → category="care at home" and sub_category="nurse visit" (routing default).
- If category is known, only allow sub_category from the allowed list below; otherwise leave sub_category null.
- Normalize: date as YYYY-MM-DD, time as HH:MM.
- Do NOT overwrite any already provided field.

Return only JSON:
{{ "intent":"...", "sentiment":"...", "entities":{{...}}, "response":"..." }}
{allowed_subcats_text}

Examples:
1) Input: "tomorrow morning for a home nurse"
   Output JSON (shape): {{"intent":"appointment_request","entities":{{"date":"<YYYY-MM-DD for tomorrow>","time":"09:00","category":"care at home","sub_category":"nurse visit"}},"response":"..."}}
2) Input: "book dentist at my home this evening"
   → time="18:00", category="care at home", sub_category="nurse visit".
"""
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"system","content":"You are Warmy, a warm and friendly healthcare assistant."},
                      {"role":"user","content":prompt}],
            temperature=0.25
        )
        reply_text = response.choices[0].message.content.strip()
        # extract JSON block
        first = reply_text.find("{")
        last = reply_text.rfind("}")
        json_text = reply_text[first:last+1] if first != -1 and last != -1 and last>first else None
        if not json_text:
            matches = re.findall(r"\{.*?\}", reply_text, re.DOTALL)
            json_text = matches[-1] if matches else None
        if not json_text:
            raise ValueError("No JSON found in LLM response")
        result = json.loads(json_text)
    except Exception as e:
        print("❌ LLM error in process_user_message:", e)
        # fallback to conversational answer
        conv = conversational_answer(user_text, previous_entities)
        emotion, sentiment = analyze_emotion_and_sentiment(user_text)
        conv["emotion"], conv["sentiment"] = emotion, sentiment
        conv["response"] = humanize_response(conv.get("response",""), name=previous_entities.get("name"))
        return conv

    # normalize and merge (combine RB and LLM, with precedence: previous_entities -> RB -> LLM)
    raw_entities = result.get("entities", {}) or {}
    normalized_entities = _normalize_keys(raw_entities)
    clean_llm = {k: v for k, v in normalized_entities.items() if v is not None and v != ""}
    merged = dict(previous_entities)
    if rb_entities:
        for k, v in rb_entities.items():
            if v not in [None, ""] and not merged.get(k):
                merged[k] = v
    for k, v in clean_llm.items():
        if v not in [None, ""] and not merged.get(k):
            merged[k] = v

    # sanitize text fields
    for key in ("category","sub_category","location","name"):
        if merged.get(key):
            merged[key] = sanitize_text_value(merged[key])

    # enforce allowed subcategory when category exists
    if merged.get("category"):
        cat_norm = normalize_category_for_compare(merged.get("category"))
        allowed_list = CATEGORY_MAP.get(cat_norm)
        if allowed_list:
            allowed_norm = [s.strip().lower() for s in allowed_list]
            sub_raw = merged.get("sub_category")
            if sub_raw and str(sub_raw).strip().lower() not in allowed_norm:
                merged["sub_category"] = None

    # normalize date/time
    date_input, time_input = merged.get("date"), merged.get("time")
    norm_date, norm_time = normalize_date_time(date_input, time_input)
    merged["date"], merged["time"] = norm_date, norm_time

    # ensure expected keys
    for k in ["name","age","date","time","category","sub_category","location","location_coords","location_address","awaiting_address","confirmed","greeted","state"]:
        if k not in merged:
            merged[k] = None if k not in ["confirmed","greeted","awaiting_address"] else False
        if k == "awaiting_address" and merged.get(k) is None:
            merged[k] = False

    result["entities"] = merged

    # emotion probe & humanize
    emotion, sentiment = analyze_emotion_and_sentiment(user_text)
    result["emotion"] = emotion
    result["sentiment"] = sentiment

    # friendly wrapping (emotion-aware)
    candidate_resp = result.get("response","") or rb_response or ""
    result["response"] = humanize_response(candidate_resp, name=merged.get("name"), emotion=emotion)

    return result

# export
__all__ = ["process_user_message", "BUTTON_MAPPINGS", "humanize_response", "sanitize_text_value", "normalize_date_time"]
