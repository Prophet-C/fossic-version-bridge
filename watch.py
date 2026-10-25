"""Background watcher: keeps fossic links in place without manual runs.

Mods updated through TriOS land in a fresh folder without our files, so this
re-injects whenever a mod folder appears or its mod_info.json changes, and
refreshes the catalog from GitHub every few hours (new mods on fossic, or a
mod that was not on fossic before).

    pythonw watch.py --mods "E:/Games/Starsector/mods"                        # always on
    pythonw watch.py --mods "E:/Games/Starsector/mods" --launch "E:/TriOS/TriOS.exe"  # with TriOS

Runs silently; see watch.log next to this file. Only one instance runs at a
time. install_autostart.ps1 creates the shortcut for either mode.
"""

import argparse
import datetime
import http.server
import json
import os
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request

import fossic_lib as fl
import fossic_login
import inject
import report

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


def process_running(image_name):
    """True if any process with this executable name runs (TriOS may restart
    itself, e.g. after self-updating, so the launched handle is not enough)."""
    out = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {image_name}", "/NH", "/FO", "CSV"],
        capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW,
    ).stdout
    return f'"{image_name.lower()}"' in out.lower()


class LocalServer:
    """Serves master files to TriOS on 127.0.0.1 for mods whose download
    fossic only gives to logged-in users: the GitHub master file, plus the
    directDownloadURL resolved with the user's login (fossic_login.py)."""

    GITHUB_TTL = 600  # seconds to reuse a fetched GitHub master file

    def __init__(self, config):
        self.config = config
        self.downloads = {}  # catalog key -> {"url", "version" (inside the archive)}
        self.synthetic = {}  # catalog key -> record of a collection-thread mod (no GitHub file)
        self._github = {}    # file name -> (fetched at, data)
        self._lock = threading.Lock()
        self.httpd = None

    def start(self):
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path.split("?")[0] in ("/", "/updates"):
                    try:
                        with open(report.REPORT, "rb") as f:
                            page = f.read()
                    except OSError:
                        self.send_error(404, "updates.html not written yet")
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(page)))
                    self.end_headers()
                    self.wfile.write(page)
                    return
                name = urllib.parse.unquote(self.path.split("?")[0].rsplit("/", 1)[-1])
                if not self.path.startswith("/versions/") or not name.endswith(".version"):
                    self.send_error(404)
                    return
                try:
                    body = json.dumps(server.master(name), ensure_ascii=False).encode("utf-8")
                except Exception as e:
                    self.send_error(502, str(e)[:100])
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        try:
            self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", inject.LOCAL_PORT), Handler)
        except OSError as e:
            log(f"local server could not start on port {inject.LOCAL_PORT}: {e}")
            return False
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        log(f"local server on http://127.0.0.1:{inject.LOCAL_PORT}")
        return True

    def master(self, name):
        key = name[: -len(".version")]
        with self._lock:
            synthetic = self.synthetic.get(key)
            hit = self._github.get(name)
            found = self.downloads.get(key)
        if synthetic:
            data = {"modName": synthetic["id"], "modVersion": fl.version_object(synthetic["version"]),
                    "fossicThread": synthetic["thread"]}
        else:
            if not hit or time.time() - hit[0] > self.GITHUB_TTL:
                url = f'{self.config["rawBase"].rstrip("/")}/versions/{urllib.parse.quote(name)}'
                req = urllib.request.Request(url, headers={"User-Agent": fl.USER_AGENT})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    hit = (time.time(), json.loads(resp.read().decode("utf-8")))
                with self._lock:
                    self._github[name] = hit
            data = dict(hit[1])
        data["masterVersionFile"] = inject.local_master_url(key)
        if found:
            data["directDownloadURL"] = found["url"]
            if found.get("version"):
                # The version written inside the archive, so the installed
                # update matches exactly.
                data["modVersion"] = fl.version_object(found["version"])
        return data


