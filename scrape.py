"""Run by GitHub Actions: crawl fossic's mod release forums, read each
thread's structured release info, and write

  versions/<mod id>.version   Version Checker master file per mod
  catalog.json                mod id -> thread, version, last edit
  README.md                   human-readable table

Mods whose thread header has the right mod id are found automatically.
mods.json holds the exceptions (wrong id in the header, several mods in one
thread, threads without the structured header).

    python scrape.py                  # full crawl
    python scrape.py --threads 3342   # only these threads, print, write nothing
"""

import argparse
import datetime
import json
import os

import fossic_lib as fl

HERE = os.path.dirname(os.path.abspath(__file__))
VERSIONS_DIR = os.path.join(HERE, "versions")


def load_config():
    with open(os.path.join(HERE, "mods.json"), encoding="utf-8-sig") as f:
        return json.load(f)


class ThreadCache:
    def __init__(self):
        self.pages = {}
        self.failed = []

    def get(self, tid):
        if tid not in self.pages:
            try:
                self.pages[tid] = fl.parse_thread(fl.fetch(fl.thread_url(tid)))
            except Exception as e:
                print(f"ERR  thread {tid}: {e}")
                self.failed.append(tid)
                self.pages[tid] = None
        return self.pages[tid]


def make_record(mod_id, tid, fid, info, picked, source):
    version, release = picked
    aid = release.get("aid") if release else None
    return {
        "id": mod_id,
        "thread": fl.thread_url(tid),
        "forum": fid,
        "title": info["title"],
        "edited": info["edited"],
        "version": version,
        "source": source,
        "aid": aid,
    }


def resolve_downloads(catalog, known):
    """Fill rec["download"] with the CDN file behind each release attachment.
    known: {aid: url or None} from the previous run; an aid's file never
    changes (a new upload gets a new aid), so only new aids cost requests."""
    new = {rec["aid"] for rec in catalog.values() if rec.get("aid") and str(rec["aid"]) not in known}
    try:
        details = fl.attach_info(sorted(new)) if new else {}
    except Exception as e:
        print(f"ERR  attachinfo_json: {e}")
        details = None  # leave new aids unresolved; retried next run
    for rec in catalog.values():
        aid = rec.get("aid")
        if not aid:
            rec["download"] = None
            continue
        if str(aid) in known:
            rec["download"] = known[str(aid)]
            continue
        if details is None:
            continue
        d = details.get(aid)
        if not d or str(d.get("ext", "")).lower() not in fl.ARCHIVE_EXTS or not d.get("downloadUrl"):
            rec["download"] = None  # missing, hidden, or not an archive
        else:
            try:
                rec["download"] = fl.resolve_download(fl.BASE + d["downloadUrl"])
            except Exception as e:
                print(f'ERR  download for {rec["id"]} (aid {aid}): {e}')
                continue  # no "download" key: retried next run
        known[str(aid)] = rec["download"]


def build_catalog(config, threads):
    """threads: list of (tid, fid). Returns {catalog key: record}."""
    cache = ThreadCache()
    catalog = {}
    priority = {fid: i for i, fid in enumerate(fl.FORUMS)}

    for tid, fid in threads:
        info = cache.get(tid)
        if not info or not info["modId"]:
            continue
        picked = fl.pick_version(info)
        version = picked[0]
        if not version:
            continue
        rec = make_record(info["modId"], tid, fid, info, picked, "header")
        key = fl.catalog_key(info["modId"])
        old = catalog.get(key)
        if old is None or priority.get(fid, 99) < priority.get(old["forum"], 99) or (
            old["forum"] == fid and fl.compare_versions(version, old["version"]) > 0
        ):
            catalog[key] = rec

    # Overrides always win over what the header says.
    for entry in config["mods"]:
        if not entry.get("thread"):
            continue
        info = cache.get(entry["thread"])
        if not info:
            continue
        picked = fl.pick_version(info, entry)
        if not picked[0]:
            print(f'??   {entry["id"]}: no version in thread {entry["thread"]} with rule {entry}')
            continue
        catalog[fl.catalog_key(entry["id"])] = make_record(entry["id"], entry["thread"], None, info, picked, "mods.json")
    return catalog, cache.failed


