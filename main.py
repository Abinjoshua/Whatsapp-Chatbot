from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
import os
import json
import requests
from dotenv import load_dotenv
from llm_utils import process_user_message, BUTTON_MAPPINGS, humanize_response, sanitize_text_value, normalize_date_time
from datetime import datetime, timedelta
from typing import Optional
import re

load_dotenv()
app = FastAPI()

WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")

if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
    print("⚠️ Missing WhatsApp env vars. Please set WHATSAPP_TOKEN and PHONE_NUMBER_ID.")

session_data = {}
# Track processed WhatsApp message IDs to avoid duplicate handling
processed_message_ids = set()

def make_empty_session():
    return {
        "name": None, "age": None, "date": None, "time": None,
        "category": None, "sub_category": None, "location": None,
        "location_coords": None, "location_address": None,
        "awaiting_address": False,  # your existing flag
        "awaiting_field": None,     # new: which field we're currently waiting for
        "confirmed": False, "greeted": False, "state": "main_menu", "last_interaction": None
    }


def safe_post(url, headers, payload):
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        try:
            resp.raise_for_status()
        except Exception:
            print("[WhatsApp API error] status:", resp.status_code, "text:", resp.text)
        return resp
    except Exception as e:
        print("[WhatsApp Request Exception]", e)
        return None

def send_buttons(user_number, question, buttons):
    """
    Validate number of buttons (1-3). If >3 use list fallback.
    `buttons` is a dict {id: title}
    """
    if not buttons:
        return
    n = len(buttons)
    if n < 1:
        return
    if n > 3:
        # fallback to list
        # build single section rows
        section = {"title": question or "Options", "rows": [{"id": k, "title": v, "description": ""} for k, v in buttons.items()]}
        send_list(user_number, question, question, None, [section])
        return
    url = f"https://graph.facebook.com/v21.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": user_number,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": question},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": key, "title": title}}
                    for key, title in buttons.items()
                ]
            }
        }
    }
    resp = safe_post(url, headers, payload)
    if resp:
        print(f"[Button Sent] {resp.status_code} - {resp.text}")

def send_list(user_number, header_text, body_text, footer_text, sections):
    url = f"https://graph.facebook.com/v21.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    interactive = {
        "type": "list",
        "header": {"type": "text", "text": header_text} if header_text else None,
        "body": {"text": body_text},
        "footer": {"text": footer_text} if footer_text else None,
        "action": {
            "button": "Choose",
            "sections": sections
        }
    }
    interactive = {k: v for k, v in interactive.items() if v is not None}
    payload = {"messaging_product": "whatsapp", "to": user_number, "type": "interactive", "interactive": interactive}
    resp = safe_post(url, headers, payload)
    if resp:
        print(f"[List Sent] {resp.status_code} - {resp.text}")

def send_text(user_number, text):
    text = text.encode("utf-8","ignore").decode()
    url = f"https://graph.facebook.com/v21.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    payload = {"messaging_product":"whatsapp","to":user_number,"type":"text","text":{"body":text}}
    resp = safe_post(url, headers, payload)
    if resp:
        print(f"[Text Sent] {resp.status_code} - {resp.text}")

def normalize_entity_keys(raw_entities):
    clean = {}
    for k,v in (raw_entities or {}).items():
        key = str(k).strip()
        clean[key] = v
    return clean

def normalize_cat(cat: Optional[str]) -> str:
    if not cat:
        return ""
    return str(cat).replace("_", " ").strip().lower()

def build_rows_from_options(options_dict):
    rows=[]
    for k,v in options_dict.items():
        rows.append({"id":k,"title":v,"description":""})
    return rows

def send_options(user_number, title, body, options_dict):
    if not options_dict: return
    n = len(options_dict)
    if 1 <= n <= 3:
        send_buttons(user_number, body, options_dict)
        return
    section = {"title": title or "Options", "rows": build_rows_from_options(options_dict)}
    send_list(user_number, title, body, None, [section])

def sanitize_text_value_local(s):
    if s is None: return None
    s = re.sub(r'[\u200B-\u200F\uFEFF]', '', str(s))
    s = re.sub(r'\s+', ' ', s).strip()
    return s

# Build a user-friendly appointment summary.
# For medicine delivery, show prescription/medicine details instead of sub-service.
def compose_summary(sess: dict) -> str:
    def val(k):
        return sess.get(k)

    lines = []
    if val('date'):
        lines.append(f"• Date: {val('date')}")
    if val('time'):
        lines.append(f"• Time: {val('time')}")
    if val('age'):
        lines.append(f"• Age: {val('age')}")
    if val('category'):
        lines.append(f"• Service: {str(val('category')).title()}")

    # Details line — prefer prescription/medicine text for medicine delivery
    cat_norm = str(val('category') or '').strip().lower()
    if cat_norm == 'medicine delivery':
        if val('prescription_uploaded'):
            lines.append("• Prescription: received")
        elif val('medicine_text'):
            lines.append(f"• Medicine details: {val('medicine_text')}")
        else:
            sc = val('sub_category')
            if sc:
                if str(sc).lower() == "send doctor's prescription":
                    lines.append("• Prescription: pending upload")
                elif str(sc).lower() == 'type the medicine':
                    lines.append("• Medicine details: pending")
                else:
                    lines.append(f"• Details: {str(sc).title()}")
    else:
        sc = val('sub_category')
        if sc:
            lines.append(f"• Sub-service: {str(sc).title()}")

    if val('location'):
        lines.append(f"• Location: {val('location')}")

    return "\n".join(lines)

@app.get("/webhook")
async def verify(request: Request):
    params = dict(request.query_params)
    if params.get("hub.verify_token") == VERIFY_TOKEN:
        print("✅ Webhook verified successfully.")
        return PlainTextResponse(params.get("hub.challenge"))
    print("❌ Webhook verification failed.")
    return PlainTextResponse("Verification failed")

