"""
export_ms_block_calendar.py

Exports a full-year, day-by-day list of every Middle School Block that
meets on every school day, with start/end times.

Unlike the Upper School version, Middle School blocks don't all follow a
consistent abbreviation prefix, so this filters by an explicit whitelist
of block abbreviations instead of a prefix match.

SPECIAL CASE — MS Explorations: this block has NO entries in the
block_times configuration table at all (confirmed via diagnostic). Its
actual schedule instead comes from the individual "Explorations" course
sections' Class Meeting Times, a separate API resource keyed to specific
classes rather than the generic block-schedule template. So this script
fetches it via a second code path (get_explorations_rows) and merges the
result in alongside everything else.

Requires: requests, pandas
    pip install requests pandas --break-system-packages

Set these env vars (same creds as your other Veracross scripts):
    VC_SCHOOL_ROUTE   e.g. "headroyce"
    VC_CLIENT_ID
    VC_CLIENT_SECRET

Usage:
    python export_ms_block_calendar.py --start 2026-08-01 --end 2027-06-30 --out ms_blocks.csv
"""

import os
import argparse
import requests
import pandas as pd

BASE_URL = "https://api.veracross.com/{school_route}/v3"

SCOPES = (
    "academics.calendar_rotation_days:list "
    "academics.calendar_rotation_days:read "
    "academics.config.block_schedules:list "
    "academics.config.block_schedules:read "
    "academics.config.blocks:list "
    "academics.config.blocks:read "
    "academics.config.rotation_days:list "
    "academics.config.rotation_days:read "
    "academics.config.block_times:list "
    "academics.config.block_times:read "
    "academics.classes:list "
    "academics.classes:read "
    "academics.classes.meeting_times:list "
    "academics.classes.meeting_times:read"
)

# Middle School block ABBREVIATIONS, from the Blocks list in Axiom (System
# homepage -> Blocks). Matching on abbreviation rather than description,
# because Upper School has its own block also named "Lunch" — description
# text isn't unique across school levels, but abbreviations are
# (MS-LCH vs. whatever Upper School's Lunch abbreviation is).
# Edit this list if MS blocks are added/renamed.
#
# NOTE: "MS Explorations" (abbreviation MS-EXP) is deliberately NOT in
# this list — it has zero rows in block_times, so it's fetched separately
# via get_explorations_rows() and merged in afterward.
DEFAULT_MS_BLOCK_ABBREVIATIONS = [
    "MS-MBK",   # Morning Break
    "MS-LCH",   # Lunch
    "MS-ABK",   # Afternoon Break 1 & 2 (both share this abbreviation)
    "MS-1",
    "MS-2",
    "MS-3",
    "MS-4",
    "MS-5",
    "MS-6",
    "MS-7",
    "MS-8",
    "MS-MCI",   # MS Morning Check-In
    "MS-ASM",   # MS-Assembly
    "MS-OH",    # MS Office Hours — NOTE: no "Applies To"/Block Group tag in
                # Axiom like the others; confirm this is meant to be MS-only
                # before relying on it being complete.
]

# The course name to look up in /academics/classes to find all
# Explorations sections. Update if the course gets renamed.
EXPLORATIONS_COURSE_NAME = "Explorations"


def get_token(school_route, client_id, client_secret, scope=SCOPES):
    """Client-credentials OAuth flow — same pattern as your ICS script's get_access_token()."""
    resp = requests.post(
        f"https://accounts.veracross.com/{school_route}/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": scope,
        },
    )
    print("Status:", resp.status_code)
    print("Response:", resp.text)
    resp.raise_for_status()
    return resp.json()["access_token"]


def api_get(school_route, token, path, params=None):
    """GET with pagination handled via X-Page-Number / X-Page-Size headers."""
    url = f"{BASE_URL.format(school_route=school_route)}{path}"
    results = []
    page = 1
    while True:
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Page-Number": str(page),
            "X-Page-Size": "1000",
        }
        r = requests.get(url, headers=headers, params=params or {})
        if not r.ok:
            print(f"--- API error on {url} ---")
            print("Status:", r.status_code)
            print("Headers:", headers)
            print("Params:", params)
            print("Response:", r.text)
        r.raise_for_status()
        data = r.json()
        batch = data if isinstance(data, list) else data.get("data", data)
        if not batch:
            break
        results.extend(batch)
        if len(batch) < 1000:
            break
        page += 1
    return results