def write_master(config, key, rec):
    path = os.path.join(VERSIONS_DIR, key + ".version")
    data = {
        "masterVersionFile": fl.master_url(config["rawBase"], key),
        "modName": rec["id"],
        "modVersion": fl.version_object(rec["version"]),
    }
    if rec.get("download"):
        # TriOS reads this from the remote file and offers a one-click update.
        data["directDownloadURL"] = rec["download"]
    data |= {
        # Extra fields are ignored by TriOS and Version Checker; kept for people.
        "fossicThread": rec["thread"],
        "fossicTitle": rec["title"],
    }
    try:
        with open(path, encoding="utf-8") as f:
            if json.load(f) == data:
                return False
    except (OSError, ValueError):
        pass
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return True


def write_readme(catalog):
    lines = [
        "# fossic-version-bridge",
        "",
        "Version Checker master files for Starsector mods released on [fossic.org](https://www.fossic.org/)",
        "(远行星号中文论坛), so [TriOS](https://github.com/wispborne/TriOS) can show when they have updates.",
        "",
        "Everything below is generated by `scrape.py` from the structured release info on each thread.",
        "Fix wrong matches in `mods.json`, not here. To use it, run `inject.py` locally (see `USAGE.md`).",
        "",
        f"Last run: {datetime.datetime.now(datetime.timezone.utc):%Y-%m-%d %H:%M} UTC, {len(catalog)} mods.",
        "",
        "| Mod id | Version | Thread last edited | Thread |",
        "|---|---|---|---|",
    ]
    for key in sorted(catalog):
        r = catalog[key]
        title = r["title"].replace("|", "\\|")
        lines.append(f'| `{r["id"]}` | {r["version"]} | {r["edited"] or ""} | [{title}]({r["thread"]}) |')
    lines.append("")
    with open(os.path.join(HERE, "README.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--threads", type=int, nargs="*", help="test: only these thread ids, write nothing")
    args = ap.parse_args()
    config = load_config()

    if args.threads:
        catalog, _ = build_catalog(config, [(t, None) for t in args.threads])
        resolve_downloads(catalog, {})
        for r in catalog.values():
            print(f'{r["id"]:35} {r["version"]:15} {r["source"]:10} {r["title"]}\n{"":35} download: {r.get("download")}')
        return

    threads = []
    for fid in fl.FORUMS:
        found = list(fl.list_forum_threads(fid))
        print(f"forum {fid}: {len(found)} threads")
        threads += [(tid, fid) for tid, _ in found]
    if not threads:
        raise SystemExit("no threads listed; fossic layout changed or site unreachable")

    catalog_path = os.path.join(HERE, "catalog.json")
    try:
        with open(catalog_path, encoding="utf-8") as f:
            previous = json.load(f)
    except (OSError, ValueError):
        previous = {}

    catalog, failed = build_catalog(config, threads)
    if len(failed) > len(threads) * 0.3:
        raise SystemExit(f"{len(failed)} of {len(threads)} threads failed; not writing a partial catalog")
    known = {str(r["aid"]): r["download"] for r in previous.values() if r.get("aid") and "download" in r}
    resolve_downloads(catalog, known)

    os.makedirs(VERSIONS_DIR, exist_ok=True)
    # Merge with the previous catalog so a mod whose thread failed to load
    # this run keeps its last known version instead of vanishing.
    failed_urls = {fl.thread_url(t) for t in failed}
    for key, rec in previous.items():
        if key not in catalog and rec["thread"] in failed_urls:
            catalog[key] = rec

    changed = [k for k, r in catalog.items() if write_master(config, k, r)]
    with open(catalog_path, "w", encoding="utf-8") as f:
        json.dump(dict(sorted(catalog.items())), f, ensure_ascii=False, indent=1)
        f.write("\n")
    write_readme(catalog)
    print(f"{len(catalog)} mods; changed: {len(changed)}; failed threads: {failed or 'none'}")


if __name__ == "__main__":
    main()
