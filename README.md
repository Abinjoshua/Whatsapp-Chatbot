# Whatsapp Chatbot

This project implements a WhatsApp chatbot using FastAPI and OpenAI. It handles interactive WhatsApp Cloud API messages and uses an LLM for extraction and conversational fallback.

## Quick start (Windows / PowerShell)

1. Create and activate a virtual environment:

```powershell
python -m venv .venv
. .\.venv\Scripts\Activate.ps1
```

2. Install dependencies:

```powershell
pip install -r requirements.txt
```

3. Create a `.env` file in the project root with these variables:

```
OPENAI_API_KEY=sk-...
WHATSAPP_TOKEN=EAAJ...            # WhatsApp Cloud API token
PHONE_NUMBER_ID=1234567890        # WhatsApp phone number id
VERIFY_TOKEN=your_verify_token
```

Note: `.env` is listed in `.gitignore` to avoid committing secrets.

4. Run the FastAPI app (development):

```powershell
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

5. Expose to the internet for webhook testing (ngrok example):

```powershell
ngrok http 8000
```

6. Configure WhatsApp Cloud webhook to point to `https://<your-ngrok-host>/webhook` and use the same `VERIFY_TOKEN`.

## Files of interest
- `main.py` — FastAPI webhook + WhatsApp send helpers.
- `llm_utils.py` — LLM helpers and `process_user_message`.
- `session_*.json` — session template (ignored by git).

## Notes
- If you accidentally committed secrets, rotate them and remove them from git history.
- To push this repo to GitHub see the instructions in the repository root or use `gh repo create`.
