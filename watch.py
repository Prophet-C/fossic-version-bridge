"""Background watcher: keeps fossic links in place without manual runs.

Mods updated through TriOS land in a fresh folder without our files, so this
re-injects whenever a mod folder appears or its mod_info.json changes, and
refreshes the catalog from GitHub every few hours (new mods on fossic, or a
mod that was not on fossic before).

    pythonw watch.py --mods "E:/Games/Starsector/mods"

Runs silently; see watch.log next to this file. Only one instance runs at a
time. Started at logon by the shortcut install_autostart.ps1 creates.
"""

import argparse
import datetime
import json
import os
import sys
import time

import fossic_lib as fl
import inject

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "watch.log")
CATALOG_CACHE = os.path.join(HERE, "catalog_cache.json")
LOCK = os.path.join(HERE, "watch.lock")


def log(msg):
    try:
        if os.path.exists(LOG) and os.path.getsize(LOG) > 1_000_000:
            os.replace(LOG, LOG + ".old")
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")
    except OSError:
        pass


def single_instance():
    """Hold an exclusive lock on watch.lock for the life of the process."""
    import msvcrt
    handle = open(LOCK, "a+")
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        return None
    return handle


def refresh_catalog(config):
    """Download the catalog; fall back to the last copy when offline."""
    try:
        catalog = inject.load_catalog(config, None)
        with open(CATALOG_CACHE, "w", encoding="utf-8") as f:
            json.dump(catalog, f, ensure_ascii=False)
        return catalog
    except Exception as e:
        log(f"catalog download failed ({e}); using cached copy")
        try:
            with open(CATALOG_CACHE, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None


def signatures(mods_dir):
    """Per folder: mod_info.json mtime and whether a version csv exists.
    Our own writes do not touch mod_info.json, so they don't retrigger."""
    sigs = {}
    try:
        names = os.listdir(mods_dir)
    except OSError:
        return sigs
    for d in names:
        info = os.path.join(mods_dir, d, "mod_info.json")
        try:
            sigs[d] = (os.stat(info).st_mtime, os.path.exists(os.path.join(mods_dir, d, inject.CSV_REL)))
        except OSError:
            pass
    return sigs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mods", required=True)
    ap.add_argument("--interval", type=float, default=30, help="seconds between folder checks")
    ap.add_argument("--refresh-hours", type=float, default=3, help="hours between catalog downloads")
    args = ap.parse_args()

    lock = single_instance()
    if lock is None:
        return  # already running
    with open(os.path.join(HERE, "mods.json"), encoding="utf-8-sig") as f:
        config = json.load(f)

    log(f"started, watching {args.mods}")
    catalog, last_refresh, known = None, 0.0, {}
    while True:
        try:
            if time.time() - last_refresh > args.refresh_hours * 3600 or catalog is None:
                fresh = refresh_catalog(config)
                last_refresh = time.time()
                if fresh is not None:
                    catalog = fresh
                    known = {}  # rescan everything against the new catalog
            if catalog is not None:
                sigs = signatures(args.mods)
                changed = {d for d, s in sigs.items() if known.get(d) != s}
                if changed:
                    rows, _ = inject.scan(config, catalog, args.mods, only=changed, log=log)
                    for state, name, local, remote, thread in rows:
                        if state.startswith("UPDATE"):
                            log(f"update available: {name} {local} -> {remote}  {thread}")
                    known = signatures(args.mods)  # after our writes
        except Exception as e:
            log(f"error: {e!r}")
        time.sleep(args.interval)


if __name__ == "__main__":
    if sys.stdout is None:  # pythonw: no console
        sys.stdout = sys.stderr = open(os.devnull, "w")
    main()
