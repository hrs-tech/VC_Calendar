"""
export_ms_block_calendar.py

Exports a full-year, day-by-day list of every Middle School Block that
meets on every school day, with start/end times.

Unlike the Upper School version, Middle School blocks don't all follow a
consistent abbreviation prefix (e.g. "MS Explorations" is abbreviated
"EXP", not "MS-EXP"), so this filters by an explicit whitelist of block
descriptions instead of a prefix match.

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
    "academics.config.block_times:read"
)

# Middle School block names, from the Blocks list in Axiom (System homepage
# -> Blocks). Edit this list if MS blocks are added/renamed.
DEFAULT_MS_BLOCK_NAMES = [
    "Morning Break",
    "Lunch",
    "Afternoon Break 1",
    "Afternoon Break 2",
    "MS-1",
    "MS-2",
    "MS-3",
    "MS-4",
    "MS-5",
    "MS-6",
    "MS-7",
    "MS Explorations",
]


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
                          block_names=None, debug=True):
    if block_names is None:
        block_names = DEFAULT_MS_BLOCK_NAMES

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
        print("--- block_times columns ---")
        print(bt_df.columns.tolist())
        if len(bt_df) > 0:
            found_names = set(pd.json_normalize(bt_df["block"])["description"])
            missing = set(block_names) - found_names
            if missing:
                print(f"--- WARNING: these block names were not found in block_times: {missing} ---")

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

    # 5. Filter to the explicit Middle School block name whitelist
    if "block_description" in merged.columns and block_names:
        merged = merged[merged["block_description"].isin(block_names)]

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
    parser.add_argument("--out", default="ms_block_calendar.csv")
    parser.add_argument(
        "--block-names", default=None,
        help="Comma-separated list of block names to include, overriding the "
             "built-in Middle School list. E.g. 'MS-1,MS-2,Lunch'"
    )
    args = parser.parse_args()

    school_route = os.environ["VC_SCHOOL_ROUTE"]
    client_id = os.environ["VC_CLIENT_ID"]
    client_secret = os.environ["VC_CLIENT_SECRET"]

    block_names = args.block_names.split(",") if args.block_names else None

    token = get_token(school_route, client_id, client_secret)
    df = build_block_calendar(
        school_route, token, args.start, args.end,
        block_names=block_names,
    )
    df.to_csv(args.out, index=False)
    print(f"Wrote {len(df)} rows to {args.out}")


if __name__ == "__main__":
    main()
