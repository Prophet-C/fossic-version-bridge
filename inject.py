"""Run locally: give every installed mod that fossic knows about, and that has
no Version Checker file of its own, a .version file pointing at this repo's
master file. TriOS then checks it like any other mod.

    python inject.py --mods "E:/Games/Starsector/mods"            # inject + report
    python inject.py --mods "E:/Games/Starsector/mods" --dry-run  # report only
    python inject.py --mods "E:/Games/Starsector/mods" --remove   # undo

Re-run it after updating mods: a fresh mod folder no longer has the file.
"""

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

import fossic_lib as fl

HERE = os.path.dirname(os.path.abspath(__file__))
OUR_FILE = "fossic_bridge.version"
CSV_REL = os.path.join("data", "config", "version", "version_files.csv")


def load_catalog(config, source):
    if source and os.path.exists(source):
        with open(source, encoding="utf-8") as f:
            return json.load(f)
    url = source or f'{config["rawBase"].rstrip("/")}/catalog.json'
    req = urllib.request.Request(url, headers={"User-Agent": fl.USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def is_ours(mod_dir):
    csv = os.path.join(mod_dir, CSV_REL)
    try:
        with open(csv, encoding="utf-8-sig") as f:
            return OUR_FILE in f.read()
    except OSError:
        return False


def remove(mod_dir):
    os.remove(os.path.join(mod_dir, CSV_REL))
    ours = os.path.join(mod_dir, OUR_FILE)
    if os.path.exists(ours):
        os.remove(ours)


def _write_if_changed(path, text):
    try:
        with open(path, encoding="utf-8") as f:
            if f.read() == text:
                return False
    except OSError:
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return True


# The watcher serves login-resolved master files here (watch.py).
LOCAL_PORT = 47391


def local_master_url(key):
    return f"http://127.0.0.1:{LOCAL_PORT}/versions/{urllib.parse.quote(fl.master_file_name(key))}"


def inject(config, mod_dir, mod_id, name, local_version, rec, with_download=False, local=False):
    """Returns True when a file was written. local: point TriOS at the
    watcher's 127.0.0.1 server instead of GitHub (login-only downloads)."""
    data = {
        "masterVersionFile": local_master_url(fl.catalog_key(mod_id)) if local
        else fl.master_url(config["rawBase"], mod_id, with_download),
        "modName": name,
        "modVersion": fl.version_object(local_version),
        "fossicThread": rec["thread"],
    }
    wrote = _write_if_changed(os.path.join(mod_dir, OUR_FILE), json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    wrote |= _write_if_changed(os.path.join(mod_dir, CSV_REL), f"version file\n{OUR_FILE}\n")
    return wrote


ORDER = {"UPDATE": 0, "UPDATE*": 1, "check": 2, "ok": 3, "ok*": 4, "excluded": 5}


def classify(rec, rule, name):
    """How to use a catalog record for an installed mod (which, having no
    Version Checker file of its own, has no checkable English source).

    Returns "original", "repost" (fallback: update notices only), "repost-dl"
    (fallback that may also one-click update), or None (excluded)."""
    if rule.get("exclude"):
        return None
    if rec.get("source") == "mods.json" or (rec.get("origin") or fl.origin_of(rec.get("forum"))) == "original":
        return "original"
    # Translation/repost thread used as a fallback. Its archive may be a
    # translated or altered build, so only offer it for one-click updates
    # when the installed copy is that Chinese build or the user said so.
    return "repost-dl" if rule.get("trustRepost") or fl.has_cjk(name) else "repost"


def scan(config, catalog, mods_dir, dry_run=False, only=None, log=print, use_local=False):
    """Inject every eligible mod (or only the folder names in `only`).

    use_local: a fossic login is configured, so mods whose download fossic
    only gives to logged-in users are pointed at the watcher's local server.
    Returns (rows, unknown); each row is a dict with key, state, name,
    installed, fossic, thread, local (served locally) and download (public
    one-click link from GitHub, if any)."""
    rules = {fl.catalog_key(m["id"]): m for m in config["mods"]}
    rows, unknown = [], []
    for d in sorted(os.listdir(mods_dir)):
        if only is not None and d not in only:
            continue
        mod_dir = os.path.join(mods_dir, d)
        info_path = os.path.join(mod_dir, "mod_info.json")
        if not os.path.isfile(info_path):
            continue
        if os.path.exists(os.path.join(mod_dir, CSV_REL)) and not is_ours(mod_dir):
            continue  # the mod ships its own Version Checker file
        try:
            info = fl.load_json_lenient(info_path)
        except Exception as e:
            log(f"  ! skipped {d}: cannot parse mod_info.json ({e})")
            continue
        mod_id, name = info["id"], info.get("name", d)
        key = fl.catalog_key(mod_id)
        local = fl.version_string(info.get("version", "0"))
        rec = catalog.get(key)
        rule = rules.get(key, {})
        if not rec:
            unknown.append(name)
            continue
        row = {"key": key, "name": name, "installed": local, "fossic": rec["version"],
               "thread": rec["thread"], "local": False, "download": None}
        kind = classify(rec, rule, name)
        if kind is None:
            if not dry_run and is_ours(mod_dir):
                remove(mod_dir)
                log(f"unlinked {name}: excluded in mods.json")
            rows.append(row | {"state": "excluded"})
            continue
        # Repost-sourced mods whose file has a download use the .dl variant
        # only when classify allows one-click updates for them.
        with_download = kind == "repost-dl" and bool(rec.get("download"))
        public = rec.get("download") if kind == "original" or with_download else None
        row["download"] = public
        # No public download, but the archive may be login-only: let the
        # watcher resolve it with the user's login and serve it locally.
        row["local"] = use_local and kind != "repost" and not public
        if not dry_run and inject(config, mod_dir, mod_id, name, local, rec, with_download, row["local"]):
            log(f"linked {name} ({local}) to {rec['thread']}")
        cmp = fl.compare_versions(rec["version"], local)
        if kind == "original":
            state = "UPDATE" if cmp > 0 else "ok" if cmp == 0 else "check"
        else:
            # Fallback source: a newer local copy simply wins.
            state = "UPDATE*" if cmp > 0 else "ok*"
        rows.append(row | {"state": state})
    return rows, unknown


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mods", required=True, help="Starsector mods folder")
    ap.add_argument("--catalog", help="catalog.json path or URL (default: the GitHub copy)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--remove", action="store_true", help="delete the files this script added")
    args = ap.parse_args()

    with open(os.path.join(HERE, "mods.json"), encoding="utf-8-sig") as f:
        config = json.load(f)

    if args.remove:
        n = 0
        for d in os.listdir(args.mods):
            mod_dir = os.path.join(args.mods, d)
            if is_ours(mod_dir):
                n += 1
                if not args.dry_run:
                    remove(mod_dir)
        print(f"removed from {n} mods" + (" (dry run)" if args.dry_run else ""))
        return

    catalog = load_catalog(config, args.catalog)
    import fossic_login
    rows, unknown = scan(config, catalog, args.mods, dry_run=args.dry_run,
                         use_local=fossic_login.load_session() is not None)

    print(f'\n{"state":10} {"mod":32} {"installed":14} {"fossic":14} thread')
    for r in sorted(rows, key=lambda r: (ORDER[r["state"]], r["name"])):
        print(f'{r["state"]:10} {r["name"][:32]:32} {r["installed"]:14} {r["fossic"]:14} {r["thread"]}')
    linked = [r for r in rows if r["state"] != "excluded"]
    print(f"\n{len(linked)} mods {'would be ' if args.dry_run else ''}linked to fossic; "
          f"{sum(r['state'].startswith('UPDATE') for r in rows)} have updates.")
    if any(r["local"] for r in rows):
        print("Mods needing a fossic login are served by the watcher: start TriOS from the TriOS (fossic) shortcut.")
    if not args.dry_run:
        import report
        cache = fossic_login.load_cache()
        local_dl = {k: v["download"] for k, v in cache.items() if v.get("download")}
        problems = {k: v["problem"] for k, v in cache.items() if v.get("problem")}
        report.write(rows, unknown, local_dl, problems)
        print(f"Update list: {report.REPORT}")
    print("  *        = fallback source: fossic only mirrors this mod (汉化/搬运 thread). A newer installed copy wins;")
    print("             one-click update is offered only if the installed copy is the Chinese build (or trustRepost).")
    print("  check    = installed is newer than its original fossic thread: stale thread or odd version scheme; verify")
    if unknown:
        print("No Version Checker file and not found on fossic: " + ", ".join(unknown))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
