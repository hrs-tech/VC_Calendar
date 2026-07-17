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
    "academics.config.rotation_days:read"
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
    """GET with pagination handled."""
    url = f"{BASE_URL.format(school_route=school_route)}{path}"
    headers = {"Authorization": f"Bearer {token}"}
    results = []
    page = 1
    while True:
        p = dict(params or {})
        p.update({"x_page_number": page, "x_page_size": 1000})
        r = requests.get(url, headers=headers, params=p)
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


def build_block_calendar(school_route, token, start_date, end_date, debug=True):
    # 1. Pull every calendar rotation day in the date range
    #    (date -> which rotation_id and/or block_schedule_id applies)
    rotation_days = api_get(
        school_route,
        token,
        "/academics/calendar_rotation_days",
        params={"date_on_or_after": start_date, "date_on_or_before": end_date},
    )
    rd_df = pd.DataFrame(rotation_days)

    # 2. Pull rotation day definitions (id -> "A", "B", "C" label)
    rotation_defs = api_get(school_route, token, "/academics/config/rotation_days")
    rot_df = pd.DataFrame(rotation_defs)

    # 3. Pull block schedule definitions
    block_schedules = api_get(school_route, token, "/academics/config/block_schedules")
    bs_df = pd.DataFrame(block_schedules)

    # 4. Pull block definitions (times, descriptions)
    blocks = api_get(school_route, token, "/academics/config/blocks")
    blk_df = pd.DataFrame(blocks)

    if debug:
        # These print statements are the fastest way to confirm real field
        # names/shapes returned by your school's instance. Once you've
        # confirmed the joins below work, you can delete this block.
        print("--- calendar_rotation_days columns ---")
        print(rd_df.columns.tolist())
        print("--- rotation_days columns ---")
        print(rot_df.columns.tolist())
        print("--- block_schedules columns ---")
        print(bs_df.columns.tolist())
        print("--- blocks columns ---")
        print(blk_df.columns.tolist())

    # 5. Join calendar day -> rotation label
    merged = rd_df.copy()
    if "rotation_id" in merged.columns and "id" in rot_df.columns:
        merged = merged.merge(
            rot_df, left_on="rotation_id", right_on="id", suffixes=("", "_rotation")
        )

    # 6. Join calendar day -> block schedule -> blocks meeting on it.
    #    NOTE: whether "blocks" links to "block_schedules" via a shared
    #    block_schedule_id, or blocks are nested inside the block_schedule
    #    response, depends on your school's config — check the printed
    #    columns above and adjust this join accordingly.
    if "block_schedule_id" in merged.columns and "block_schedule_id" in blk_df.columns:
        merged = merged.merge(
            blk_df, on="block_schedule_id", suffixes=("", "_block"), how="left"
        )
    elif "block_schedule_id" in merged.columns and "id" in bs_df.columns:
        merged = merged.merge(
            bs_df, left_on="block_schedule_id", right_on="id",
            suffixes=("", "_schedule"), how="left",
        )

    return merged


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--out", default="block_calendar.csv")
    args = parser.parse_args()

    school_route = os.environ["VC_SCHOOL_ROUTE"]
    client_id = os.environ["VC_CLIENT_ID"]
    client_secret = os.environ["VC_CLIENT_SECRET"]

    token = get_token(school_route, client_id, client_secret)
    df = build_block_calendar(school_route, token, args.start, args.end)
    df.to_csv(args.out, index=False)
    print(f"Wrote {len(df)} rows to {args.out}")


if __name__ == "__main__":
    main()
