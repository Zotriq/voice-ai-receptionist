import datetime as dt

import pytest
from fastapi.testclient import TestClient

from receptionist.scheduling import AppointmentBook, Clinic
from receptionist.server import create_app

# Monday 2026-11-16, 08:00 clinic time.
NOW = dt.datetime(2026, 11, 16, 8, 0)
SECRET = "test-secret"


class FrozenClinic(Clinic):
    def now(self):
        return NOW


@pytest.fixture
def book(tmp_path):
    return AppointmentBook(tmp_path / "test.db", FrozenClinic.load())


@pytest.fixture
def slack():
    return []


@pytest.fixture
def client(book, slack):
    class FakeHttp:
        def post(self, url, json, timeout):
            slack.append(json["text"])

    app = create_app(book, secret=SECRET, slack_webhook="https://hooks.slack.test/x", http=FakeHttp())
    return TestClient(app)


def tool_call(name, call_id="call_1", **arguments):
    """Shape of the payload Vapi sends to the server URL for a function tool."""
    return {
        "message": {
            "type": "tool-calls",
            "call": {"id": call_id, "customer": {"number": "+15550001111"}},
            "toolCallList": [{"id": f"tc_{name}", "type": "function", "function": {"name": name, "arguments": arguments}}],
        }
    }


def send(client, payload, secret=SECRET):
    return client.post("/vapi/webhook", json=payload, headers={"X-Vapi-Secret": secret})


def result_of(response):
    [r] = response.json()["results"]
    return r
