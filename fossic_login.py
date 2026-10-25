"""Resolve login-only fossic downloads with the user's own browser login.

Some fossic attachments (all of Mod制作发布, some original releases) only
download for logged-in users, so the GitHub crawl cannot offer them. The
watcher resolves them locally, for installed mods only, and serves the
result to TriOS from 127.0.0.1 (see watch.py). Nothing resolved here is
published anywhere.

The login comes from fossic_login.txt, which the user fills in themselves
(template: fossic_login.example.txt) and which never leaves this machine.
"""

import json
import os
import re
import time

import fossic_lib as fl

HERE = os.path.dirname(os.path.abspath(__file__))
LOGIN_FILE = os.path.join(HERE, "fossic_login.txt")
CACHE_FILE = os.path.join(HERE, "login_cache.json")
RETRY_HOURS = 24  # re-check a mod with no usable download this often


def load_session():
    """Returns {"Cookie", "User-Agent"} or None when not configured."""
    try:
        with open(LOGIN_FILE, encoding="utf-8-sig") as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    values = {}
    for line in lines:
        if line.lstrip().startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        values[key.strip().lower()] = value.strip()
    cookie, agent = values.get("cookie", ""), values.get("user-agent", "")
    if not cookie or not agent or "粘贴" in cookie or "粘贴" in agent:
        return None
    return {"Cookie": cookie, "User-Agent": agent}


def load_cache():
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_cache(cache):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=1)


def _resolve(rec, rule, session):
    """Returns (download url or None, problem or None)."""
    tid = int(re.search(r"thread-(\d+)", rec["thread"]).group(1))
    content = fl.fetch(fl.thread_url(tid), session=session)
    if re.search(r"discuz_uid = '0'", content):
        raise fl.LoginRequired()
    info = fl.parse_thread(content)
    version, release = fl.pick_version(info, rule)
    if version != rec["version"]:
        # The thread moved on since the last crawl; GitHub will catch up and
        # the next check retries. Never offer an archive for another version.
        return None, f"帖子版本已变为 {version}，等待下次抓取"
    aid = release.get("aid") if release else None
    files = {}
    if not aid and fl.candidate_aids(info):
        # Same file-name matching as the crawl (scrape.choose_post_archives),
        # but logged in, so attachments hidden from guests are visible too.
        files = fl.attach_info(fl.candidate_aids(info), session=session)
        aid = fl.pick_post_archive(version, files)
        if not aid:
            # No name carries the version: open the archives and take the
            # newest one that holds this mod (as scrape.inspect_archives).
            found = _find_by_opening(rec, files, session)
            if found:
                return found, None
    if not aid:
        return None, "帖子里没有能确定对应这个版本的压缩包"
    d = files.get(aid) or fl.attach_info([aid], session=session).get(aid)
    if not d or str(d.get("ext", "")).lower() not in fl.ARCHIVE_EXTS or not d.get("downloadUrl"):
        return None, "附件不是压缩包或无法读取"
    url = fl.resolve_download(fl.BASE + d["downloadUrl"], session=session)
    if not url:
        return None, "附件需要积分或其他权限"
    if not fl.publicly_downloadable(url):
        return None, "直链也需要登录，TriOS 无法下载"
    # Confirm the archive is this mod, and report the version written
    # inside it, so an installed update matches exactly.
    try:
        mods = fl.inspect_archive(url, str(d.get("ext", "")) or None)
    except Exception:
        mods = None  # unreadable: keep the link, the version match above stands
    if mods is not None:
        match = next((m for m in mods if fl.catalog_key(m["id"]) == fl.catalog_key(rec["id"])), None)
        if not match:
            return None, f"附件里是别的 mod（{', '.join(m['id'] for m in mods)}）"
        return {"url": url, "version": fl.normalize(match["version"])}, None
    return {"url": url, "version": None}, None


def _find_by_opening(rec, files, session):
    """Open candidate archives (cached per aid, like collection threads) and
    return {"url", "version"} for the newest one containing rec's mod id."""
    cache = load_cache()
    archives = cache.setdefault("__archives__", {})
    key, best = fl.catalog_key(rec["id"]), None
    for aid, d in files.items():
        ext = str(d.get("ext", "")).lower()
        if ext not in fl.ARCHIVE_EXTS or not d.get("downloadUrl"):
            continue
        hit = archives.get(str(aid))
        if hit is None:
            hit = {"dateline": d.get("dateline") or 0, "filename": d.get("filename")}
            try:
                url = fl.resolve_download(fl.BASE + d["downloadUrl"], session=session)
                if not url:
                    raise ValueError("attachment needs credits or permission")
                hit["mods"] = _inspect(url, ext, session)
                hit["download"] = url if fl.publicly_downloadable(url) else None
            except fl.LoginRequired:
                raise
            except Exception as e:
                hit["problem"] = str(e)[:120]
            archives[str(aid)] = hit
        for m in hit.get("mods", []):
            v = fl.normalize(m["version"])
            if fl.catalog_key(m["id"]) == key and hit.get("download") and (
                    best is None or fl.compare_versions(v, best["version"]) > 0):
                best = {"url": hit["download"], "version": v}
    save_cache(cache)
    return best


