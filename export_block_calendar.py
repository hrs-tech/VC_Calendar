"""
export_block_calendar.py

Exports a full-year, day-by-day list of every Block that meets on every
school day, by joining Veracross's Calendar Rotation Days to their
Block Schedules.

Requires: requests, pandas
    pip install requests pandas --break-system-packages

Set these env vars (same creds you already use for VC_Calendar / GitHub Actions):
    VC_SCHOOL_ROUTE   e.g. "headroyce"
    VC_CLIENT_ID
    VC_CLIENT_SECRET

Usage:
    python export_block_calendar.py --start 2026-08-01 --end 2027-06-30 --out blocks_2026-27.csv
"""

import os
import argparse
import requests
import pandas as pd

BASE_URL = "https://api.veracross.com/{school_route}/v3"

# Scopes needed for this script — confirmed against your IAM OAuth app's
# enabled scopes list.
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
    "academics.config.block_times:read"
)


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


def build_block_calendar(school_route, token, start_date, end_date,
                          school_level_prefix="US", debug=True):
    # 1. Pull every calendar rotation day in the date range
    rotation_days = api_get(
        school_route,
        token,
        "/academics/calendar_rotation_days",
        params={"date_on_or_after": start_date, "date_on_or_before": end_date},
    )
    rd_df = pd.DataFrame(rotation_days)

    # 2. Pull block TIMES — the actual start/end times per block, keyed to
    #    a specific rotation_day + block_schedule (confirmed via debug output:
    #    columns are id, block, block_schedule, rotation_day, rotation,
    #    start_time, end_time — with block/block_schedule/rotation_day/rotation
    #    all nested dicts).
    block_times = api_get(school_route, token, "/academics/config/block_times")
    bt_df = pd.DataFrame(block_times)

    if debug:
        print("--- calendar_rotation_days columns ---")
        print(rd_df.columns.tolist())
        print("--- block_times columns ---")
        print(bt_df.columns.tolist())

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

    # 4. Join: a calendar day's "day" (e.g. "US Day 1") + its block_schedule
    #    matches block_times' "rotation_day" + "block_schedule".
    merged = rd_df.merge(
        bt_df,
        left_on=["day_id", "block_schedule_id"],
        right_on=["rotation_day_id", "block_schedule_id"],
        suffixes=("", "_bt"),
        how="inner",
    )

    # 5. Filter to Upper School only, using the block abbreviation prefix
    #    (e.g. "US-CT" for Community Time). Adjust school_level_prefix if
    #    your school uses a different convention.
    if "block_abbreviation" in merged.columns and school_level_prefix:
        merged = merged[
            merged["block_abbreviation"].str.startswith(school_level_prefix, na=False)
        ]

    # 6. Trim to just what was asked for: date, block name, start, end
    out = merged.rename(columns={"block_description": "block_name"})
    keep = [c for c in ["date", "block_name", "start_time", "end_time"] if c in out.columns]
    out = out[keep].sort_values(
        [c for c in ["date", "start_time"] if c in keep]
    ).reset_index(drop=True)

    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--out", default="block_calendar.csv")
    parser.add_argument(
        "--school-level-prefix", default="US",
        help='Block abbreviation prefix to filter to (e.g. "US" for Upper School). '
             'Use "" to disable filtering and export all school levels.'
    )
    args = parser.parse_args()

    school_route = os.environ["VC_SCHOOL_ROUTE"]
    client_id = os.environ["VC_CLIENT_ID"]
    client_secret = os.environ["VC_CLIENT_SECRET"]

    token = get_token(school_route, client_id, client_secret)
    df = build_block_calendar(
        school_route, token, args.start, args.end,
        school_level_prefix=args.school_level_prefix,
    )
    df.to_csv(args.out, index=False)
    print(f"Wrote {len(df)} rows to {args.out}")


if __name__ == "__main__":
    main()
