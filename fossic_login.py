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
        return None, "帖子里没有能确定对应这个版本的压缩包"
    d = files.get(aid) or fl.attach_info([aid], session=session).get(aid)
    if not d or str(d.get("ext", "")).lower() not in fl.ARCHIVE_EXTS or not d.get("downloadUrl"):
        return None, "附件不是压缩包或无法读取"
    url = fl.resolve_download(fl.BASE + d["downloadUrl"], session=session)
    if not url:
        return None, "附件需要积分或其他权限"
    if not fl.publicly_downloadable(url):
        return None, "直链也需要登录，TriOS 无法下载"
    return url, None


def refresh(catalog, rules, keys, session, log):
    """Resolve downloads for the given catalog keys, using the cache.

    Returns (downloads {key: url}, problems {key: text}, login_ok)."""
    cache = load_cache()
    downloads, problems, login_ok = {}, {}, True
    for key in keys:
        rec = catalog.get(key)
        if not rec:
            continue
        hit = cache.get(key)
        fresh = hit and hit.get("version") == rec["version"] and hit.get("thread") == rec["thread"] and (
            hit.get("download") or time.time() - hit.get("checked", 0) < RETRY_HOURS * 3600)
        if not fresh and login_ok and session:
            try:
                url, problem = _resolve(rec, rules.get(key, {}), session)
                hit = {"version": rec["version"], "thread": rec["thread"], "download": url,
                       "problem": problem, "checked": time.time()}
                cache[key] = hit
                log(f"login download for {rec['id']} {rec['version']}: {url or problem}")
            except fl.LoginRequired:
                login_ok = False
                log("fossic login expired or invalid; update fossic_login.txt")
            except Exception as e:
                log(f"login download for {rec['id']} failed: {e!r}")
        if hit and hit.get("version") == rec["version"]:
            if hit.get("download"):
                downloads[key] = hit["download"]
            elif hit.get("problem"):
                problems[key] = hit["problem"]
    save_cache(cache)
    return downloads, problems, login_ok
