#!/usr/bin/env python3
"""
Builds docs/data/cameras.json: City of Toronto traffic cameras (still images),
plus Ontario 511 highway cameras when ONTARIO511_KEY is set.

Cameras barely change, so this only refetches when the file is older than
24h (or with --force). If a fetch fails, the previous file is kept untouched
so the incident pipeline never loses its camera list.

    python scripts/fetch_cameras.py [--force]
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

CKAN = "https://ckan0.cf.opendata.inter.prod-toronto.ca"
UA = "toer-feed/1.0 (personal project; github.com/jasonalves-debug/TOER)"
TORONTO_BBOX = (43.55, 43.95, -79.75, -79.0)  # lat_min, lat_max, lng_min, lng_max
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "docs", "data")
OUT = os.path.join(DATA_DIR, "cameras.json")
MAX_AGE_H = 24


def get_json(url, params=None, timeout=60, tries=2):
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json, */*"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001 — retry then re-raise
            last = e
            time.sleep(2 if i == 0 else 5)
    raise last


def in_box(lat, lng):
    return (lat is not None and lng is not None
            and TORONTO_BBOX[0] <= lat <= TORONTO_BBOX[1] and TORONTO_BBOX[2] <= lng <= TORONTO_BBOX[3])


def point_of(row):
    g = row.get("geometry")
    if isinstance(g, str):
        try:
            g = json.loads(g)
        except ValueError:
            g = None
    if isinstance(g, dict):
        c = g.get("coordinates")
        if c and isinstance(c[0], list):  # MultiPoint -> first point
            c = c[0]
        if c and len(c) >= 2:
            return float(c[1]), float(c[0])
    try:
        return float(row.get("LATITUDE")), float(row.get("LONGITUDE"))
    except (TypeError, ValueError):
        return None, None


def fetch_city():
    pkg = get_json(f"{CKAN}/api/3/action/package_show", {"id": "traffic-cameras"})["result"]
    res = next((r for r in pkg["resources"] if r.get("datastore_active")), None)
    if not res:
        raise RuntimeError("no datastore resource found for traffic-cameras")
    rows, offset = [], 0
    while True:
        page = get_json(f"{CKAN}/api/3/action/datastore_search",
                        {"resource_id": res["id"], "limit": 5000, "offset": offset}, timeout=180)["result"]["records"]
        rows += page
        if len(page) < 5000:
            break
        offset += 5000
    out = []
    for r in rows:
        lat, lng = point_of(r)
        url = (r.get("IMAGEURL") or "").strip()
        if not url or not in_box(lat, lng):
            continue
        main, cross = (r.get("MAINROAD") or "").strip(), (r.get("CROSSROAD") or "").strip()
        name = f"{main} & {cross}".title() if main and cross else (main or cross or "Traffic camera").title()
        out.append({"src": "city", "name": name, "lat": round(lat, 5), "lng": round(lng, 5),
                    "views": [["Live", url.replace("http://", "https://")]]})
    return out


def fetch_511():
    key = os.environ.get("ONTARIO511_KEY", "").strip()
    if not key:
        return None
    data = get_json("https://511on.ca/api/v2/get/cameras", {"format": "json", "lang": "en", "key": key})
    out = []
    for c in data or []:
        try:
            lat, lng = float(c.get("Latitude")), float(c.get("Longitude"))
        except (TypeError, ValueError):
            continue
        views = [[(v.get("Description") or "View"), v.get("Url")]
                 for v in (c.get("Views") or []) if v.get("Status") == "Enabled" and v.get("Url")]
        if views and in_box(lat, lng):
            out.append({"src": "511", "name": c.get("Location") or c.get("Roadway") or "511 camera",
                        "lat": round(lat, 5), "lng": round(lng, 5), "views": views})
    return out


def main():
    force = "--force" in sys.argv
    if not force and os.path.exists(OUT):
        # Use the timestamp inside the file, not the file's mtime: a fresh git
        # checkout (every Actions run) resets mtime and would block refetching forever.
        try:
            with open(OUT) as f:
                gen = datetime.fromisoformat(json.load(f)["generated"])
            age_h = (datetime.now(timezone.utc) - gen).total_seconds() / 3600
            if 0 <= age_h < MAX_AGE_H:
                print(f"[cameras] cameras.json is {age_h:.1f}h old (< {MAX_AGE_H}h) — skipping refetch")
                return
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            pass  # unreadable or missing timestamp -> refetch
    cams, status = [], {}
    try:
        city = fetch_city()
        cams += city
        status["city"] = f"live ({len(city)})"
    except Exception as e:  # noqa: BLE001
        status["city"] = f"error: {e}"
        print(f"[cameras] city FAILED: {e}", file=sys.stderr)
    try:
        hw = fetch_511()
        if hw is None:
            status["511"] = "skipped (no ONTARIO511_KEY)"
        else:
            cams += hw
            status["511"] = f"live ({len(hw)})"
    except Exception as e:  # noqa: BLE001
        status["511"] = f"error: {e}"
        print(f"[cameras] 511 FAILED: {e}", file=sys.stderr)
    print(f"[cameras] {len(cams)} cameras — {status}")
    if not cams:
        print("[cameras] nothing fetched — keeping existing cameras.json", file=sys.stderr)
        return
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OUT, "w") as f:
        json.dump({"generated": datetime.now(timezone.utc).isoformat(), "status": status, "cameras": cams},
                  f, separators=(",", ":"))
    print(f"[cameras] wrote {OUT}")


if __name__ == "__main__":
    main()
