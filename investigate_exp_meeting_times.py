"""
investigate_exp_meeting_times.py

One-off diagnostic: pulls the "Explorations" classes and their actual
meeting times from Veracross, since we've confirmed the EXP block has
zero rows in the block_times CONFIGURATION table — meaning its real
schedule lives in the class-level Class Meeting Times resource instead.

This is meant to be run once to inspect the raw data shape, not as a
long-term part of the export pipeline.

Requires: requests, pandas
    pip install requests pandas --break-system-packages

Set these env vars (same creds as your other Veracross scripts):
    VC_SCHOOL_ROUTE
    VC_CLIENT_ID
    VC_CLIENT_SECRET

Usage:
    python investigate_exp_meeting_times.py
"""

import os
import requests
import pandas as pd

BASE_URL = "https://api.veracross.com/{school_route}/v3"

# NOTE: these two scopes are new — add them to your OAuth app in
# Identity & Access Management before running this, or the token request
# (and/or the API calls) will fail with a scope error, same as we saw
# earlier with block_times.
SCOPES = (
    "academics.classes:list "
    "academics.classes:read "
    "academics.classes.meeting_times:list "
    "academics.classes.meeting_times:read"
)

# The specific Explorations sections, from your Class ID list in Axiom.
EXPLORATIONS_CLASS_IDS = [
    "901-EXP-GW",
    "901-EXP-KJ",
    "901-EXP-Kjac",
    "901-EXP-KWJB",
    "901-EXP-MW",
    "901-EXP-PR",
    "901-EXP-RG",
    "901-EXP-SK",
    "901-EXP-WA",
]


def get_token(school_route, client_id, client_secret, scope=SCOPES):
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


def main():
    school_route = os.environ["VC_SCHOOL_ROUTE"]
    client_id = os.environ["VC_CLIENT_ID"]
    client_secret = os.environ["VC_CLIENT_SECRET"]

    token = get_token(school_route, client_id, client_secret)

    # 1. Pull all classes, then filter locally to the Explorations sections.
    #    (Filtering locally rather than guessing at a query param name —
    #    if this list is huge, we can revisit with a server-side filter.)
    all_classes = api_get(school_route, token, "/academics/classes")
    classes_df = pd.DataFrame(all_classes)

    print("--- classes columns ---")
    print(classes_df.columns.tolist())

    if "class_id" in classes_df.columns:
        exp_classes = classes_df[classes_df["class_id"].isin(EXPLORATIONS_CLASS_IDS)]
    else:
        print("--- WARNING: no 'class_id' column found; printing first raw record instead ---")
        print(all_classes[0] if all_classes else "no classes returned")
        exp_classes = pd.DataFrame()

    print(f"--- matched {len(exp_classes)} of {len(EXPLORATIONS_CLASS_IDS)} Explorations classes ---")
    print(exp_classes)

    if len(exp_classes) == 0:
        return

    # 2. Meeting times are nested under a specific class ID
    #    (/academics/classes/{id}/meeting_times), not a flat listable
    #    collection — confirmed by the prior 400 error when we tried it
    #    as a flat path.
    id_col = "id" if "id" in exp_classes.columns else None
    if not id_col:
        print("--- Could not find internal 'id' column on classes — inspect manually ---")
        return

    all_rows = []
    for internal_id, class_id_label in zip(exp_classes["id"], exp_classes["class_id"]):
        rows = api_get(school_route, token, f"/academics/classes/{internal_id}/meeting_times")
        for row in rows:
            row["_class_id"] = class_id_label
            row["_internal_class_id"] = internal_id
        all_rows.extend(rows)

    mt_df = pd.DataFrame(all_rows)
    print("--- class meeting_times columns ---")
    print(mt_df.columns.tolist())
    print(f"--- {len(mt_df)} total meeting time rows across all 9 Explorations classes ---")
    if len(mt_df) > 0:
        print("--- sample record ---")
        print(mt_df.iloc[0].to_dict())
        print("--- full table ---")
        print(mt_df)


if __name__ == "__main__":
    main()
