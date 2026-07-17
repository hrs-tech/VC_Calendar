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

    # 4. Pull block definitions (names only — no times, per your last debug output)
    blocks = api_get(school_route, token, "/academics/config/blocks")
    blk_df = pd.DataFrame(blocks)

    # 5. Pull block TIMES — this is where start/end times almost certainly live
    block_times = api_get(school_route, token, "/academics/config/block_times")
    bt_df = pd.DataFrame(block_times)

    if debug:
        print("--- calendar_rotation_days columns ---")
        print(rd_df.columns.tolist())
        print("--- rotation_days columns ---")
        print(rot_df.columns.tolist())
        print("--- block_schedules columns ---")
        print(bs_df.columns.tolist())
        print("--- blocks columns ---")
        print(blk_df.columns.tolist())
        print("--- block_times columns ---")
        print(bt_df.columns.tolist())
        print("--- block_times sample row ---")
        if len(bt_df) > 0:
            print(bt_df.iloc[0].to_dict())

    # 5. Flatten the nested dict columns Veracross returns inline
    #    (rotation, day, block_schedule each come back as {'id':.., 'description':..})
    for col in ["rotation", "day", "block_schedule"]:
        if col in rd_df.columns:
            expanded = pd.json_normalize(rd_df[col]).add_prefix(f"{col}_")
            rd_df = pd.concat(
                [rd_df.drop(columns=[col]).reset_index(drop=True), expanded], axis=1
            )

    # NOTE: we don't yet know how block_times links back to a specific
    # calendar day (via block_schedule_id? day_id? both?), so for now we
    # return the flattened rotation-day table only. Once we see the
    # block_times columns/sample printed above, the final merge gets added
    # here.
    merged = rd_df

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
