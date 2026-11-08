import json

from tests.conftest import result_of, send, tool_call


def test_rejects_wrong_secret(client):
    assert send(client, tool_call("get_clinic_info", topic="hours"), secret="nope").status_code == 401


def test_bearer_token_also_accepted(client):
    res = client.post("/vapi/webhook", json=tool_call("get_clinic_info", topic="hours"), headers={"Authorization": "Bearer test-secret"})
    assert res.status_code == 200


def test_availability_is_spoken_naturally(client):
    r = result_of(send(client, tool_call("check_availability", service="checkup", date="2026-11-16")))
    assert r["toolCallId"] == "tc_check_availability"
    # First slot is 09:00 (at least 1 hour from 08:00 now); lunch 13:00-14:00 is skipped.
    assert r["result"].startswith("On Monday, November 16 I have 9 AM, 9:30 AM, 10 AM or 10:30 AM, and")


def test_weekend_points_to_next_open_day(client):
    r = result_of(send(client, tool_call("check_availability", service="whitening", date="2026-11-21")))
    assert r["result"].startswith("We're fully booked on Saturday, November 21. The next openings are on Monday, November 23")


def test_full_booking_flow(client, book):
    booked = result_of(send(client, tool_call("book_appointment", patient_name="Jane Doe", phone="(555) 123-4567",
                                              service="filling", date="2026-11-17", time="10:00", notes="upper left molar")))
    assert booked["result"].startswith("Booked: Filling for Jane Doe on Tuesday, November 17 at 10 AM.")
    code = booked["result"].rsplit("code is ", 1)[1].rstrip(".").replace(" ", "")

    # The slot (and the overlapping half hour) is gone.
    avail = result_of(send(client, tool_call("check_availability", service="consultation", date="2026-11-17")))["result"]
    assert "10 AM" not in avail and "10:30 AM" not in avail and "11 AM" in avail

    found = result_of(send(client, tool_call("find_appointment", phone="555-123-4567")))["result"]
    assert "filling on Tuesday, November 17 at 10 AM" in found

    cancelled = result_of(send(client, tool_call("cancel_appointment", confirmation_code=" ".join(code))))["result"]
    assert cancelled == "Done. The appointment on Tuesday, November 17 at 10 AM is cancelled."
    assert book.conn.execute("SELECT call_id FROM appointments").fetchone()[0] == "call_1"


def test_double_booking_is_refused_with_a_spoken_reason(client):
    args = dict(patient_name="A", phone="5551112222", service="checkup", date="2026-11-18", time="09:00")
    send(client, tool_call("book_appointment", **args))
    r = result_of(send(client, tool_call("book_appointment", **{**args, "patient_name": "B", "time": "9:30 AM"})))
    assert r["result"] == "Wednesday November 18 at 9:30 AM is not available for a check-up and cleaning."


def test_lunch_and_closing_time_respected(book):
    import datetime as dt

    slots = book.free_slots(dt.date(2026, 11, 20), "whitening")  # Friday 09:00-15:00, 90 minutes
    assert dt.time(11, 30) in slots      # ends exactly at 13:00 when lunch starts
    assert dt.time(12, 0) not in slots   # would run into lunch
    assert slots[-1] == dt.time(11, 30)  # 14:00 + 90 min would end after the 15:00 close


def test_bad_input_gets_helpful_message(client):
    r = result_of(send(client, tool_call("check_availability", service="botox")))
    assert r["result"].startswith("Unknown service 'botox'")
    r = result_of(send(client, tool_call("book_appointment", patient_name="X", phone="1", service="checkup", date="tomorrow", time="9")))
    assert "year-month-day" in r["result"] or "phone" in r["result"]


def test_unknown_tool_and_missing_args_return_errors_with_200(client):
    res = send(client, tool_call("launch_rocket"))
    assert res.status_code == 200 and result_of(res)["error"] == "Unknown tool launch_rocket"
    res = send(client, tool_call("book_appointment", patient_name="X"))
    assert res.status_code == 200 and "Missing or invalid arguments" in result_of(res)["error"]


def test_string_arguments_and_multiple_calls(client):
    payload = tool_call("get_clinic_info", topic="insurance")
    payload["message"]["toolCallList"].append(
        {"id": "tc2", "type": "function", "function": {"name": "get_clinic_info", "arguments": json.dumps({"topic": "hours"})}}
    )
    results = send(client, payload).json()["results"]
    assert results[0]["result"].startswith("We accept Delta Dental")
    assert results[1]["toolCallId"] == "tc2" and "Thu 10:00 to 19:00" in results[1]["result"]


def test_end_of_call_report_is_logged_and_sent_to_slack(client, slack):
    report = {
        "message": {
            "type": "end-of-call-report",
            "endedReason": "customer-ended-call",
            "call": {"id": "call_9", "customer": {"number": "+15550009999"}},
            "analysis": {"summary": "Jane booked a filling for Tuesday at 10 AM."},
            "artifact": {"transcript": "AI: Thanks for calling...", "recording": {"url": "https://rec.test/9.wav"}},
        }
    }
    assert send(client, report).json() == {"ok": True}
    [call] = client.get("/calls").json()
    assert call["caller"] == "+15550009999" and call["recording_url"] == "https://rec.test/9.wav"
    assert "Jane booked a filling" in slack[0]


def test_other_events_are_acknowledged(client):
    assert send(client, {"message": {"type": "status-update", "status": "in-progress"}}).json() == {"ok": True}
