#!/usr/bin/env python3
"""
Builds docs/data/fsa.json: for each Toronto postal prefix (M1K, M4C, ...) a
centre point and radius, estimated from the City's school and place-of-worship
locations (both carry a postal code and a point). It is an approximation, not
a boundary file, and the UI says so.

Static data, so it only refetches when the file is older than 30 days (or
with --force). A failed fetch keeps the existing file.

    python scripts/build_fsa.py [--force]
"""
import json
import math
import os
import statistics
import sys
from datetime import datetime, timezone

from fetch_cameras import CKAN, get_json, in_box, point_of  # same folder

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "docs", "data")
OUT = os.path.join(DATA_DIR, "fsa.json")
MAX_AGE_DAYS = 30
SLUGS = ("school-locations-all-types", "places-of-worship")
MIN_R, MAX_R, THIN_R = 700, 3500, 900  # metres


def datastore_rows(slug):
    pkg = get_json(f"{CKAN}/api/3/action/package_show", {"id": slug})["result"]
    res = next((r for r in pkg["resources"] if r.get("datastore_active")), None)
    if not res:
        raise RuntimeError(f"{slug}: no datastore resource")
    rows, offset = [], 0
    while True:
        page = get_json(f"{CKAN}/api/3/action/datastore_search",
                        {"resource_id": res["id"], "limit": 5000, "offset": offset}, timeout=180)["result"]["records"]
        rows += page
        if len(page) < 5000:
            return rows
        offset += 5000


def postal_prefix(row):
    for k, v in row.items():
        if "POSTAL" in k.upper() and isinstance(v, str):
            code = v.strip().upper().replace(" ", "")[:3]
            if len(code) == 3 and code[0] == "M" and code[1].isdigit() and code[2].isalpha():
                return code
    return None


def dist_m(lat1, lng1, lat2, lng2):
    dy = (lat2 - lat1) * 111320.0
    dx = (lng2 - lng1) * 111320.0 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dx, dy)


def summarize(points):
    lat = statistics.median(p[0] for p in points)  # median: one stray point can't drag the centre
    lng = statistics.median(p[1] for p in points)
    if len(points) < 3:
        r = THIN_R
    else:
        ds = sorted(dist_m(lat, lng, p[0], p[1]) for p in points)
        r = ds[min(len(ds) - 1, int(len(ds) * 0.85))] * 1.15
        r = max(MIN_R, min(MAX_R, r))
    return {"lat": round(lat, 5), "lng": round(lng, 5), "r": int(round(r)), "n": len(points)}


def main():
    force = "--force" in sys.argv
    if not force and os.path.exists(OUT):
        try:
            with open(OUT) as f:
                gen = datetime.fromisoformat(json.load(f)["generated"])
            age_d = (datetime.now(timezone.utc) - gen).total_seconds() / 86400
            if 0 <= age_d < MAX_AGE_DAYS:
                print(f"[fsa] fsa.json is {age_d:.0f}d old (< {MAX_AGE_DAYS}d) — skipping rebuild")
                return
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            pass
    groups, status = {}, {}
    for slug in SLUGS:
        try:
            rows = datastore_rows(slug)
            kept = 0
            for r in rows:
                code = postal_prefix(r)
                lat, lng = point_of(r)
                if code and in_box(lat, lng):
                    groups.setdefault(code, []).append((lat, lng))
                    kept += 1
            status[slug] = f"{kept}/{len(rows)} rows used"
        except Exception as e:  # noqa: BLE001
            status[slug] = f"error: {e}"
            print(f"[fsa] {slug} FAILED: {e}", file=sys.stderr)
    print(f"[fsa] {len(groups)} postal prefixes — {status}")
    if len(groups) < 20:  # Toronto has ~100; far fewer means a bad fetch
        print("[fsa] too few prefixes — keeping existing fsa.json", file=sys.stderr)
        return
    fsas = {code: summarize(pts) for code, pts in sorted(groups.items())}
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OUT, "w") as f:
        json.dump({"generated": datetime.now(timezone.utc).isoformat(), "status": status, "fsas": fsas},
                  f, separators=(",", ":"))
    print(f"[fsa] wrote {OUT}")


if __name__ == "__main__":
    main()