def _inspect(url, ext, session):
    """Mods inside an archive; tries without the login first (what TriOS
    will do), then with it."""
    try:
        return fl.inspect_archive(url, ext)
    except Exception:
        return fl.inspect_archive(url, ext, session=session)


def collection_records(collections, session, log):
    """Collection threads (mods.json "collections") ship several small mods
    as first-post attachments, with no per-mod version anywhere but inside
    each archive. Open every archive once (cached per attachment id), read
    its mod_info.json files, and return catalog-style records {key: record};
    a mod uploaded more than once takes its newest upload. Their downloads
    are login-only, hence served locally like the rest here."""
    cache = load_cache()
    archives = cache.setdefault("__archives__", {})
    records = {}
    for col in collections:
        tid = col["thread"]
        content = fl.fetch(fl.thread_url(tid), session=session)
        if re.search(r"discuz_uid = '0'", content):
            raise fl.LoginRequired()
        info = fl.parse_thread(content)
        files = fl.attach_info(info["postAids"], session=session) if info["postAids"] else {}
        for aid, d in files.items():
            ext = str(d.get("ext", "")).lower()
            if ext not in fl.ARCHIVE_EXTS:
                continue
            hit = archives.get(str(aid))
            if hit is None:
                hit = {"dateline": d.get("dateline") or 0, "filename": d.get("filename")}
                try:
                    url = fl.resolve_download(fl.BASE + d["downloadUrl"], session=session)
                    if not url:
                        raise ValueError("attachment needs credits or permission")
                    hit["mods"] = _inspect(url, ext, session)
                    hit["download"] = url if fl.publicly_downloadable(url) else None
                except fl.LoginRequired:
                    raise
                except Exception as e:
                    hit["problem"] = str(e)[:120]
                archives[str(aid)] = hit
                log(f"collection {tid}: {d.get('filename')} -> "
                    f"{[m['id'] + ' ' + m['version'] for m in hit.get('mods', [])] or hit.get('problem')}")
            for m in hit.get("mods", []):
                key = fl.catalog_key(m["id"])
                old = records.get(key)
                if old and old["_dateline"] >= hit["dateline"]:
                    continue
                records[key] = {
                    "id": m["id"], "thread": fl.thread_url(tid), "forum": None, "title": info["title"],
                    "edited": info["edited"], "version": fl.normalize(m["version"]), "source": "collection",
                    "origin": "original", "aid": int(aid), "download": None,
                    "_local_download": hit.get("download"), "_dateline": hit["dateline"],
                }
    save_cache(cache)
    return records


def refresh(catalog, rules, keys, session, log):
    """Resolve downloads for the given catalog keys, using the cache.

    Returns (downloads {key: {"url", "version"}}, problems {key: text},
    login_ok). "version" is the one inside the archive (None if unread);
    the local server reports it instead of the thread's."""
    cache = load_cache()
    downloads, problems, login_ok = {}, {}, True
    for key in keys:
        rec = catalog.get(key)
        if not rec:
            continue
        if rec.get("source") == "collection":
            # Already opened by collection_records.
            if rec.get("_local_download"):
                downloads[key] = {"url": rec["_local_download"], "version": rec["version"]}
            continue
        hit = cache.get(key)
        fresh = hit and hit.get("version") == rec["version"] and hit.get("thread") == rec["thread"] and (
            hit.get("download") or time.time() - hit.get("checked", 0) < RETRY_HOURS * 3600)
        if not fresh and login_ok and session:
            try:
                found, problem = _resolve(rec, rules.get(key, {}), session)
                hit = {"version": rec["version"], "thread": rec["thread"],
                       "download": found and found["url"], "archiveVersion": found and found["version"],
                       "problem": problem, "checked": time.time()}
                cache[key] = hit
                log(f"login download for {rec['id']} {rec['version']}: {hit['download'] or problem}")
            except fl.LoginRequired:
                login_ok = False
                log("fossic login expired or invalid; update fossic_login.txt")
            except Exception as e:
                log(f"login download for {rec['id']} failed: {e!r}")
        if hit and hit.get("version") == rec["version"]:
            if hit.get("download"):
                downloads[key] = {"url": hit["download"], "version": hit.get("archiveVersion")}
            elif hit.get("problem"):
                problems[key] = hit["problem"]
    # _find_by_opening wrote "__archives__" meanwhile; keep its entries.
    current = load_cache()
    current.update({k: v for k, v in cache.items() if k != "__archives__"})
    save_cache(current)
    return downloads, problems, login_ok
