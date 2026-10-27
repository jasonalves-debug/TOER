#!/usr/bin/env python3
"""
One-shot diagnostic. Answers a single question:

    Does the TPS ArcGIS layer carry an *authoritative* coded-value domain
    for its event-type field?

Drop this in scripts/ next to update_data.py and run once:

    python scripts/probe_codes.py

Two possible outcomes:

  * It prints a DOMAIN table  -> that's TPS's own code->label mapping. It is
    the source of truth. You can auto-generate the whole glossary from it and
    retire the hand-maintained TYPE_GLOSSARY (including the unverified
    Gemini-sourced half). FTRPD / PERKN will appear in that table decoded.

  * It prints "NO coded-value domain" -> the event-type field is free
    text / raw CAD abbreviations with no official legend attached. Unknown
    codes then have to be decoded manually against TPS material, never
    guessed. The distinct-values list below tells you exactly which codes
    you actually need to decode (don't decode ones that never appear).

Reuses update_data.py's own resolver so it hits the identical layer your
real pipeline uses — no divergence.
"""

import json
import re
from collections import Counter

# Reuse the real pipeline's resolution + helpers so we probe the SAME layer.
from update_data import resolve_candidate_urls, http_get_json, find_field

ITEM_ID = "a22f5295933e48a5b0a4c90cd3c4cae1"  # matches fetch_police()

# Same detection order update_data.py uses, so type_field here == there.
TYPE_PATTERNS = ["CALL_TYPE", "TYP_ENG", "OFFENCE", "CATEGORY", "TYPE"]


def resolve_layer_url(service_url):
    if re.search(r"/(FeatureServer|MapServer)/\d+/?$", service_url):
        return service_url.rstrip("/")
    svc = http_get_json(service_url, {"f": "json"})
    layers = svc.get("layers") or []
    return f"{service_url.rstrip('/')}/{layers[0]['id']}" if layers else None


def main():
    candidates, title = resolve_candidate_urls(ITEM_ID)
    print(f"'{title}' -> {len(candidates)} candidate service URL(s)\n")

    for service_url in candidates:
        layer_url = resolve_layer_url(service_url)
        if not layer_url:
            continue
        try:
            layer = http_get_json(layer_url, {"f": "json"})
        except Exception as e:
            print(f"  skip {service_url}: {e}")
            continue

        fields = layer.get("fields", [])
        if not fields:
            continue

        type_field = find_field(fields, TYPE_PATTERNS)
        print("=" * 70)
        print(f"LAYER: {layer_url}")
        print(f"type field detected: {type_field}")
        print("=" * 70)

        # 1) Is there an authoritative coded-value domain on ANY field?
        found_any_domain = False
        for fld in fields:
            dom = fld.get("domain")
            if dom and dom.get("type") == "codedValue":
                found_any_domain = True
                cvs = dom.get("codedValues", [])
                star = "  <-- THIS IS YOUR TYPE FIELD" if fld["name"] == type_field else ""
                print(f"\nDOMAIN on field '{fld['name']}' ({len(cvs)} codes){star}")
                table = {cv["code"]: cv["name"] for cv in cvs}
                print(json.dumps(table, indent=2, ensure_ascii=False))

        if not found_any_domain:
            print("\nNO coded-value domain on any field.")
            print("-> No official legend. Decode the codes below manually; never guess.")

        # 2) Regardless, show the distinct raw values actually present in the
        #    live data. If a domain exists these are the codes it decodes; if
        #    not, this is your exact to-decode worklist.
        params = {
            "where": "1=1",
            "outFields": type_field or "*",
            "returnGeometry": "false",
            "resultRecordCount": "1000",
            "f": "json",
        }
        data = http_get_json(f"{layer_url}/query", params)
        vals = Counter(
            (feat.get("attributes", {}) or {}).get(type_field)
            for feat in data.get("features", [])
        )
        print(f"\nDistinct raw '{type_field}' values in live data "
              f"({len(vals)} unique, {sum(vals.values())} rows):")
        for v, n in vals.most_common():
            print(f"    {v!r}: {n}")
        return  # first valid Toronto layer is enough

    print("No valid layer found among candidates.")


if __name__ == "__main__":
    main()