@app.post("/webhook")
async def webhook_handler(request: Request):
    try:
        data = await request.json()
        print("\n================= 🌐 Incoming Webhook =================")
        try:
            obj = data.get("object")
            has_messages = bool(((data.get("entry") or [{}])[0].get("changes") or [{}])[0].get("value", {}).get("messages"))
            print({"object": obj, "has_messages": has_messages})
        except Exception:
            print("incoming webhook received")

        entries = data.get("entry") or []
        if not entries:
            print("⚠️ No entry in webhook.")
            return {"status":"ignored"}
        entry = entries[0] or {}
        changes = entry.get("changes") or []
        if not changes:
            print("⚠️ No changes in entry.")
            return {"status":"ignored"}
        change = changes[0] or {}
        value = change.get("value") or {}
        messages = value.get("messages") or []
        if not messages:
            print("⚠️ No messages found in webhook.")
            return {"status":"ignored"}
        message = messages[0] or {}
        message_id = message.get("id")
        if message_id:
            if message_id in processed_message_ids:
                print(f"⚠️ Duplicate message id {message_id}; ignoring")
                return {"status":"ignored"}
            # simple cap to avoid unbounded growth
            if len(processed_message_ids) > 2000:
                processed_message_ids.clear()
            processed_message_ids.add(message_id)
        user_number = message.get("from")
        print(f"📞 From: {user_number}")
        is_interactive = "interactive" in message
        user_name = None
        try:
            user_name = (value.get("contacts") or [])[0].get("profile", {}).get("name")
        except Exception:
            user_name = None
        print(f"👤 Contact name: {user_name or 'N/A'}")
        user_text = ""
        if "text" in message:
            user_text = message["text"].get("body", "")
        elif "interactive" in message:
            interactive_obj = message["interactive"]
            if "button_reply" in interactive_obj:
                user_text = interactive_obj["button_reply"].get("id", "")
            elif "list_reply" in interactive_obj:
                user_text = interactive_obj["list_reply"].get("id", "")
        print(f"💬 Raw user text / id: {user_text}")
        
        # Debug: Log message types for media detection
        print(f"📋 Message keys: {list(message.keys())}")
        if "image" in message:
            print("🖼️ Image detected in message")
        if "document" in message:
            print("📄 Document detected in message")

        # handle location messages first
        if "location" in message:
            loc = message.get("location") or {}
            loc_name = loc.get("name") or loc.get("address")
            lat = loc.get("latitude"); lon = loc.get("longitude")
            coords = None
            if lat is not None and lon is not None:
                try: coords = (float(lat), float(lon))
                except Exception: coords = None
            address = loc_name or (f"{coords[0]},{coords[1]}" if coords else None)
            if address or coords:
                sess = session_data.get(user_number) or make_empty_session()
                sess["location"] = address
                sess["location_address"] = address
                sess["location_coords"] = f"{coords[0]},{coords[1]}" if coords else None
                sess["last_interaction"] = "location"
                if sess.get("awaiting_address"): sess.pop("awaiting_address", None)
                # also clear awaiting_field if it was "location"
                if sess.get("awaiting_field") == "location":
                    sess["awaiting_field"] = None
                session_data[user_number] = sess
                friendly_loc = sess.get("location_address") or sess.get("location_coords")
                send_text(user_number, humanize_response("", kind="ack_location", location=friendly_loc))
                if all([sess.get("date"), sess.get("time"), sess.get("category"), sess.get("sub_category"), sess.get("location"), sess.get("age")]) and sess.get("time") not in [None,"","00:00"]:
                    summary_text = compose_summary(sess)
                    send_text(user_number, humanize_response("", kind="confirm_summary", summary=summary_text, name=sess.get("name")))
                    send_buttons(user_number, "Confirm booking?", {"confirm_yes":"Yes","confirm_no":"No"})
                    sess["state"]="confirming"
                    sess.pop("awaiting_address", None)
                    session_data[user_number] = sess
                    print(f"Session for {user_number}: {json.dumps(sess)}")
                    return {"status":"ok"}
                # ask next missing
                if not sess.get("time"):
                    # 1. If awaiting_field is set, do not send a new question yet
                    if sess.get("awaiting_field") is not None:
                        return {"status":"ok"}

                    # 2. Send only interactive buttons to avoid duplicate prompt lines
                    send_buttons(user_number, "Select preferred time:", {"time_morning":"Morning","time_afternoon":"Afternoon","time_evening":"Evening"})

                    # 3. Mark that we are waiting for "time" answer
                    sess["awaiting_field"] = "time"

                    # 4. Save updated session data
                    session_data[user_number] = sess

                    # 5. Return immediately to avoid multiple sends
                    return {"status":"ok"}

                elif not sess.get("date"):
                    if sess.get("awaiting_field") is not None:
                        return {"status":"ok"}
                    send_text(user_number, "Please provide the date for the appointment.")
                    send_buttons(user_number, "Please select appointment date:", {"date_today":"Today","date_tomorrow":"Tomorrow","date_pick":"Pick another date"})
                    sess["awaiting_field"] = "date"
                    session_data[user_number] = sess
                    return {"status":"ok"}

                elif not sess.get("category"):
                    send_text(user_number, "Which service would you like to book?")
                    send_buttons(user_number, "What would you like to book today?", {"care_at_home":"Care at Home","medicine_delivery":"Medicine Delivery","lab_test":"Lab Test"})
                elif not sess.get("sub_category"):
                    cat = normalize_cat(sess.get("category"))
                    if cat=="care at home":
                        send_options(user_number, "Care at Home", "Select a subcategory for Care at Home:", {"nurse_visit":"Nurse Visit","physiotherapy":"Physiotherapy","elderly_care":"Elderly Care","post_surgery_care":"Post Surgery Care"})
                    elif cat=="medicine delivery":
                        send_options(user_number, "Medicine Delivery", "How would you like to provide the medicine details?", {
                            "send_doctors_prescription": "Send Prescription",
                            "type_the_medicine": "Type Medicine"
                        })
                    elif cat=="lab test":
                        send_options(user_number, "Lab Test", "Select a subcategory for Lab Test:", {"blood_test":"Blood Test","urine_test":"Urine Test","covid_test":"COVID Test","full_body_checkup":"Full Body Checkup"})
                    else:
                        send_text(user_number, "Please tell me which sub-service you want.")
                session_data[user_number]=sess
                return {"status":"ok"}

        normalized = (user_text or "").strip().lower()
        if normalized in {"yes","y","confirm","ok","sure"} or user_text == "confirm_yes":
            sess = session_data.get(user_number,{}) or {}
            if sess and sess.get("state") == "confirming":
                # Build a structured record of the confirmed session
                record = {
                    "confirmed_at": datetime.now().isoformat(),
                    "user_number": user_number,
                    "name": sess.get("name"),
                    "age": sess.get("age"),
                    "category": sess.get("category"),
                    "sub_category": sess.get("sub_category"),
                    "date": sess.get("date"),
                    "time": sess.get("time"),
                    "location_address": sess.get("location_address"),
                    "location_coords": sess.get("location_coords"),
                    "state": sess.get("state"),
                    "last_interaction": sess.get("last_interaction")
                }
                try:
                    print("\n✅ Booking Confirmed — Structured Session Record")
                    print(json.dumps(record, indent=2, ensure_ascii=False))
                    # Also persist a snapshot for debugging/ops
                    filename = f"session_{user_number}.json"
                    with open(filename, "w", encoding="utf-8") as f:
                        json.dump(record, f, indent=2, ensure_ascii=False)
                except Exception as e:
                    print("⚠️ Failed to write structured session record:", e)

                send_text(user_number, humanize_response("", kind="confirmation_yes", name=sess.get("name")))
                session_data[user_number] = make_empty_session()
                return {"status":"ok"}
            else:
                # If user says yes outside confirming flow, treat as wanting to proceed with a new booking
                send_buttons(user_number, "What would you like to book today?", {"care_at_home":"Care at Home","medicine_delivery":"Medicine Delivery","lab_test":"Lab Test"})
                return {"status":"ok"}
        elif normalized in {"no","cancel","stop"} or user_text == "confirm_no":
            sess = session_data.get(user_number,{}) or {}
            if sess and sess.get("state") == "confirming":
                send_text(user_number, humanize_response("", kind="confirmation_no", name=sess.get("name")))
                session_data[user_number] = make_empty_session()
                return {"status":"ok"}
            else:
                # Acknowledge and end politely without invoking the LLM
                send_text(user_number, "Okay — if you need anything later, just say hi.")
                return {"status":"ok"}

        # Allow media messages (image/document) even when there's no textual content
        has_media = ("image" in message) or ("document" in message)
        if not user_number or (not user_text and not has_media):
            print("⚠️ Missing user number or text.")
            return {"status":"ignored"}

        GREETING_TEXTS = {"hi","hello","hey","hey warmy"}
        if not is_interactive and normalized in GREETING_TEXTS:
            session_data[user_number] = make_empty_session()
            session_data[user_number]["last_interaction"] = "greeting"
            send_text(user_number, humanize_response("", kind="greeting", name=(user_name or "")))
            send_buttons(user_number, "What would you like to book today?", {"care_at_home":"Care at Home","medicine_delivery":"Medicine Delivery","lab_test":"Lab Test"})
            return {"status":"ok"}

        if user_text.lower() == "test":
            send_text(user_number, "✅ Bot is working fine!")
            return {"status":"ok"}

        prev_entities = session_data.get(user_number, None)

        # capture typed address (if awaiting)
        if prev_entities and prev_entities.get("awaiting_address") and "text" in message:
            address_text = (user_text or "").strip()
            if not address_text:
                send_text(user_number, "I didn't catch that. Please type your address or share your location.")
                return {"status":"ok"}
            sess = session_data.get(user_number,{}) or make_empty_session()
            sess["location"] = sanitize_text_value_local(address_text)
            sess["location_address"] = sanitize_text_value_local(address_text)
            sess["location_coords"] = None
            sess["awaiting_address"] = False
            # clear awaiting_field if it corresponds to location
            if sess.get("awaiting_field") == "location":
                sess["awaiting_field"] = None
            sess["last_interaction"] = "typed_address"
            session_data[user_number]=sess
            send_text(user_number, humanize_response("", kind="ack_location", location=address_text, name=sess.get("name")))
            if all([sess.get("date"), sess.get("time"), sess.get("category"), sess.get("sub_category"), sess.get("location"), sess.get("age")]) and sess.get("time") not in [None,"","00:00"]:
                summary_text = compose_summary(sess)
                send_text(user_number, humanize_response("", kind="confirm_summary", summary=summary_text, name=sess.get("name")))
                send_buttons(user_number, "Confirm booking?", {"confirm_yes":"Yes","confirm_no":"No"})
                sess["state"]="confirming"
                session_data[user_number]=sess
                return {"status":"ok"}
            if not sess.get("time"):
                # Check if another question is already outstanding
                if sess.get("awaiting_field") is not None:
                    return {"status":"ok"}
                send_buttons(user_number, "Select preferred time:", {"time_morning":"Morning","time_afternoon":"Afternoon","time_evening":"Evening"})
                # Mark we are waiting for the "time" response
                sess["awaiting_field"] = "time"
                session_data[user_number] = sess
                return {"status":"ok"}

            elif not sess.get("date"):
                send_text(user_number, "Please provide the date for the appointment.")
                send_buttons(user_number, "Please select appointment date:", {"date_today":"Today","date_tomorrow":"Tomorrow","date_pick":"Pick another date"})
                session_data[user_number]=sess
                return {"status":"ok"}
            elif not sess.get("category"):
                send_text(user_number, "Which service would you like to book?")
                send_buttons(user_number, "What would you like to book today?", {"care_at_home":"Care at Home","medicine_delivery":"Medicine Delivery","lab_test":"Lab Test"})
                session_data[user_number]=sess
                return {"status":"ok"}
            elif not sess.get("sub_category"):
                cat = normalize_cat(sess.get("category"))
                if cat=="care at home":
                    send_options(user_number, "Care at Home", "Select a subcategory for Care at Home:", {"nurse_visit":"Nurse Visit","physiotherapy":"Physiotherapy","elderly_care":"Elderly Care","post_surgery_care":"Post Surgery Care"})
                elif cat=="medicine delivery":
                    send_options(user_number, "Medicine Delivery", "How would you like to provide the medicine details?", {
                        "send_doctors_prescription": "Send Prescription",
                        "type_the_medicine": "Type Medicine"
                    })
                elif cat=="lab test":
                    send_options(user_number, "Lab Test", "Select a subcategory for Lab Test:", {"blood_test":"Blood Test","urine_test":"Urine Test","covid_test":"COVID Test","full_body_checkup":"Full Body Checkup"})
                else:
                    send_text(user_number, "Please tell me which sub-service you want.")
                session_data[user_number]=sess
                return {"status":"ok"}

        # interactive handlers mapping (date/time etc)
        interactive_time_map = {"morning":"09:00","afternoon":"15:00","evening":"18:00"}
        interactive_date_map = {"today": lambda: datetime.now().strftime("%Y-%m-%d"), "tomorrow": lambda: (datetime.now()+timedelta(days=1)).strftime("%Y-%m-%d")}

        if prev_entities is None:
            prev_entities = make_empty_session()

        # capture typed age (if awaiting)
        if prev_entities and prev_entities.get("awaiting_field") == "age" and "text" in message:
            age_text = (user_text or "").strip()
            m = re.search(r"(\d{1,3})", age_text)
            if not m:
                send_text(user_number, "I didn't catch that. Please type the age as a number (e.g. 32).")
                return {"status":"ok"}
            try:
                age_val = int(m.group(1))
            except Exception:
                send_text(user_number, "Please provide a valid numeric age (e.g. 32).")
                return {"status":"ok"}
            if age_val <= 0 or age_val > 120:
                send_text(user_number, "That age looks off — please enter an age between 1 and 120.")
                return {"status":"ok"}
            sess = session_data.get(user_number,{}) or make_empty_session()
            sess["age"] = str(age_val)
            sess["awaiting_field"] = None
            sess["last_interaction"] = "typed_age"
            session_data[user_number] = sess
            send_text(user_number, f"Thanks — noted age: {age_val}.")
            # If all required fields now present, show summary & ask confirmation
            if all([sess.get("date"), sess.get("time"), sess.get("category"), sess.get("sub_category"), sess.get("location"), sess.get("age")]) and sess.get("time") not in [None, "", "00:00"]:
                summary_text = compose_summary(sess)
                send_text(user_number, humanize_response("", kind="confirm_summary", summary=summary_text, name=sess.get("name")))
                send_buttons(user_number, "Confirm booking?", {"confirm_yes":"Yes","confirm_no":"No"})
                sess["state"] = "confirming"
                session_data[user_number] = sess
                return {"status":"ok"}

        mapped_text = BUTTON_MAPPINGS.get(user_text, user_text)
        mapped_text = str(mapped_text).strip().lower()
        for prefix in ("date_","time_","confirm_","btn_"):
            if mapped_text.startswith(prefix):
                mapped_text = mapped_text[len(prefix):]; break

        # Subcategory selection handlers for Medicine Delivery
        if mapped_text in {"send doctor's prescription", "type the medicine"}:
            sess = session_data.get(user_number) or make_empty_session()
            sess["sub_category"] = mapped_text
            if mapped_text == "send doctor's prescription":
                sess["awaiting_field"] = "prescription_upload"
                session_data[user_number] = sess
                send_text(user_number, "Please send the prescription as a PDF or image.")
                return {"status":"ok"}
            else:
                sess["awaiting_field"] = "medicine_text"
                session_data[user_number] = sess
                send_text(user_number, "Please type the medicine name(s) you need.")
                return {"status":"ok"}

        # Direct category selection handler for interactive buttons
        if mapped_text in {"care at home", "medicine delivery", "lab test"}:
            # Update session with chosen category and prompt subcategory options
            prev_entities = session_data.get(user_number) or make_empty_session()
            prev_entities["category"] = mapped_text
            prev_entities["awaiting_field"] = "sub_category"
            session_data[user_number] = prev_entities

            if mapped_text == "care at home":
                send_options(user_number, "Care at Home", "Select a subcategory for Care at Home:", {
                    "nurse_visit": "Nurse Visit",
                    "physiotherapy": "Physiotherapy",
                    "elderly_care": "Elderly Care",
                    "post_surgery_care": "Post Surgery Care"
                })
            elif mapped_text == "medicine delivery":
                send_options(user_number, "Medicine Delivery", "How would you like to provide the medicine details?", {
                    "send_doctors_prescription": "Send Prescription",
                    "type_the_medicine": "Type Medicine"
                })
            else:  # lab test
                send_options(user_number, "Lab Test", "Select a subcategory for Lab Test:", {
                    "blood_test": "Blood Test",
                    "urine_test": "Urine Test",
                    "covid_test": "COVID Test",
                    "full_body_checkup": "Full Body Checkup"
                })
            return {"status":"ok"}

        # Direct subcategory selection handlers for Care at Home and Lab Test
        if mapped_text in {"nurse visit", "physiotherapy", "elderly care", "post surgery care",
                           "blood test", "urine test", "covid test", "full body checkup"}:
            sess = session_data.get(user_number) or make_empty_session()
            # infer category from choice if not already set
            if mapped_text in {"nurse visit", "physiotherapy", "elderly care", "post surgery care"}:
                sess.setdefault("category", "care at home")
            else:
                sess.setdefault("category", "lab test")
            # set chosen subcategory and clear any pending prompt for it
            sess["sub_category"] = mapped_text
            if sess.get("awaiting_field") == "sub_category":
                sess["awaiting_field"] = None
            session_data[user_number] = sess

            # Ask the next missing field in order: date -> time -> age -> location
            priority = ["date", "time", "age", "location"]
            def is_missing(field):
                val = sess.get(field)
                return (val is None) or (str(val).strip() == "") or (field == "time" and val == "00:00")
            next_field = None
            for f in priority:
                if is_missing(f):
                    next_field = f
                    break
            if next_field == "date":
                send_buttons(user_number, "Please select appointment date:", {
                    "date_today": "Today",
                    "date_tomorrow": "Tomorrow",
                    "date_pick": "Pick another date"
                })
                sess["awaiting_field"] = "date"
                session_data[user_number] = sess
                return {"status":"ok"}
            elif next_field == "time":
                send_buttons(user_number, "Select preferred time:", {
                    "time_morning": "Morning",
                    "time_afternoon": "Afternoon",
                    "time_evening": "Evening"
                })
                sess["awaiting_field"] = "time"
                session_data[user_number] = sess
                return {"status":"ok"}
            elif next_field == "age":
                send_text(user_number, "Please type the patient's age (in years).")
                sess["awaiting_field"] = "age"
                session_data[user_number] = sess
                return {"status":"ok"}
            elif next_field == "location":
                send_text(user_number, "Please share your location (📎 → Location) or type your address.")
                sess["awaiting_field"] = "location"
                sess["awaiting_address"] = True
                session_data[user_number] = sess
                return {"status":"ok"}

        # If user is typing medicine names, capture and move forward
        if not is_interactive:
            sess = session_data.get(user_number) or make_empty_session()
            if sess.get("awaiting_field") == "medicine_text" and (user_text or "").strip():
                sess["medicine_text"] = sanitize_text_value_local(user_text)
                sess["awaiting_field"] = None
                # If category not set, default to medicine delivery for this flow
                if not sess.get("category"):
                    sess["category"] = "medicine delivery"
                if not sess.get("sub_category"):
                    sess["sub_category"] = "type the medicine"
                session_data[user_number] = sess
                send_text(user_number, humanize_response("Medicine details noted.", kind="friendly_ack", summary=sess.get("medicine_text")))

                # Ask next missing field(s)
                priority = ["date", "time", "age", "location"]
                def is_missing(field):
                    val = sess.get(field)
                    return (val is None) or (str(val).strip() == "") or (field == "time" and val == "00:00")
                next_field = None
                for f in priority:
                    if is_missing(f):
                        next_field = f
                        break
                if next_field == "date":
                    send_buttons(user_number, "Please select appointment date:", {"date_today":"Today","date_tomorrow":"Tomorrow","date_pick":"Pick another date"})
                    sess["awaiting_field"] = "date"
                    session_data[user_number] = sess
                    return {"status":"ok"}
                elif next_field == "time":
                    send_buttons(user_number, "Select preferred time:", {"time_morning":"Morning","time_afternoon":"Afternoon","time_evening":"Evening"})
                    sess["awaiting_field"] = "time"
                    session_data[user_number] = sess
                    return {"status":"ok"}
                elif next_field == "age":
                    send_text(user_number, "Please type the patient's age (in years).")
                    sess["awaiting_field"] = "age"
                    session_data[user_number] = sess
                    return {"status":"ok"}
                elif next_field == "location":
                    send_text(user_number, "Please share your location (📎 → Location) or type your address.")
                    sess["awaiting_field"] = "location"
                    session_data[user_number] = sess
                    return {"status":"ok"}

        # Handle prescription uploads: image or document
        print(f"🔍 Checking for media - awaiting_field: {prev_entities.get('awaiting_field') if prev_entities else 'None'}")
        if "image" in message or "document" in message:
            print("📁 Media detected, checking session...")
            sess = session_data.get(user_number) or make_empty_session()
            print(f"📊 Session awaiting_field: {sess.get('awaiting_field')}")
            if sess.get("awaiting_field") == "prescription_upload":
                media_kind = "image" if "image" in message else "document"
                media_obj = message.get(media_kind) or {}
                sess["prescription_uploaded"] = True
                sess["prescription_media_id"] = media_obj.get("id")
                sess["awaiting_field"] = None
                print(f"✅ Prescription media processed - media_id: {media_obj.get('id')}")
                if not sess.get("category"):
                    sess["category"] = "medicine delivery"
                if not sess.get("sub_category"):
                    sess["sub_category"] = "send doctor's prescription"
                session_data[user_number] = sess
                send_text(user_number, "Thanks — prescription received. ✅")

                # Ask next missing field(s)
                priority = ["date", "time", "age", "location"]
                def is_missing(field):
                    val = sess.get(field)
                    return (val is None) or (str(val).strip() == "") or (field == "time" and val == "00:00")
                next_field = None
                for f in priority:
                    if is_missing(f):
                        next_field = f
                        break
                if next_field == "date":
                    send_buttons(user_number, "Please select appointment date:", {"date_today":"Today","date_tomorrow":"Tomorrow","date_pick":"Pick another date"})
                    sess["awaiting_field"] = "date"
                    session_data[user_number] = sess
                    return {"status":"ok"}
                elif next_field == "time":
                    send_buttons(user_number, "Select preferred time:", {"time_morning":"Morning","time_afternoon":"Afternoon","time_evening":"Evening"})
                    sess["awaiting_field"] = "time"
                    session_data[user_number] = sess
                    return {"status":"ok"}
                elif next_field == "age":
                    send_text(user_number, "Please type the patient's age (in years).")
                    sess["awaiting_field"] = "age"
                    session_data[user_number] = sess
                    return {"status":"ok"}
                elif next_field == "location":
                    send_text(user_number, "Please share your location (📎 → Location) or type your address.")
                    sess["awaiting_field"] = "location"
                    session_data[user_number] = sess
                    return {"status":"ok"}

        if mapped_text == "type_address":
            sess = session_data.get(user_number) or make_empty_session()
            sess["awaiting_address"] = True
            sess["last_interaction"] = "awaiting_address_prompt"
            session_data[user_number]=sess
            send_text(user_number, "Please type your address now, or share location using WhatsApp's location button.")
            return {"status":"ok"}
        if mapped_text == "share_location":
            send_text(user_number, "Please use the attachment (📎) → Location → Send to share your location.")
            return {"status":"ok"}
        if mapped_text in ("pick","pick_date"):
            sess = session_data.get(user_number) or make_empty_session()
            sess["awaiting_field"] = "date"
            session_data[user_number] = sess
            send_text(user_number, "Please type the appointment date (YYYY-MM-DD), or say 'today' / 'tomorrow'.")
            return {"status":"ok"}

        # ---------- DATE handler (single, authoritative) ----------
        if mapped_text in interactive_date_map:
            chosen_date = interactive_date_map[mapped_text]()
            prev_entities["date"] = chosen_date
            prev_entities["last_interaction"] = "date_selected"

            # If we were explicitly waiting for "date", clear that because the user provided it.
            if prev_entities.get("awaiting_field") == "date":
                prev_entities["awaiting_field"] = None

            # persist immediately
            session_data[user_number] = prev_entities
            send_text(user_number, f"Date set to {chosen_date}.")

            # priority: ask TIME first, then AGE, then LOCATION (then category/sub_category if still missing)
            priority = ["time", "age", "location", "category", "sub_category"]

            def is_missing(field):
                val = prev_entities.get(field)
                return (val is None) or (str(val).strip() == "") or (field == "time" and val == "00:00")

            next_field = None
            for f in priority:
                if is_missing(f):
                    next_field = f
                    break

            # Ask only the next missing field and set awaiting_field accordingly
            if next_field == "time":
                if prev_entities.get("awaiting_field") is None:
                    send_buttons(user_number, "Select preferred time:", {
                        "time_morning": "Morning",
                        "time_afternoon": "Afternoon",
                        "time_evening": "Evening"
                    })
                    prev_entities["awaiting_field"] = "time"
                    session_data[user_number] = prev_entities
                    return {"status":"ok"}

            elif next_field == "age":
                if prev_entities.get("awaiting_field") is None:
                    send_text(user_number, "Please type the patient's age (in years).")
                    prev_entities["awaiting_field"] = "age"
                    session_data[user_number] = prev_entities
                    return {"status":"ok"}

            elif next_field == "location":
                sess = session_data.get(user_number) or prev_entities
                if not sess.get("awaiting_address") and prev_entities.get("awaiting_field") is None:
                    sess["awaiting_address"] = True
                    sess["last_interaction"] = "asked_for_address"
                    # Mark that awaiting_field corresponds to location (for typed location flow)
                    sess["awaiting_field"] = "location"
                    session_data[user_number] = sess
                    send_text(user_number, humanize_response("Please share your location or type your address so we can assign the nearest staff.", name=prev_entities.get("name")))
                    return {"status":"ok"}
                else:
                    session_data[user_number] = sess
                    return {"status":"ok"}

            elif next_field == "category":
                if prev_entities.get("awaiting_field") is None:
                    send_buttons(user_number, "What would you like to book today?", {
                        "care_at_home": "Care at Home",
                        "medicine_delivery": "Medicine Delivery",
                        "lab_test": "Lab Test"
                    })
                    prev_entities["awaiting_field"] = "category"
                    session_data[user_number] = prev_entities
                    return {"status":"ok"}

            elif next_field == "sub_category":
                cat = normalize_cat(prev_entities.get("category"))
                if cat == "care at home":
                    send_options(user_number, "Care at Home", "Select a subcategory for Care at Home:", {
                        "nurse_visit": "Nurse Visit",
                        "physiotherapy": "Physiotherapy",
                        "elderly_care": "Elderly Care",
                        "post_surgery_care": "Post Surgery Care"
                    })
                elif cat == "medicine delivery":
                    send_options(user_number, "Medicine Delivery", "How would you like to provide the medicine details?", {
                        "send_doctors_prescription": "Send Prescription",
                        "type_the_medicine": "Type Medicine"
                    })
                elif cat == "lab test":
                    send_options(user_number, "Lab Test", "Select a subcategory for Lab Test:", {
                        "blood_test": "Blood Test",
                        "urine_test": "Urine Test",
                        "covid_test": "COVID Test",
                        "full_body_checkup": "Full Body Checkup"
                    })
                else:
                    if prev_entities.get("awaiting_field") is None:
                        send_text(user_number, humanize_response("Please tell me which sub-service you want.", name=prev_entities.get("name")))
                prev_entities["awaiting_field"] = "sub_category"
                session_data[user_number] = prev_entities
                return {"status":"ok"}

            else:
                # nothing left in priority list; if everything filled, prompt confirmation
                if all([prev_entities.get("date"), prev_entities.get("time"), prev_entities.get("category"),
                        prev_entities.get("sub_category"), prev_entities.get("location")]) and prev_entities.get("time") not in [None, "", "00:00"]:
                    summary_text = compose_summary(prev_entities)
                    send_text(user_number, humanize_response("", kind="confirm_summary", summary=summary_text, name=prev_entities.get("name")))
                    send_buttons(user_number, "Confirm booking?", {"confirm_yes":"Yes","confirm_no":"No"})
                    prev_entities["state"] = "confirming"
                    prev_entities["awaiting_field"] = None
                    session_data[user_number] = prev_entities

            return {"status":"ok"}

        # ---------- TIME handler (asks next missing field after time — usually location) ----------
        if mapped_text in interactive_time_map:
            chosen_time = interactive_time_map[mapped_text]
            prev_entities["time"] = chosen_time
            prev_entities["last_interaction"] = "time_selected"
            # user answered time -> clear awaiting_field (they answered it)
            prev_entities["awaiting_field"] = None
            session_data[user_number] = prev_entities

            send_text(user_number, f"Got it — {mapped_text.title()} selected.")

            # compute next missing field; if date is still missing, ask for date next
            priority_after_time = ["date", "age", "location", "category", "sub_category"]
            def is_missing_field(field):
                v = prev_entities.get(field)
                return (v is None) or (str(v).strip() == "") or (field == "time" and v == "00:00")

            next_field = None
            for f in priority_after_time:
                if is_missing_field(f):
                    next_field = f
                    break

            if next_field == "date":
                if prev_entities.get("awaiting_field") is None:
                    send_buttons(user_number, "Please select appointment date:", {
                        "date_today": "Today",
                        "date_tomorrow": "Tomorrow",
                        "date_pick": "Pick another date"
                    })
                    prev_entities["awaiting_field"] = "date"
                    session_data[user_number] = prev_entities
            elif next_field == "age":
                if prev_entities.get("awaiting_field") is None:
                    send_text(user_number, "Please type the patient's age (in years).")
                    prev_entities["awaiting_field"] = "age"
                    session_data[user_number] = prev_entities
            elif next_field == "location":
                sess = session_data.get(user_number) or prev_entities
                if not sess.get("awaiting_address"):
                    sess["awaiting_address"] = True
                    sess["last_interaction"] = "asked_for_address_after_time"
                    # mark awaiting_field to tie typed location handling
                    sess["awaiting_field"] = "location"
                    session_data[user_number] = sess
                    send_text(user_number, humanize_response("Please share your location or type your address so we can assign the nearest staff.", name=prev_entities.get("name")))
                else:
                    session_data[user_number] = sess

            elif next_field == "category":
                if prev_entities.get("awaiting_field") is None:
                    send_buttons(user_number, "What would you like to book today?", {
                        "care_at_home": "Care at Home",
                        "medicine_delivery": "Medicine Delivery",
                        "lab_test": "Lab Test"
                    })
                    prev_entities["awaiting_field"] = "category"
                    session_data[user_number] = prev_entities

            elif next_field == "sub_category":
                cat = normalize_cat(prev_entities.get("category"))
                if cat == "care at home":
                    send_options(user_number, "Care at Home", "Select a subcategory for Care at Home:", {
                        "nurse_visit": "Nurse Visit",
                        "physiotherapy": "Physiotherapy",
                        "elderly_care": "Elderly Care",
                        "post_surgery_care": "Post Surgery Care"
                    })
                elif cat == "medicine delivery":
                    send_options(user_number, "Medicine Delivery", "How would you like to provide the medicine details?", {
                        "send_doctors_prescription": "Send Prescription",
                        "type_the_medicine": "Type Medicine"
                    })
                elif cat == "lab test":
                    send_options(user_number, "Lab Test", "Select a subcategory for Lab Test:", {
                        "blood_test": "Blood Test",
                        "urine_test": "Urine Test",
                        "covid_test": "COVID Test",
                        "full_body_checkup": "Full Body Checkup"
                    })
                prev_entities["awaiting_field"] = "sub_category"
                session_data[user_number] = prev_entities

            else:
                # nothing else needed — if everything filled, confirm
                if all([prev_entities.get("date"), prev_entities.get("time"), prev_entities.get("category"),
                        prev_entities.get("sub_category"), prev_entities.get("location"), prev_entities.get("age")]):
                    summary_text = compose_summary(prev_entities)
                    send_text(user_number, humanize_response("", kind="confirm_summary", summary=summary_text, name=prev_entities.get("name")))
                    send_buttons(user_number, "Confirm booking?", {"confirm_yes":"Yes","confirm_no":"No"})
                    prev_entities["state"] = "confirming"
                    prev_entities["awaiting_field"] = None
                    session_data[user_number] = prev_entities

            return {"status":"ok"}

        # --- Quick free-text fast path for common phrases (e.g., "tomorrow morning") ---
        free_text = (user_text or "")
        if isinstance(free_text, str):
            ft_lower = free_text.lower()
            date_choice = None
            time_choice = None
            if re.search(r"\btomorrow\b", ft_lower):
                date_choice = (datetime.now()+timedelta(days=1)).strftime("%Y-%m-%d")
            elif re.search(r"\btoday\b", ft_lower):
                date_choice = datetime.now().strftime("%Y-%m-%d")
            if re.search(r"\bmorning\b", ft_lower):
                time_choice = "09:00"
            elif re.search(r"\bafternoon\b", ft_lower):
                time_choice = "15:00"
            elif re.search(r"\bevening\b", ft_lower):
                time_choice = "18:00"

            if date_choice or time_choice:
                # update prev_entities with detected fields
                if date_choice:
                    prev_entities["date"] = date_choice
                    if prev_entities.get("awaiting_field") == "date":
                        prev_entities["awaiting_field"] = None
                if time_choice:
                    prev_entities["time"] = time_choice
                    if prev_entities.get("awaiting_field") == "time":
                        prev_entities["awaiting_field"] = None
                prev_entities["last_interaction"] = "free_text_datetime"
                session_data[user_number] = prev_entities

                # After setting date/time, ask the next missing field with same priority policy as DATE handler
                priority = ["time", "age", "location", "category", "sub_category"] if date_choice and not time_choice else ["age", "location", "category", "sub_category"]

                def is_missing_ft(field):
                    val = prev_entities.get(field)
                    return (val is None) or (str(val).strip() == "") or (field == "time" and val == "00:00")

                next_field = None
                for f in priority:
                    if is_missing_ft(f):
                        next_field = f
                        break

                if next_field == "time":
                    if prev_entities.get("awaiting_field") is None:
                        send_buttons(user_number, "Select preferred time:", {"time_morning":"Morning","time_afternoon":"Afternoon","time_evening":"Evening"})
                        prev_entities["awaiting_field"] = "time"
                        session_data[user_number] = prev_entities
                    return {"status":"ok"}
                elif next_field == "age":
                    if prev_entities.get("awaiting_field") is None:
                        send_text(user_number, "Please type the patient's age (in years).")
                        prev_entities["awaiting_field"] = "age"
                        session_data[user_number] = prev_entities
                    return {"status":"ok"}
                elif next_field == "location":
                    sess = session_data.get(user_number) or prev_entities
                    if not sess.get("awaiting_address") and prev_entities.get("awaiting_field") is None:
                        sess["awaiting_address"] = True
                        sess["last_interaction"] = "asked_for_address_after_free_text"
                        sess["awaiting_field"] = "location"
                        session_data[user_number] = sess
                        send_text(user_number, humanize_response("Please share your location or type your address so we can assign the nearest staff.", name=prev_entities.get("name")))
                    else:
                        session_data[user_number] = sess
                    return {"status":"ok"}
                elif next_field == "category":
                    if prev_entities.get("awaiting_field") is None:
                        send_buttons(user_number, "What would you like to book today?", {"care_at_home":"Care at Home","medicine_delivery":"Medicine Delivery","lab_test":"Lab Test"})
                        prev_entities["awaiting_field"] = "category"
                        session_data[user_number] = prev_entities
                    return {"status":"ok"}
                elif next_field == "sub_category":
                    cat = normalize_cat(prev_entities.get("category"))
                    if cat == "care at home":
                        send_options(user_number, "Care at Home", "Select a subcategory for Care at Home:", {"nurse_visit":"Nurse Visit","physiotherapy":"Physiotherapy","elderly_care":"Elderly Care","post_surgery_care":"Post Surgery Care"})
                    elif cat == "medicine delivery":
                        send_options(user_number, "Medicine Delivery", "How would you like to provide the medicine details?", {
                            "send_doctors_prescription": "Send Prescription",
                            "type_the_medicine": "Type Medicine"
                        })
                    elif cat == "lab test":
                        send_options(user_number, "Lab Test", "Select a subcategory for Lab Test:", {"blood_test":"Blood Test","urine_test":"Urine Test","covid_test":"COVID Test","full_body_checkup":"Full Body Checkup"})
                    else:
                        if prev_entities.get("awaiting_field") is None:
                            send_text(user_number, humanize_response("Please tell me which sub-service you want.", name=prev_entities.get("name")))
                    prev_entities["awaiting_field"] = "sub_category"
                    session_data[user_number] = prev_entities
                    return {"status":"ok"}

        # --- LLM processing for free text and fallbacks ---
        # Skip LLM processing if this was an interactive button that was already handled above
        # (date/time handlers return early, so if we reach here, it wasn't a handled interactive button)
        # Check if this was a date/time button that should have been handled
        mapped_text_for_check = BUTTON_MAPPINGS.get(user_text, user_text)
        mapped_text_for_check = str(mapped_text_for_check).strip().lower()
        for prefix in ("date_","time_","confirm_","btn_"):
            if mapped_text_for_check.startswith(prefix):
                mapped_text_for_check = mapped_text_for_check[len(prefix):]
                break
        
        # If this was a date/time selection that was already handled, skip LLM processing
        interactive_time_map_check = {"morning","afternoon","evening"}
        interactive_date_map_check = {"today","tomorrow"}
        if is_interactive and (mapped_text_for_check in interactive_time_map_check or mapped_text_for_check in interactive_date_map_check):
            # This was already handled by interactive handlers above - don't process with LLM
            print("⏭️ Skipping LLM processing - already handled by interactive handler")
            return {"status":"ok"}
        
        print("🤖 Calling LLM via process_user_message...")
        result = process_user_message(user_text, prev_entities)

        raw_entities = result.get("entities", {}) or {}
        entities = normalize_entity_keys(raw_entities)
        clean_entities = {k:v for k,v in entities.items() if v is not None and v != ""}
        existing = session_data.get(user_number, {}) or {}
        merged_entities = {**existing, **clean_entities}

        for key in ("category","sub_category","location","name"):
            if key in merged_entities and merged_entities.get(key) is not None:
                merged_entities[key] = sanitize_text_value_local(merged_entities[key])

        if (not merged_entities.get("name")) and user_name:
            merged_entities["name"] = user_name
        merged_entities.setdefault("greeted", False)
        merged_entities.setdefault("confirmed", False)
        merged_entities["last_interaction"] = "llm_processed"
        session_data[user_number] = merged_entities
        entities = merged_entities

        print("✅ LLM replied:", result.get("response"))
        print("🧩 Entities:", entities, "emotion:", result.get("emotion"), "sentiment:", result.get("sentiment"))

        intent = result.get("intent")
        category = entities.get("category")
        sub_category = entities.get("sub_category")
        reply_text = result.get("response", "Sorry, I didn’t get that.")

        # Prefer structured buttons over free-text if we're about to ask for date/time
        def missing_date_time(ent):
            need_date = not ent.get("date")
            need_time = (not ent.get("time")) or ent.get("time") == "00:00"
            return need_date or need_time

        reply_sent = False
        if intent == "general_query":
            send_text(user_number, reply_text)
            reply_sent = True

        # Shortcut: user wants "today" explicitly and we already have category/subcategory
        if re.search(r"\b(today|i want today|for today)\b", user_text, re.I):
            sess = session_data.get(user_number) or make_empty_session()
            if sess.get("category") or sess.get("sub_category"):
                today = datetime.now().strftime("%Y-%m-%d")
                sess["date"] = today

                # ✅ Stop re-asking for time again later
                sess["awaiting_field"] = None

                session_data[user_number] = sess

                if not sess.get("time"):
                    send_text(user_number, "Sure — which time today works for you?")
                    send_buttons(user_number, "Select preferred time:", {
                        "time_morning":"Morning",
                        "time_afternoon":"Afternoon",
                        "time_evening":"Evening"
                    })
                    return {"status":"ok"}

                summary_text = compose_summary(sess)
                send_text(user_number, humanize_response("", kind="confirm_summary", summary=summary_text, name=sess.get("name")))
                send_buttons(user_number, "Confirm booking?", {"confirm_yes":"Yes","confirm_no":"No"})
                sess["state"]="confirming"
                session_data[user_number]=sess
                return {"status":"ok"}


        # --- Handle extracted results / UI prompts exactly as before ---
        if intent == "greeting" and not session_data.get(user_number, {}).get("already_greeted"):
            sess = session_data.get(user_number,{}) or {}
            sess["already_greeted"] = True
            session_data[user_number] = sess
            first_name = sess.get("name") or user_name
            send_text(user_number, humanize_response("", kind="greeting", name=first_name))
            send_buttons(user_number, "What would you like to book today?", {"care_at_home":"Care at Home","medicine_delivery":"Medicine Delivery","lab_test":"Lab Test"})
            return {"status":"ok"}

        elif category and not sub_category:
            cat = normalize_cat(category)
            if cat=="care at home":
                send_options(user_number, "Care at Home", "Select a subcategory for Care at Home:", {"nurse_visit":"Nurse Visit","physiotherapy":"Physiotherapy","elderly_care":"Elderly Care","post_surgery_care":"Post Surgery Care"})
            elif cat=="medicine delivery":
                send_options(user_number, "Medicine Delivery", "How would you like to provide the medicine details?", {
                    "send_doctors_prescription": "Send Prescription",
                    "type_the_medicine": "Type Medicine"
                })
            elif cat=="lab test":
                send_options(user_number, "Lab Test", "Select a subcategory for Lab Test:", {"blood_test":"Blood Test","urine_test":"Urine Test","covid_test":"COVID Test","full_body_checkup":"Full Body Checkup"})
            else:
                send_text(user_number, reply_text)

        elif not category and not (intent == "greeting" or entities.get("greeted")):
            # general chit-chat or Q&A answered by LLM
            if not 'reply_sent' in locals() or not reply_sent:
                send_text(user_number, reply_text)

        else:
            # Ask exactly one missing thing, preserving your current priority (date -> time -> category -> sub_category -> location)
            missing = [k for k,v in entities.items() if (not v) and (k not in ["confirmed","greeted"])]
            # priority order: DATE -> TIME -> CATEGORY -> SUB_CATEGORY -> LOCATION
            if "date" in missing or not entities.get("date"):
                # avoid asking again if we've already asked another field
                if entities.get("awaiting_field") is None:
                    send_text(user_number, humanize_response("Please provide the date for the appointment.", kind=None, name=entities.get("name")))
                    send_buttons(user_number, "Please select appointment date:", {
                        "date_today": "Today",
                        "date_tomorrow": "Tomorrow",
                        "date_pick": "Pick another date"
                    })
                    entities["awaiting_field"] = "date"
                    session_data[user_number] = entities

            elif "time" in missing or not entities.get("time") or entities.get("time") == "00:00":
                # ask time only when we're not already waiting for something else
                if entities.get("awaiting_field") is None:
                    send_buttons(user_number, "Select preferred time:", {
                        "time_morning": "Morning",
                        "time_afternoon": "Afternoon",
                        "time_evening": "Evening"
                    })
                    entities["awaiting_field"] = "time"
                    session_data[user_number] = entities

            elif "age" in missing or not entities.get("age"):
                if entities.get("awaiting_field") is None:
                    send_text(user_number, "Please type the patient's age (in years).")
                    entities["awaiting_field"] = "age"
                    session_data[user_number] = entities

            elif "category" in missing:
                if entities.get("awaiting_field") is None:
                    # If we already sent an empathetic/general reply, avoid sending another text; just show buttons
                    if not ('reply_sent' in locals() and reply_sent):
                        send_text(user_number, humanize_response("Which service would you like to book?", kind=None, name=entities.get("name")))
                    send_buttons(user_number, "What would you like to book today?", {
                        "care_at_home": "Care at Home",
                        "medicine_delivery": "Medicine Delivery",
                        "lab_test": "Lab Test"
                    })
                    entities["awaiting_field"] = "category"
                    session_data[user_number] = entities

            elif "sub_category" in missing:
                cat = normalize_cat(entities.get("category"))
                if cat == "care at home":
                    send_options(user_number, "Care at Home", "Select a subcategory for Care at Home:", {
                        "nurse_visit": "Nurse Visit",
                        "physiotherapy": "Physiotherapy",
                        "elderly_care": "Elderly Care",
                        "post_surgery_care": "Post Surgery Care"
                    })
                    entities["awaiting_field"] = "sub_category"
                    session_data[user_number] = entities
                elif cat == "medicine delivery":
                    send_options(user_number, "Medicine Delivery", "How would you like to provide the medicine details?", {
                        "send_doctors_prescription": "Send Prescription",
                        "type_the_medicine": "Type Medicine"
                    })
                    entities["awaiting_field"] = "sub_category"
                    session_data[user_number] = entities
                elif cat == "lab test":
                    send_options(user_number, "Lab Test", "Select a subcategory for Lab Test:", {
                        "blood_test": "Blood Test",
                        "urine_test": "Urine Test",
                        "covid_test": "COVID Test",
                        "full_body_checkup": "Full Body Checkup"
                    })
                    entities["awaiting_field"] = "sub_category"
                    session_data[user_number] = entities
                else:
                    if entities.get("awaiting_field") is None:
                        send_text(user_number, humanize_response("Please tell me which sub-service you want.", kind=None, name=entities.get("name")))
                        entities["awaiting_field"] = "sub_category"
                        session_data[user_number] = entities

            elif not entities.get("location"):
                sess = session_data.get(user_number) or make_empty_session()
                # if we already asked for address, don't re-ask
                if not sess.get("awaiting_address") and entities.get("awaiting_field") is None:
                    sess["awaiting_address"] = True
                    sess["last_interaction"] = "asked_for_address"
                    # mark awaiting_field as location so typed location handling is consistent
                    sess["awaiting_field"] = "location"
                    session_data[user_number] = sess
                    send_text(user_number, humanize_response("Please share your location or type your address so we can assign the nearest staff.", kind=None, name=entities.get("name")))
                else:
                    # if awaiting_address or awaiting_field set, just persist
                    session_data[user_number] = sess

            elif all([entities.get("date"), entities.get("time"), entities.get("category"), entities.get("sub_category"), entities.get("location"), entities.get("age")]) and entities.get("time") not in [None, "", "00:00"]:
                summary_text = compose_summary(entities)
                send_text(user_number, humanize_response("", kind="confirm_summary", summary=summary_text, name=entities.get("name")))
                send_buttons(user_number, "Confirm booking?", {"confirm_yes":"Yes","confirm_no":"No"})
                sess = session_data.get(user_number, {})
                sess['state'] = 'confirming'
                sess.pop("awaiting_address", None)
                sess.update(entities)
                sess["last_interaction"] = "asked_to_confirm"
                # clear awaiting_field because we're moving to confirmation
                sess["awaiting_field"] = None
                session_data[user_number] = sess
                return {"status":"ok"}
            else:
                # fallback
                send_text(user_number, reply_text)

        print("---------------------------------------------------------")

    except Exception as e:
        print("❌ Error in webhook handler:", e)

    return {"status":"ok"}