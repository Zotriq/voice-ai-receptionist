"""FastAPI server that Vapi calls during and after phone calls.

POST /vapi/webhook
    tool-calls          -> run the requested tools, reply {"results": [{"toolCallId", "result"|"error"}]}
    end-of-call-report  -> store summary, transcript and recording; optional Slack alert
    anything else       -> acknowledged

GET  /appointments, /calls  -> simple JSON views for a front-desk dashboard

Run with:  uvicorn receptionist.server:create_app --factory
"""

from __future__ import annotations

import hmac
import json
import logging
import os
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request

from .scheduling import AppointmentBook, BookingError
from .tools import make_tools

log = logging.getLogger("receptionist")


def _check_secret(expected: str | None, x_vapi_secret: str | None, authorization: str | None) -> None:
    if not expected:
        return  # local development
    supplied = x_vapi_secret or (authorization or "").removeprefix("Bearer ").strip()
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="invalid secret")


def _arguments(call: dict) -> dict:
    args = call.get("function", {}).get("arguments") or {}
    return json.loads(args) if isinstance(args, str) else args


def create_app(book: AppointmentBook | None = None, *, secret: str | None = None,
               slack_webhook: str | None = None, http: httpx.Client | None = None) -> FastAPI:
    load_dotenv()
    book = book or AppointmentBook(os.getenv("DATABASE_PATH", "receptionist.db"))
    secret = secret if secret is not None else os.getenv("VAPI_SECRET")
    slack_webhook = slack_webhook if slack_webhook is not None else os.getenv("SLACK_WEBHOOK_URL")
    tools = make_tools(book)
    app = FastAPI(title=f"{book.clinic.config['name']} voice receptionist")

    def run_tool(call: dict, call_id: str | None) -> dict[str, str]:
        name = call.get("function", {}).get("name", "")
        result = {"toolCallId": call.get("id", "")}
        fn = tools.get(name)
        if fn is None:
            result["error"] = f"Unknown tool {name}"
            return result
        try:
            kwargs = _arguments(call)
            if name == "book_appointment":
                kwargs["_call_id"] = call_id
            result["result"] = fn(**kwargs)
        except BookingError as exc:
            # Expected problems (slot taken, bad date): give the assistant something to say.
            result["result"] = str(exc)
        except TypeError as exc:
            result["error"] = f"Missing or invalid arguments for {name}: {exc}"
        except Exception:  # never fail the whole webhook; Vapi needs a 200
            log.exception("tool %s failed", name)
            result["error"] = "Something went wrong on our side."
        return result

    def notify_slack(text: str) -> None:
        if not slack_webhook:
            return
        try:
            (http or httpx).post(slack_webhook, json={"text": text}, timeout=5)
        except httpx.HTTPError:
            log.warning("Slack notification failed")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/vapi/webhook")
    async def vapi_webhook(
        request: Request,
        x_vapi_secret: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _check_secret(secret, x_vapi_secret, authorization)
        message = (await request.json()).get("message", {})
        kind = message.get("type")
        call = message.get("call") or {}

        if kind == "tool-calls":
            calls = message.get("toolCallList") or [t.get("toolCall", {}) for t in message.get("toolWithToolCallList", [])]
            return {"results": [run_tool(c, call.get("id")) for c in calls]}

        if kind == "end-of-call-report":
            artifact = message.get("artifact") or {}
            recording = artifact.get("recording") or {}
            summary = (message.get("analysis") or {}).get("summary") or message.get("summary")
            caller = (call.get("customer") or {}).get("number")
            book.log_call(
                call_id=call.get("id", "unknown"),
                caller=caller,
                ended_reason=message.get("endedReason"),
                summary=summary,
                transcript=artifact.get("transcript"),
                recording_url=recording.get("url") or recording.get("stereoUrl") or artifact.get("recordingUrl"),
            )
            notify_slack(f":telephone_receiver: Call from {caller or 'unknown'} ended ({message.get('endedReason')}).\n{summary or ''}")
            return {"ok": True}

        return {"ok": True}

    @app.get("/appointments")
    def appointments() -> list[dict]:
        rows = book.conn.execute("SELECT * FROM appointments ORDER BY starts_at").fetchall()
        return [dict(r) for r in rows]

    @app.get("/calls")
    def calls() -> list[dict]:
        rows = book.conn.execute("SELECT call_id, caller, ended_reason, summary, recording_url, created_at FROM calls ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

    return app


