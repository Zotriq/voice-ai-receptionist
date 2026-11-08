# Voice AI Receptionist

![tests](https://github.com/Zotriq/voice-ai-receptionist/actions/workflows/tests.yml/badge.svg)
![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)
![Vapi](https://img.shields.io/badge/Vapi-voice%20agent-5B47FB?style=flat-square)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white)
![ElevenLabs](https://img.shields.io/badge/ElevenLabs-voice-000000?style=flat-square)

An AI receptionist that **answers the phone for a dental clinic 24/7**. It checks the real schedule, books,
finds and cancels appointments, answers common questions, hands the call to a person when needed, and logs a
summary of every call.

Built on [Vapi](https://vapi.ai) for the phone side (speech-to-text, LLM, ElevenLabs voice) and a FastAPI
**tool server** that holds the clinic's actual business logic.

## What a call looks like

```
Caller: Hi, I chipped a tooth this morning. Can I come in today?
Mia:    I'm sorry to hear that. Let me check the schedule.            → check_availability(emergency, 2026-11-17)
Mia:    I have 2 PM or 2:30 PM today. Which works better?
Caller: 2 PM.
Mia:    Great. Can I have your full name and a good callback number?
Caller: Jane Doe, 555 123 4567.
Mia:    That's 555-123-4567, correct? ... One moment while I book that.  → book_appointment(...)
Mia:    You're booked for an emergency visit today at 2 PM. Your confirmation code is 4 F 9 A 1 C.
        ...
[call ends]  → end-of-call report: summary + transcript + recording saved, posted to Slack
```

## Architecture

```
 Phone call ─► Vapi (Deepgram STT → GPT-4o → ElevenLabs TTS)
                   │  tool-calls                 ▲ {"results":[{"toolCallId","result"}]}
                   ▼                             │
              FastAPI tool server  /vapi/webhook  (X-Vapi-Secret checked)
                   │
                   ├─ check_availability   opening hours, lunch break, service length, existing bookings
                   ├─ book_appointment     validated, double-booking safe (SQLite transaction)
                   ├─ find_appointment     by caller's phone number
                   ├─ cancel_appointment   by confirmation code
                   └─ get_clinic_info      hours, address, prices, insurance, payment, policies
                   │
                   └─ end-of-call-report → calls table (+ optional Slack alert)

 transfer_to_front_desk  → Vapi transferCall tool, for upset callers or billing disputes
```

### Built for real phone calls

- **Answers are written to be spoken.** Results read as natural speech ("Monday, November 16 at 9:30 AM") and offer at most a few options.
- **The schedule is real.** It respects opening hours, the lunch break, each service's length and existing bookings, and never offers a slot less than an hour away.
- **No double bookings.** The slot is checked again inside a database transaction at booking time.
- **Mistakes are recoverable.** A taken slot, an unknown service or a bad date comes back as a sentence the assistant can say, never a crash.
- **It follows Vapi's contract.** The server always returns HTTP 200 with one result or error per `toolCallId`, as flat strings.
- **Safety rules in the prompt.** No medical advice, and the caller is told to call 911 for emergencies. Upset callers are transferred to the front desk.
- **Every call is logged** with its summary, transcript, recording URL and ended reason, with an optional Slack notification.

## Setup

```bash
git clone https://github.com/Zotriq/voice-ai-receptionist.git
cd voice-ai-receptionist
pip install -e .
cp .env.example .env

uvicorn receptionist.server:create_app --factory --port 8000
# or: docker build -t receptionist . && docker run -p 8000:8000 -v $PWD/data:/data --env-file .env receptionist
```

Expose it publicly (deploy to Render, Railway or Fly.io, or use `ngrok http 8000` while testing), then create
the assistant in your Vapi account:

```bash
VAPI_API_KEY=... PUBLIC_SERVER_URL=https://your-url python scripts/create_assistant.py
```

This creates the five function tools plus a transfer tool, and the assistant "Mia" with the prompt in
[`vapi/system_prompt.md`](vapi/system_prompt.md). Attach a phone number to the assistant in the Vapi
dashboard and call it.

### Make it your clinic

Everything clinic-specific lives in [`receptionist/clinic.json`](receptionist/clinic.json): name, time zone,
opening hours, lunch break, services with durations and prices, and FAQ answers. Change that file and the
prompt. No code changes are needed for a different clinic, salon or law office.

## Front-desk endpoints

| Endpoint | Returns |
|---|---|
| `GET /appointments` | All bookings with status, phone, notes and the call they came from |
| `GET /calls` | Call log: caller, ended reason, summary, recording URL |
| `GET /health` | Health check |

## Tests

```bash
pip install -e ".[dev]"
pytest
```

13 tests send real Vapi-shaped webhook payloads to the server: secret checks, availability wording, the full
book → find → cancel flow, double-booking protection, lunch and closing-time rules, error handling that
still returns 200, multiple and string-encoded tool arguments, end-of-call logging with Slack, and the assistant
setup script (Vapi API mocked).

## Project structure

```
receptionist/
  server.py       FastAPI webhook for Vapi (tool calls, end-of-call reports) + dashboard endpoints
  tools.py        the five tools, returning speakable sentences
  scheduling.py   opening hours, free slots, bookings, cancellations, call log (SQLite)
  clinic.json     clinic configuration
vapi/
  system_prompt.md   the receptionist's instructions
  tools.json         function tool definitions
scripts/create_assistant.py   creates tools + assistant through the Vapi API
tests/
```

## License

MIT © Zohaib Tariq
