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


def get_token(school_route, client_id, client_secret):
    """Client-credentials OAuth flow, same as used by IAM-registered OAuth apps."""
    resp = requests.post(
        f"https://accounts.veracross.com/{school_route}/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        },
    )
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


def build_block_calendar(school_route, token, start_date, end_date):
    # 1. Pull every calendar rotation day in the date range
    rotation_days = api_get(
        school_route,
        token,
        "/academics/calendar_rotation_days",
        params={"date_on_or_after": start_date, "date_on_or_before": end_date},
    )
    rd_df = pd.DataFrame(rotation_days)

    # 2. Pull every block schedule definition (id -> blocks/times) once
    block_schedules = api_get(
        school_route, token, "/academics/configuration/block_schedules"
    )
    bs_df = pd.DataFrame(block_schedules)

    # block_schedules typically embed a "blocks" list per schedule; explode it
    # so each row becomes one block occurrence on that schedule.
    bs_df = bs_df.explode("blocks").reset_index(drop=True)
    block_cols = pd.json_normalize(bs_df["blocks"]).add_prefix("block_")
    bs_expanded = pd.concat(
        [bs_df.drop(columns=["blocks"]).reset_index(drop=True), block_cols], axis=1
    )

    # 3. Join calendar day -> its block schedule -> that schedule's blocks
    merged = rd_df.merge(
        bs_expanded,
        left_on="block_schedule_id",
        right_on="id",
        suffixes=("_day", "_schedule"),
        how="left",
    )

    # 4. Trim to the columns that actually matter for a printable calendar
    keep = [c for c in [
        "date", "rotation_id", "rotation_description",
        "block_schedule_id", "description",
        "block_description", "block_start_time", "block_end_time",
    ] if c in merged.columns]

    out = merged[keep].sort_values(
        [c for c in ["date", "block_start_time"] if c in keep]
    )
    return out


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