def get_explorations_rows(school_route, token, start_date, end_date,
                           course_name=EXPLORATIONS_COURSE_NAME, debug=True):
    """
    Fetch actual meeting occurrences for the Explorations block via
    class-level Class Meeting Times, since this block has no entries in
    the block_times configuration table.

    Returns a DataFrame with columns: date, block_name, start_time, end_time
    — same shape as the block_times-derived rows, de-duplicated across
    the multiple Explorations sections that share the same time slot.
    """
    all_classes = api_get(school_route, token, "/academics/classes")
    classes_df = pd.DataFrame(all_classes)

    if len(classes_df) == 0 or "course" not in classes_df.columns:
        if debug:
            print("--- WARNING: no classes returned, or 'course' column missing; "
                  "skipping Explorations ---")
        return pd.DataFrame(columns=["date", "block_name", "start_time", "end_time"])

    if debug:
        sample_course_val = classes_df["course"].dropna().iloc[0] if classes_df["course"].notna().any() else None
        print(f"--- raw 'course' field sample: type={type(sample_course_val)}, value={sample_course_val!r} ---")

    # Extract course description defensively — some rows may have a null
    # or differently-shaped "course" value, which breaks a blanket
    # json_normalize (that's what caused the earlier KeyError: 'description').
    course_desc = classes_df["course"].apply(
        lambda c: c.get("description") if isinstance(c, dict)
        else (c if isinstance(c, str) else None)
    )

    if debug:
        print("--- distinct course names found in classes ---")
        print(sorted(course_desc.dropna().unique().tolist()))

    matches = classes_df[course_desc == course_name]

    if len(matches) == 0 and "description" in classes_df.columns:
        # Fallback: maybe the course name shows up in the class's own
        # description field instead (e.g. "Explorations - GW").
        if debug:
            print("--- no matches via 'course' field; trying class 'description' field instead ---")
            candidates = classes_df[
                classes_df["description"].str.contains(course_name, case=False, na=False)
            ]
            print(f"--- classes whose description contains '{course_name}': {len(candidates)} ---")
            if len(candidates) > 0:
                print(candidates[["id", "class_id", "description"]])
        matches = classes_df[
            classes_df["description"].str.contains(course_name, case=False, na=False)
        ]

    if debug:
        print(f"--- found {len(matches)} classes for course '{course_name}' ---")

    if len(matches) == 0:
        return pd.DataFrame(columns=["date", "block_name", "start_time", "end_time"])

    all_rows = []
    for internal_id in matches["id"]:
        rows = api_get(school_route, token, f"/academics/classes/{internal_id}/meeting_times")
        all_rows.extend(rows)

    if not all_rows:
        if debug:
            print(f"--- WARNING: 0 meeting time rows found for '{course_name}' classes ---")
        return pd.DataFrame(columns=["date", "block_name", "start_time", "end_time"])

    mt_df = pd.DataFrame(all_rows)

    # block is a nested dict like {'id':16,'description':'MS Explorations',...}
    block_info = pd.json_normalize(mt_df["block"])
    mt_df["block_name"] = block_info["description"]

    # start_time/end_time come back as full ISO timestamps
    # (e.g. "1900-01-01T14:30:00Z") — extract just HH:MM.
    mt_df["start_time"] = mt_df["start_time"].str.slice(11, 16)
    mt_df["end_time"] = mt_df["end_time"].str.slice(11, 16)

    # Restrict to the requested date range (dates are "YYYY-MM-DD" strings,
    # which sort/compare correctly as strings).
    mt_df = mt_df[(mt_df["date"] >= start_date) & (mt_df["date"] <= end_date)]

    # De-duplicate: multiple sections meeting at the same time on the same
    # date should produce one row, not one per section.
    out = mt_df[["date", "block_name", "start_time", "end_time"]].drop_duplicates()

    if debug:
        print(f"--- {len(out)} unique Explorations occurrences after de-duplication "
              f"(from {len(mt_df)} raw section-level rows) ---")

    return out.reset_index(drop=True)


