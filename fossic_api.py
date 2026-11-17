"""Official public index adapter. Never fetch forum HTML or private cookies."""
import datetime
import json
import urllib.parse

import fossic_lib as fl

API_URL = "https://api.fossic.org/mods?include_modding=true"


def build_catalog(config, previous, rows=None):
    rows = json.loads(fl.fetch(API_URL)) if rows is None else rows
    if not isinstance(rows, list) or not rows:
        raise ValueError("Official mod API returned an empty or invalid index; keeping old files")
    priority = {fid: i for i, fid in enumerate(fl.FORUMS)}
    catalog = {}
    by_thread = {}

    def record(row, rule=None):
        meta = row["thread_meta"]
        releases = [{"modVersion": r["mod_version"],
                     "gameVersion": r["game_version_id"],
                     "name": r.get("display_name") or "",
                     "aid": r["attachment_id"], "raw": r}
                    for r in row.get("mod_releases") or []]
        edited = datetime.datetime.fromtimestamp(
            row["mod_update_date"], datetime.timezone(datetime.timedelta(hours=8))
        ).strftime("%Y-%m-%d %H:%M")
        info = {"headerVersion": row["mod_version"], "releases": releases, "edited": edited}
        version, release = fl.pick_version(info, rule)
        if not version:
            return None
        mod_id = rule["id"] if rule else row["mod_id"]
        key = fl.catalog_key(mod_id)
        if not key or key in (".", "..") or any(c in key for c in '/\\:'):
            raise ValueError("Unsafe mod id in API index")
        aid = release["aid"] if release else None
        raw = release["raw"] if release else {}
        url = raw.get("download_url")
        # Only explicit public zip releases qualify for TriOS one-click updates.
        if (not row.get("mod_allow_direct_download") or not url
                or urllib.parse.urlparse(url).scheme != "https"
                or not (raw.get("file_name") or "").lower().endswith(".zip")):
            url = None
        old = previous.get(key, {})
        if aid and aid == old.get("aid") and fl.same_version(version, old.get("version", "")):
            version = old["version"]  # preserve archive-normalized version
        return {"id": mod_id, "thread": fl.thread_url(meta["tid"]),
                "forum": meta["fid"], "title": meta["title"], "edited": edited,
                "version": version, "source": "mods.json" if rule else "api",
                "origin": fl.origin_of(meta["fid"]), "aid": aid, "download": url}

    for row in rows:
        meta = row["thread_meta"]
        by_thread[meta["tid"]] = row
        if not row["mod_id"]:
            continue
        rec = record(row)
        if not rec:
            continue
        key = fl.catalog_key(rec["id"])
        old = catalog.get(key)
        if (old is None or priority.get(rec["forum"], 99) < priority.get(old["forum"], 99)
                or (rec["forum"] == old["forum"] and fl.compare_versions(rec["version"], old["version"]) > 0)):
            catalog[key] = rec
    for rule in config["mods"]:
        row = by_thread.get(rule.get("thread"))
        key = fl.catalog_key(rule["id"])
        rec = record(row, rule) if row else None
        if rec:
            catalog[key] = rec
        elif key in previous:
            catalog[key] = previous[key]
            print(f"WARN override {key}: no API match; retaining last known record")
    if not catalog or (previous and len(catalog) < len(previous) * 0.7):
        raise ValueError("Official API coverage dropped by over 30%; keeping old files")
    missing = previous.keys() - catalog.keys()
    print(f"Official API: {len(rows)} rows; {len(catalog)} matched; {len(missing)} old records retained")
    # Old unstructured posts are absent from the public API. Do not erase them.
    return {**previous, **catalog}
