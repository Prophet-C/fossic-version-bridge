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
        "origin": fl.origin_of(fid),
        "aid": aid,
        # No release entry with exactly this version (no release list, or
        # the header says "0.81 fix4" where the list says "0.81"): match the
        # version against the file names of the release and first-post
        # attachments instead (choose_post_archives); dropped before saving.
        "_postAids": [] if aid else fl.candidate_aids(info),
    }


ATTACH_CACHE = os.path.join(HERE, "attach_cache.json")


def choose_post_archives(catalog):
    """For records without a release-list attachment, pick the first-post
    archive matching their version (fl.pick_post_archive). Attachment names
    never change, so details are cached in attach_cache.json."""
    try:
        with open(ATTACH_CACHE, encoding="utf-8") as f:
            cache = {int(k): v for k, v in json.load(f).items()}
    except (OSError, ValueError):
        cache = {}
    wanted = {a for rec in catalog.values() for a in rec.get("_postAids", [])}
    missing = sorted(wanted - cache.keys())
    if missing:
        try:
            for aid, d in fl.attach_info(missing).items():
                cache[aid] = {k: d.get(k) for k in ("filename", "ext", "dateline")}
        except Exception as e:
            print(f"ERR  attachinfo_json for post attachments: {e}")
    for rec in catalog.values():
        aids = rec.pop("_postAids", [])
        if aids and not rec.get("aid"):
            aid = fl.pick_post_archive(rec["version"], {a: cache[a] for a in aids if a in cache})
            if aid:
                rec["aid"] = aid
            else:
                # No name carries the version: inspect_archives opens these
                # archives and looks for the mod's id inside instead.
                rec["_candidates"] = [(a, cache[a]["ext"]) for a in aids
                                      if a in cache and str(cache[a].get("ext", "")).lower() in fl.ARCHIVE_EXTS]
        if rec.get("aid") and rec["aid"] in cache:
            rec["_ext"] = cache[rec["aid"]].get("ext")
    with open(ATTACH_CACHE, "w", encoding="utf-8") as f:
        json.dump({str(k): v for k, v in sorted(cache.items())}, f, ensure_ascii=False, indent=0)
        f.write("\n")


ARCHIVE_CACHE = os.path.join(HERE, "archive_cache.json")
FULL_DOWNLOAD_BUDGET = 3 * 1024 ** 3  # bytes of 7z/rar per run; the rest waits for the next run


def inspect_archives(catalog):
    """Check one-click files against the mod_info.json inside them.

    - A zip download is verified (cheap: Range requests). If it holds a
      different mod, the link is dropped; otherwise the version TriOS sees
      becomes the archive's own, so an installed update matches it exactly.
    - A mod left without a download because no file name carried its
      version gets its candidate archives opened (7z/rar in full, within a
      per-run budget) to find the one containing its id.
    Results are cached per attachment id in archive_cache.json: an aid's
    file never changes."""
    try:
        with open(ARCHIVE_CACHE, encoding="utf-8") as f:
            cache = json.load(f)
    except (OSError, ValueError):
        cache = {}
    budget = [FULL_DOWNLOAD_BUDGET]

    def mods_in(aid, ext, url, zip_only=False):
        hit = cache.get(str(aid))
        if hit is None:
            if ext and ext.lower() != "zip" and (zip_only or budget[0] <= 0):
                return None  # verifying only, or over budget: try next run
            try:
                hit = {"mods": fl.inspect_archive(url, ext, zip_only=zip_only)}
            except fl.NotZip:
                return None  # verification skips 7z/rar (whole download)
            except Exception as e:
                hit = {"error": str(e)[:120]}
            if ext and ext.lower() != "zip":
                budget[0] -= fl.MAX_FULL_DOWNLOAD // 3  # rough average size
            cache[str(aid)] = hit
        return hit.get("mods")

    for key, rec in catalog.items():
        candidates = rec.pop("_candidates", [])
        ext = rec.pop("_ext", None)
        if rec.get("download") and str(ext or "zip").lower() == "zip":
            mods = mods_in(rec["aid"], ext, rec["download"], zip_only=True)
            if mods is None:
                continue
            match = next((m for m in mods if fl.catalog_key(m["id"]) == key), None)
            if mods and not match:
                print(f'WRONG {rec["id"]}: archive holds {[m["id"] for m in mods]}; link dropped')
                rec["download"] = None
            elif match:
                rec["version"] = fl.normalize(match["version"])
        elif not rec.get("download") and candidates:
            try:
                details = fl.attach_info([a for a, _ in candidates])
            except Exception as e:
                print(f'ERR  attachinfo for {rec["id"]}: {e}')
                continue
            best = None
            for aid, cext in candidates:
                d = details.get(aid)
                if not d or not d.get("downloadUrl"):
                    continue
                hit = cache.get(str(aid))
                url = None
                if hit is None:
                    try:
                        url = fl.resolve_download(fl.BASE + d["downloadUrl"])
                    except fl.LoginRequired:
                        cache[str(aid)] = {"error": "login required"}
                        continue
                    if not url:
                        continue
                mods = mods_in(aid, cext, url) if url or hit else None
                for m in mods or []:
                    if fl.catalog_key(m["id"]) == key and (
                            best is None or fl.compare_versions(fl.normalize(m["version"]), best[1]) > 0):
                        best = (aid, fl.normalize(m["version"]), url, d)
            if best:
                aid, version, url, d = best
                rec["aid"], rec["version"] = aid, version
                rec["download"] = url or fl.resolve_download(fl.BASE + d["downloadUrl"])
                print(f'FOUND {rec["id"]} {version} by opening the archive')
    with open(ARCHIVE_CACHE, "w", encoding="utf-8") as f:
        json.dump(dict(sorted(cache.items(), key=lambda kv: int(kv[0]))), f, ensure_ascii=False, indent=0)
        f.write("\n")


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
            except fl.LoginRequired:
                rec["download"] = None  # login-only; users resolve it locally (fossic_login.py)
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
    forum_of = {tid: fid for tid, fid in threads}
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
        fid = forum_of.get(entry["thread"])
        catalog[fl.catalog_key(entry["id"])] = make_record(entry["id"], entry["thread"], fid, info, picked, "mods.json")
    return catalog, cache.failed


def write_master(config, key, rec):
    """Write the master file(s) for one mod; returns True if any changed.
    See fl.master_file_name for why repost sources get two files."""
    repost = rec.get("origin") == "repost" and rec.get("source") != "mods.json"
    variants = [False, True] if repost and rec.get("download") else [False]
    changed = False
    for with_download in variants:
        data = {
            "masterVersionFile": fl.master_url(config["rawBase"], key, with_download),
            "modName": rec["id"],
            "modVersion": fl.version_object(rec["version"]),
        }
        if rec.get("download") and (with_download or not repost):
            # TriOS reads this from the remote file and offers a one-click update.
            data["directDownloadURL"] = rec["download"]
        data |= {
            # Extra fields are ignored by TriOS and Version Checker; kept for people.
            "fossicThread": rec["thread"],
            "fossicTitle": rec["title"],
            "fossicOrigin": rec.get("origin"),
        }
        changed |= _write_json_if_changed(os.path.join(VERSIONS_DIR, fl.master_file_name(key, with_download)), data)
    return changed


def _write_json_if_changed(path, data):
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
        choose_post_archives(catalog)
        resolve_downloads(catalog, {})
        inspect_archives(catalog)
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
    choose_post_archives(catalog)
    resolve_downloads(catalog, known)
    inspect_archives(catalog)

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
