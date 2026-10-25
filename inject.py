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


def inject(config, mod_dir, mod_id, name, local_version, rec):
    data = {
        "masterVersionFile": fl.master_url(config["rawBase"], mod_id),
        "modName": name,
        "modVersion": fl.version_object(local_version),
        "fossicThread": rec["thread"],
    }
    with open(os.path.join(mod_dir, OUR_FILE), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.makedirs(os.path.dirname(os.path.join(mod_dir, CSV_REL)), exist_ok=True)
    with open(os.path.join(mod_dir, CSV_REL), "w", encoding="utf-8") as f:
        f.write(f"version file\n{OUR_FILE}\n")


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
    rows, unknown = [], []
    for d in sorted(os.listdir(args.mods)):
        mod_dir = os.path.join(args.mods, d)
        info_path = os.path.join(mod_dir, "mod_info.json")
        if not os.path.isfile(info_path):
            continue
        if os.path.exists(os.path.join(mod_dir, CSV_REL)) and not is_ours(mod_dir):
            continue  # the mod ships its own Version Checker file
        try:
            info = fl.load_json_lenient(info_path)
        except Exception as e:
            print(f"  ! skipped {d}: cannot parse mod_info.json ({e})")
            continue
        mod_id, name = info["id"], info.get("name", d)
        local = fl.version_string(info.get("version", "0"))
        rec = catalog.get(fl.catalog_key(mod_id))
        if not rec:
            unknown.append(name)
            continue
        if not args.dry_run:
            inject(config, mod_dir, mod_id, name, local, rec)
        cmp = fl.compare_versions(rec["version"], local)
        state = "UPDATE" if cmp > 0 else "ok" if cmp == 0 else "local newer"
        rows.append((state, name, local, rec["version"], rec["thread"]))

    order = {"UPDATE": 0, "local newer": 1, "ok": 2}
    print(f'\n{"state":12} {"mod":32} {"installed":14} {"fossic":14} thread')
    for state, name, local, remote, thread in sorted(rows, key=lambda r: (order[r[0]], r[1])):
        print(f"{state:12} {name[:32]:32} {local:14} {remote:14} {thread}")
    print(f"\n{len(rows)} mods {'would be ' if args.dry_run else ''}linked to fossic; "
          f"{sum(r[0] == 'UPDATE' for r in rows)} have updates.")
    if unknown:
        print("No Version Checker file and not found on fossic: " + ", ".join(unknown))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