class Watcher:
    LOGIN_CHECK = 3600  # seconds between login-download refreshes when nothing changed

    def __init__(self, config, mods_dir, refresh_hours, server=None, session=None):
        self.config, self.mods_dir, self.refresh_hours = config, mods_dir, refresh_hours
        self.server, self.session = server, session
        self.catalog, self.last_refresh, self.known = None, 0.0, {}
        self.last_login_check, self.login_state = 0.0, ("ok" if session else None)
        self.problems = {}
        self.collection = {}  # mods found in mods.json "collections" threads (login only)

    def merged_catalog(self):
        # A mod's own thread wins over its copy in a collection thread.
        return {**self.collection, **self.catalog}

    def refresh_collections(self):
        if not (self.server and self.config.get("collections")):
            return
        try:
            self.collection = fossic_login.collection_records(self.config["collections"], self.session, log)
            self.known = {}  # newly found mods may match installed folders
        except fl.LoginRequired:
            self.login_state = "expired"
            log("fossic login expired or invalid; update fossic_login.txt")
        except Exception as e:
            log(f"collection threads failed: {e!r}")
        with self.server._lock:
            self.server.synthetic = {k: r for k, r in self.collection.items() if k not in self.catalog}

    def tick(self):
        try:
            if self.catalog is None or time.time() - self.last_refresh > self.refresh_hours * 3600:
                fresh = refresh_catalog(self.config)
                self.last_refresh = time.time()
                if fresh is not None:
                    self.catalog = fresh
                    self.known = {}  # rescan everything against the new catalog
            if self.catalog is None:
                return
            if self.server and time.time() - self.last_login_check > self.LOGIN_CHECK:
                self.refresh_collections()
            catalog = self.merged_catalog()
            sigs = signatures(self.mods_dir)
            changed = {d for d, s in sigs.items() if self.known.get(d) != s}
            use_local = self.server is not None
            if changed:
                rows, _ = inject.scan(self.config, catalog, self.mods_dir, only=changed, log=log,
                                      use_local=use_local)
                for r in rows:
                    if r["state"].startswith("UPDATE"):
                        log(f'update available: {r["name"]} {r["installed"]} -> {r["fossic"]}  {r["thread"]}')
                self.known = signatures(self.mods_dir)  # after our writes
            if changed or time.time() - self.last_login_check > self.LOGIN_CHECK:
                self.last_login_check = time.time()
                # A full dry-run pass is cheap and gives the report every mod.
                rows, unknown = inject.scan(self.config, catalog, self.mods_dir, dry_run=True,
                                            log=lambda m: None, use_local=use_local)
                local_downloads = {}
                if self.server:
                    rules = {fl.catalog_key(m["id"]): m for m in self.config["mods"]}
                    keys = [r["key"] for r in rows if r["local"]]
                    local_downloads, self.problems, ok = fossic_login.refresh(
                        catalog, rules, keys, self.session, log)
                    if self.login_state != "expired" or ok:
                        self.login_state = "ok" if ok else "expired"
                    # Rows should show the version TriOS will actually see.
                    for r in rows:
                        v = (local_downloads.get(r["key"]) or {}).get("version")
                        if v:
                            r["fossic"] = v
                            cmp = fl.compare_versions(v, r["installed"])
                            if r["state"] in ("UPDATE", "ok", "check"):
                                r["state"] = "UPDATE" if cmp > 0 else "ok" if cmp == 0 else "check"
                    with self.server._lock:
                        self.server.downloads = local_downloads
                report.write(rows, unknown, local_downloads, self.problems, self.login_state)
        except Exception as e:
            log(f"error: {e!r}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mods", required=True)
    ap.add_argument("--interval", type=float, default=30, help="seconds between folder checks")
    ap.add_argument("--refresh-hours", type=float, default=3, help="hours between catalog downloads")
    ap.add_argument("--launch", help="start this program (TriOS.exe) after a first pass, "
                                     "and stop watching once it has exited")
    args = ap.parse_args()

    lock = single_instance()
    if lock is None:
        # Another watcher already keeps the mods linked; just start TriOS.
        if args.launch:
            subprocess.Popen([args.launch], cwd=os.path.dirname(args.launch))
        return
    with open(os.path.join(HERE, "mods.json"), encoding="utf-8-sig") as f:
        config = json.load(f)

    # With a fossic login configured, serve login-only downloads locally.
    session, server = fossic_login.load_session(), None
    if session:
        server = LocalServer(config)
        if not server.start():
            server = None
    watcher = Watcher(config, args.mods, args.refresh_hours, server, session)
    log(f"started, watching {args.mods}" + (f" while {args.launch} runs" if args.launch else "")
        + ("; fossic login configured" if session else ""))
    if not args.launch:
        while True:
            watcher.tick()
            time.sleep(args.interval)

    # Launcher mode: link everything first so TriOS's startup check sees it.
    watcher.tick()
    subprocess.Popen([args.launch], cwd=os.path.dirname(args.launch))
    image = os.path.basename(args.launch)
    seen, absent = False, 0
    started = time.time()
    while True:
        time.sleep(min(args.interval, 10))
        if process_running(image):
            seen, absent = True, 0
        else:
            absent += 1
        # Allow a slow first start; after that, two misses in a row (a
        # self-update restart takes a moment) mean TriOS has closed.
        if (seen and absent >= 2) or (not seen and time.time() - started > 120):
            break
        watcher.tick()
    watcher.tick()  # catch updates installed just before closing
    log("TriOS closed; watcher stopped")


if __name__ == "__main__":
    if sys.stdout is None:  # pythonw: no console
        sys.stdout = sys.stderr = open(os.devnull, "w")
    main()
