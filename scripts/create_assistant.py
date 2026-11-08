"""Create the tools and the assistant in your Vapi account.

    VAPI_API_KEY=... PUBLIC_SERVER_URL=https://... VAPI_SECRET=... FRONT_DESK_NUMBER=+1... \
        python scripts/create_assistant.py

Then attach a phone number to the assistant in the Vapi dashboard.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

API = "https://api.vapi.ai"
VAPI_DIR = Path(__file__).parents[1] / "vapi"


def build_payloads(server_url: str, secret: str, front_desk: str) -> tuple[list[dict], dict]:
    webhook = {"url": f"{server_url.rstrip('/')}/vapi/webhook", "secret": secret}
    tools = json.loads((VAPI_DIR / "tools.json").read_text())
    for tool in tools:
        tool["server"] = webhook
    tools.append({
        "type": "transferCall",
        "function": {"name": "transfer_to_front_desk"},
        "destinations": [{
            "type": "number",
            "number": front_desk,
            "message": "Sure, I'll connect you with the front desk now.",
            "description": "Caller wants a person, is upset, or has a billing dispute",
        }],
    })
    assistant = {
        "name": "Bright Smile Dental - Mia",
        "firstMessage": "Thanks for calling Bright Smile Dental, this is Mia. How can I help you today?",
        "model": {
            "provider": "openai",
            "model": "gpt-4o",
            "temperature": 0.3,
            "messages": [{"role": "system", "content": (VAPI_DIR / "system_prompt.md").read_text()}],
            "toolIds": [],
        },
        "voice": {"provider": "11labs", "voiceId": "sarah"},
        "transcriber": {"provider": "deepgram", "model": "nova-2", "language": "en"},
        "server": webhook,
        "serverMessages": ["end-of-call-report"],
        "endCallPhrases": ["goodbye", "have a great day"],
        "analysisPlan": {"summaryPrompt": "Summarize the call in 2 sentences: who called, what they wanted, and what was booked or still needs follow-up."},
    }
    return tools, assistant


def main(client: httpx.Client | None = None) -> str:
    load_dotenv()
    key = os.environ["VAPI_API_KEY"]
    tools, assistant = build_payloads(os.environ["PUBLIC_SERVER_URL"], os.environ["VAPI_SECRET"], os.environ["FRONT_DESK_NUMBER"])
    client = client or httpx.Client(base_url=API, headers={"Authorization": f"Bearer {key}"}, timeout=30)

    tool_ids = []
    for tool in tools:
        res = client.post("/tool", json=tool)
        res.raise_for_status()
        tool_ids.append(res.json()["id"])
    assistant["model"]["toolIds"] = tool_ids

    res = client.post("/assistant", json=assistant)
    res.raise_for_status()
    assistant_id = res.json()["id"]
    print(f"Created assistant {assistant_id} with {len(tool_ids)} tools. Attach a phone number in the Vapi dashboard.")
    return assistant_id


if __name__ == "__main__":
    main()
