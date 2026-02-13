# 📅 Agentic Calendar (Sochen Yoman)

**Python** · **aiogram** · **Telegram** · **Google Calendar API** · **Firestore** · **OpenAI** · **Whisper** · **aiohttp** · **Docker** · **Cloud Run**

**A Telegram bot that connects your Google Calendar with natural language.**  
Speak or type in Hebrew (or English), and the bot creates events, invites contacts, shows your schedule, and manages preferences—powered by OpenAI intent classification and a Hebrew-first, casual persona.

---

## ✨ Features

| Feature | Description |
|--------|-------------|
| **Create events** | Natural language: "תקבע פגישה עם דני מחר ב-10" → event created with title, time, and optional attendees. |
| **All-day & multi-day** | Birthdays, vacations, multi-day events with correct date handling. |
| **Recurring events** | Daily, weekly, monthly, yearly (RFC 5545 RRULE); optional "until when?" flow. |
| **Color by category** | Explicit color override or category-based colors (work, sport, personal, etc.). |
| **Invite attendees** | Resolve names from your contact list and send Google Calendar invites. |
| **Search & display** | "מה יש לי היום?", "מתי הפגישה הבאה?" — on-demand schedule view. |
| **Update & reschedule** | Move, rename, change color/location, add attendees; clear before/after diff. |
| **Delete with confirmation** | Remove events only after explicit user confirmation. |
| **Daily briefing** | Optional morning summary (e.g. 08:00) via Cloud Scheduler. |
| **Voice messages** | Send voice notes; transcribed with Whisper and processed like text. |
| **Admin test suite** | Password-protected suite (CRUD, onboarding sim, voice loop, search loop, dry-run) for developers. |

---

## 🛠 Tech Stack

- **Bot & HTTP:** [aiogram 3](https://docs.aiogram.dev/), [aiohttp](https://docs.aiohttp.org/)
- **Calendar & Auth:** Google Calendar API, OAuth2 (user tokens stored in Firestore)
- **Database:** Google Cloud Firestore (users, messages, tokens)
- **AI:** OpenAI (GPT for intent classification, Whisper for voice)
- **Config:** [python-dotenv](https://pypi.org/project/python-dotenv/), env-based config
- **Deployment:** Designed for **Google Cloud Run** (webhook mode); local dev uses polling

---

## 📋 Prerequisites

- **Python 3.10+**
- **Telegram Bot Token** ([BotFather](https://t.me/BotFather))
- **Google Cloud Project** with:
  - Calendar API enabled
  - OAuth2 credentials (Desktop app or Web) for user login
  - Firestore database
  - (Optional) Service account for Firestore if not using default credentials
- **OpenAI API key**

---

## 🚀 Quick Start

### 1. Clone and install

```bash
git clone <your-repo-url>
cd calendar_agent_v4
pip install -r requirements.txt
```

### 2. Environment variables

Create a `.env` file in the project root (see [Configuration](#-configuration) for full list):

```env
# Required
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
GOOGLE_CLIENT_ID=your_google_client_id
GOOGLE_CLIENT_SECRET=your_google_client_secret
OPENAI_API_KEY=your_openai_api_key

# Firestore (one of)
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
# or
GOOGLE_CREDENTIALS_JSON={"type":"service_account",...}

# Local OAuth callback (local dev)
GOOGLE_REDIRECT_URI=http://localhost:8080/oauth2callback
```

### 3. Run locally (polling)

```bash
python main.py
```

- If `BASE_WEBHOOK_URL` is **not** set, the app runs in **polling mode** (ideal for local development).
- OAuth callback for Google sign-in runs on the port defined in `OAUTH_SERVER_PORT` (default `8080`); the Telegram bot uses polling.

### 4. Run in production (webhooks)

Set:

```env
BASE_WEBHOOK_URL=https://your-service.run.app
PORT=8080
```

Then run the same command; the app will set the Telegram webhook and serve updates over HTTP. Expose the app on `PORT` and register the webhook path (e.g. `/webhook/<token>`).

---

## ⚙️ Configuration

| Variable | Description | Default |
|----------|-------------|--------|
| `TELEGRAM_BOT_TOKEN` | Bot token from BotFather | — |
| `GOOGLE_CLIENT_ID` | Google OAuth2 client ID | — |
| `GOOGLE_CLIENT_SECRET` | Google OAuth2 client secret | — |
| `GOOGLE_REDIRECT_URI` | OAuth2 redirect URI | `http://localhost:8080/oauth2callback` |
| `OPENAI_API_KEY` | OpenAI API key | — |
| `GOOGLE_APPLICATION_CREDENTIALS` | Path to service account JSON | — |
| `GOOGLE_CREDENTIALS_JSON` | Service account JSON string (alternative) | — |
| `WEBAPP_URL` / `BASE_WEBHOOK_URL` | Base URL for auth links / webhook | — |
| `BASE_WEBHOOK_URL` | Set to enable webhook mode | — |
| `PORT` | HTTP server port | `8080` |
| `ADMIN_PASSWORD` | Admin test suite password | `cks` |
| `ADMIN_TEST_ENABLED` | Enable admin test suite | `true` |

---

## 📁 Project Structure

```
calendar_agent_v4/
├── main.py                 # Entry point; webhook vs polling
├── config.py               # Env-based configuration
├── server.py               # OAuth2 callback server
├── requirements.txt
├── bot/
│   ├── __init__.py         # Router, UserMiddleware
│   ├── handlers/           # Commands, onboarding, events, chat, admin_tests
│   ├── states.py           # FSM state groups
│   ├── middleware.py      # User loading from Firestore
│   ├── jobs.py             # Daily briefing job
│   └── utils.py
├── prompts/
│   ├── base.py             # Personality & capabilities
│   ├── router.py           # Intent classification prompt + schema
│   └── skills/             # Per-intent prompts (create_event, chat, etc.)
├── services/
│   ├── llm_service.py      # OpenAI intent + confirmation
│   ├── openai_service.py   # API client, Whisper
│   ├── calendar_service.py # Google Calendar CRUD, RRULE
│   ├── firestore_service.py
│   └── auth_service.py
├── models/
│   └── user.py             # User data model
└── utils/
    └── performance.py      # Timing decorators
```

---

## 🧪 Admin Test Suite

For developers: a password-protected flow to run automated checks.

1. In Telegram, ask the bot to run tests (e.g. "בוא נריץ בדיקות").
2. Bot replies asking you to prove you’re admin by sending the **secret password**.
3. Send the password (e.g. value of `ADMIN_PASSWORD`).
4. You get a menu with 5 tests: CRUD obstacle course, onboarding sim, voice loop, search loop, dry-run event.
5. Type **צא** or **exit** anytime to leave the suite.

Entry can also be done in one message: `admin_test <password>`.

---

## 🤝 Contributing

1. Fork the repo, create a branch, make changes.
2. Run the bot locally and (if applicable) the admin test suite.
3. Open a pull request with a short description of the change.

---

*Built for Hebrew-first, casual calendar management over Telegram.*