def build_block_calendar(school_route, token, start_date, end_date,
                          block_abbreviations=None, debug=True):
    if block_abbreviations is None:
        block_abbreviations = DEFAULT_MS_BLOCK_ABBREVIATIONS

    # 1. Pull every calendar rotation day in the date range
    rotation_days = api_get(
        school_route,
        token,
        "/academics/calendar_rotation_days",
        params={"date_on_or_after": start_date, "date_on_or_before": end_date},
    )
    rd_df = pd.DataFrame(rotation_days)

    # 2. Pull block TIMES — actual start/end times per block, keyed to a
    #    specific rotation_day + block_schedule.
    block_times = api_get(school_route, token, "/academics/config/block_times")
    bt_df = pd.DataFrame(block_times)

    if debug:
        print("--- calendar_rotation_days columns ---")
        print(rd_df.columns.tolist())
        print(f"--- calendar_rotation_days: {len(rd_df)} rows fetched ---")
        print("--- block_times columns ---")
        print(bt_df.columns.tolist())
        print(f"--- block_times: {len(bt_df)} rows fetched (raw, unfiltered) ---")

        if len(bt_df) > 0:
            block_info = pd.json_normalize(bt_df["block"])
            found_abbrevs = set(block_info["abbreviation"])

            # Forward check: anything on our whitelist that's missing entirely
            # from this year's schedule (e.g. it exists as a block definition
            # but isn't currently scheduled — not necessarily an error).
            missing = set(block_abbreviations) - found_abbrevs
            if missing:
                print(f"--- INFO: these whitelisted abbreviations have no scheduled occurrences: {missing} ---")

            # Reverse check: anything scheduled that LOOKS like Middle School
            # (abbreviation starts with "MS", or description contains "MS")
            # but isn't on our whitelist yet. This is how a future addition
            # like MS-8 gets caught automatically instead of silently
            # dropped.
            ms_like = block_info[
                block_info["abbreviation"].str.startswith("MS", na=False)
                | block_info["description"].str.contains("MS", na=False)
            ]
            unaccounted = set(ms_like["abbreviation"]) - set(block_abbreviations) - {"MS-EXP"}
            if unaccounted:
                print(f"--- WARNING: possible new Middle School blocks not in whitelist: {unaccounted} ---")
                print("    Add these to DEFAULT_MS_BLOCK_ABBREVIATIONS if they belong, "
                      "or ignore if they're actually Upper School (e.g. name coincidence).")

    # 3. Flatten nested dict columns on both sides
    for col in ["rotation", "day", "block_schedule"]:
        if col in rd_df.columns:
            expanded = pd.json_normalize(rd_df[col]).add_prefix(f"{col}_")
            rd_df = pd.concat(
                [rd_df.drop(columns=[col]).reset_index(drop=True), expanded], axis=1
            )

    for col in ["block", "block_schedule", "rotation_day", "rotation"]:
        if col in bt_df.columns:
            expanded = pd.json_normalize(bt_df[col]).add_prefix(f"{col}_")
            bt_df = pd.concat(
                [bt_df.drop(columns=[col]).reset_index(drop=True), expanded], axis=1
            )

    # 4. Join: a calendar day's "day" (e.g. "MS Day 1") + its block_schedule
    #    matches block_times' "rotation_day" + "block_schedule".
    merged = rd_df.merge(
        bt_df,
        left_on=["day_id", "block_schedule_id"],
        right_on=["rotation_day_id", "block_schedule_id"],
        suffixes=("", "_bt"),
        how="inner",
    )

    # 5. Filter to the explicit Middle School block abbreviation whitelist
    if "block_abbreviation" in merged.columns and block_abbreviations:
        merged = merged[merged["block_abbreviation"].isin(block_abbreviations)]

    # 6. Trim to just what was asked for
    out = merged.rename(columns={"block_description": "block_name"})
    keep = [c for c in ["date", "block_name", "start_time", "end_time"] if c in out.columns]
    out = out[keep]

    # 7. Merge in Explorations rows (sourced separately — see module docstring)
    exp_rows = get_explorations_rows(school_route, token, start_date, end_date, debug=debug)
    out = pd.concat([out, exp_rows], ignore_index=True)

    # 8. Sort chronologically, then rename to the CSV header format
    #    requested (matches Google Calendar's CSV import column names/order).
    out = out.sort_values(
        [c for c in ["date", "start_time"] if c in out.columns]
    ).reset_index(drop=True)

    out = out.rename(columns={
        "date": "Start Date",
        "block_name": "Subject",
        "start_time": "Start Time",
        "end_time": "End Time",
    })
    column_order = [c for c in ["Start Date", "Subject", "Start Time", "End Time"] if c in out.columns]
    out = out[column_order]

    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--out", default="ms_block_calendar.csv")
    parser.add_argument(
        "--block-abbreviations", default=None,
        help="Comma-separated list of block abbreviations to include, overriding "
             "the built-in Middle School list. E.g. 'MS-1,MS-2,MS-LCH'"
    )
    args = parser.parse_args()

    school_route = os.environ["VC_SCHOOL_ROUTE"]
    client_id = os.environ["VC_CLIENT_ID"]
    client_secret = os.environ["VC_CLIENT_SECRET"]

    block_abbreviations = (
        args.block_abbreviations.split(",") if args.block_abbreviations else None
    )

    token = get_token(school_route, client_id, client_secret)
    df = build_block_calendar(
        school_route, token, args.start, args.end,
        block_abbreviations=block_abbreviations,
    )
    df.to_csv(args.out, index=False)
    print(f"Wrote {len(df)} rows to {args.out}")


if __name__ == "__main__":
    main()
