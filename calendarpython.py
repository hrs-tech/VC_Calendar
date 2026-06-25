import requests
import json
from icalendar import Calendar, Event
from datetime import datetime, date
import pytz

# --- CONFIG ---
import os

CLIENT_ID     = os.environ["VC_CLIENT_ID"]
CLIENT_SECRET = os.environ["VC_CLIENT_SECRET"]
SCHOOL_ROUTE  = os.environ["VC_SCHOOL_ROUTE"]

# Event types to pull — use the exact string values from your Veracross value list
# e.g. "All School", "Upper School", "Athletics", "Arts", etc.
EVENT_TYPES = [8, 5, 90, 91, 106]  # integers, and updated to match what's actually in your data
DATE_FROM = "2026-08-01"
DATE_TO   = "2027-06-30"  # extended to cover the 2026-27 school year

OUTPUT_FILE = "headroyce_combined.ics"
# --------------

TOKEN_URL = f"https://accounts.veracross.com/{SCHOOL_ROUTE}/oauth/token"
API_BASE  = f"https://api.veracross.com/{SCHOOL_ROUTE}/v3"


def get_access_token():
    resp = requests.post(TOKEN_URL, data={
        "grant_type": "client_credentials",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "scope": "events.group_events:list"
    })
    print("Status:", resp.status_code)
    print("Response:", resp.text)  # add this line
    resp.raise_for_status()
    return resp.json()["access_token"]


def fetch_events(token, event_type_id):
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(f"{API_BASE}/events/group_events", headers=headers)
    resp.raise_for_status()
    all_events = resp.json().get("data", [])
    filtered = [e for e in all_events if e.get("event_type_id") == event_type_id]
    print(f"    → {len(filtered)} events of type {event_type_id}")
    return filtered


def build_ics(all_events):
    cal = Calendar()
    cal.add("prodid", "-//Head-Royce Combined Calendar//EN")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("x-wr-calname", "Head-Royce School")

    seen_ids = set()
    for ev in all_events:
        uid = str(ev.get("id"))
        if uid in seen_ids:
            continue  # deduplicate if event appears in multiple type pulls
        seen_ids.add(uid)

        vevent = Event()
        vevent.add("uid", f"{uid}@veracross.headroyce")
        vevent.add("summary", ev.get("description", "Untitled Event"))

        # Dates — Veracross returns "YYYY-MM-DD" strings
        start_str = ev.get("start_date")
        end_str   = ev.get("end_date") or start_str

        # If times are present use datetime, otherwise use all-day date
        start_time = ev.get("start_time")
        end_time   = ev.get("end_time")

        if start_time:
            tz = pytz.timezone("America/Los_Angeles")
            vevent.add("dtstart", tz.localize(
                datetime.strptime(f"{start_str} {start_time}", "%Y-%m-%d %H:%M:%S")))
            vevent.add("dtend", tz.localize(
                datetime.strptime(f"{end_str} {end_time}", "%Y-%m-%d %H:%M:%S")))
        else:
            vevent.add("dtstart", date.fromisoformat(start_str))
            vevent.add("dtend",   date.fromisoformat(end_str))

        if ev.get("description"):
            vevent.add("description", ev["description"])
        if ev.get("location"):
            vevent.add("location", ev["location"])
        if ev.get("event_type"):
            vevent.add("categories", [ev["event_type"]])

        cal.add_component(vevent)

    return cal


if __name__ == "__main__":
    print("Authenticating...")
    token = get_access_token()

    all_events = []
    for et in EVENT_TYPES:
        print(f"  Fetching events of type: {et}")
        events = fetch_events(token, et)
        print(f"    → {len(events)} events")
        all_events.extend(events)

    print(f"\nTotal events (pre-dedup): {len(all_events)}")
    cal = build_ics(all_events)

    with open(OUTPUT_FILE, "wb") as f:
        f.write(cal.to_ical())
    print(f"Written to {OUTPUT_FILE}")
