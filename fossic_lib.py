"""Shared helpers: fetching fossic.org pages, reading the structured mod
release block, and comparing versions the way TriOS does. Standard library
only, so the GitHub Action needs no dependencies."""

import base64
import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://www.fossic.org/"
USER_AGENT = "fossic-version-bridge/1.0 (+https://github.com/Prophet-C/fossic-version-bridge)"
REQUEST_DELAY = 1.0  # seconds between requests; be polite to the forum

# Original releases, translations, reposts, and their "outdated" archives.
# Earlier entries win when two threads claim the same mod id.
FORUMS = [46, 71, 60, 78, 67, 68, 79]
# Mod原创发布 (and its archive) and Mod制作发布 (71, under Mod制作讨论; e.g.
# maintainer continuations) are a mod's own source. Translations (汉化) and
# reposts (搬运) mirror a mod published elsewhere, usually the English forum,
# and often lag behind it.
ORIGINAL_FORUMS = {46, 67, 71}


def origin_of(forum):
    return "original" if forum in ORIGINAL_FORUMS else "repost"


def has_cjk(s):
    return bool(re.search(r"[一-鿿]", s or ""))

_last_request = 0.0


def fetch(url, retries=3):
    global _last_request
    for attempt in range(retries):
        wait = REQUEST_DELAY - (time.time() - _last_request)
        if wait > 0:
            time.sleep(wait)
        _last_request = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(3 * (attempt + 1))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_no_redirect = urllib.request.build_opener(_NoRedirect)


ARCHIVE_EXTS = {"zip", "7z", "rar"}


def attach_info(aids):
    """fossic's own download panel asks this endpoint for attachment details:
    {aid: {"filename", "ext", "filesize", "downloadUrl", ...}}. Works without
    a login. downloadUrl is relative and carries a time-stamped hash."""
    result = {}
    aids = list(aids)
    for i in range(0, len(aids), 40):
        chunk = ",".join(str(a) for a in aids[i:i + 40])
        data = json.loads(fetch(f"{BASE}forum.php?mod=ajax&action=attachinfo_json&aids={chunk}") or "{}")
        if isinstance(data, dict):
            result.update({int(k): v for k, v in data.items()})
    return result


def resolve_download(attachment_url):
    """fossic answers an anonymous attachment request with a 302 to a static
    CDN file (stable, no expiry; the file name comes in Content-Disposition,
    which TriOS reads). Returns that URL, or None when the attachment needs a
    login or credits (fossic then shows an HTML notice instead)."""
    global _last_request
    wait = REQUEST_DELAY - (time.time() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.time()
    req = urllib.request.Request(attachment_url, headers={"User-Agent": USER_AGENT})
    try:
        with _no_redirect.open(req, timeout=30):
            return None  # 200 = notice page, not a file
    except urllib.error.HTTPError as e:
        location = e.headers.get("Location") if e.code in (301, 302, 303, 307) else None
        if location and urllib.parse.urlparse(location).hostname not in (None, "www.fossic.org", "fossic.org"):
            return location
        return None


def thread_url(tid):
    return f"{BASE}thread-{tid}-1-1.html"


def strip_tags(s):
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s).replace("\xa0", " ")).strip()


def list_forum_threads(fid):
    """Yield (tid, title) for every thread in a forum section."""
    first = fetch(f"{BASE}forum-{fid}-1.html")
    m = re.search(r'<span title="共 (\d+) 页"', first)
    pages = int(m.group(1)) if m else 1
    seen = set()
    for page in range(1, pages + 1):
        content = first if page == 1 else fetch(f"{BASE}forum-{fid}-{page}.html")
        for tid, title in re.findall(
            r'<a href="(?:https://www\.fossic\.org/)?thread-(\d+)-1-\d+\.html"[^>]*class="s xst"[^>]*>(.*?)</a>',
            content,
            flags=re.S,
        ):
            if tid not in seen:
                seen.add(tid)
                yield int(tid), strip_tags(title)


def parse_thread(content):
    """Read a mod release thread.

    fossic's mod forums use a structured header ("分类信息"): the release
    version and mod id, plus a JSON list of downloadable releases. Old threads
    posted before that template have neither.
    """
    info = {"title": "", "modId": None, "headerVersion": None, "releases": [], "edited": None}

    m = re.search(r'<span id="thread_subject"[^>]*>(.*?)</span>', content, flags=re.S)
    if m:
        info["title"] = strip_tags(m.group(1))

    m = re.search(r'<span id="mod_release_version"><code>(.*?)</code> / ID:<code>(.*?)</code>', content, flags=re.S)
    if m:
        info["headerVersion"] = strip_tags(m.group(1)) or None
        info["modId"] = strip_tags(m.group(2)) or None

    m = re.search(r"_ta\.innerHTML = '(\[.*?\])';", content, flags=re.S)
    if m:
        try:
            info["releases"] = [
                {"modVersion": str(r.get("modVersion", "")).strip(),
                 "gameVersion": r.get("gameVersion", ""),
                 "name": str(r.get("modVersionDisplayName", "")).strip(),
                 "aid": r.get("aid")}
                for r in json.loads(html.unescape(m.group(1)))
            ]
        except ValueError:
            pass

    # Attachments in the first post (many threads upload archives there
    # without filling in the release list). Links carry "aid|hash|time|uid|tid".
    info["postAids"] = []
    start = content.find('id="postmessage_')
    end = content.find('<div id="comment_', start) if start >= 0 else -1
    body = content[start:end if end > 0 else start + 300000] if start >= 0 else ""
    for enc in re.findall(r"mod=attachment&(?:amp;)?aid=([A-Za-z0-9+/=%]+)", body):
        enc = urllib.parse.unquote(enc)
        try:
            aid = int(base64.b64decode(enc + "=" * (-len(enc) % 4)).decode("ascii").split("|")[0])
        except (ValueError, UnicodeDecodeError):
            continue
        if aid not in info["postAids"]:
            info["postAids"].append(aid)

    # First post's last edit, else its posting time.
    m = re.search(r"本帖最后由 .*? 于 (\d{4}-\d{1,2}-\d{1,2} \d{1,2}:\d{2}) 编辑", content)
    if not m:
        m = re.search(r'<em id="authorposton\d+">发表于 (?:<span title=")?(\d{4}-\d{1,2}-\d{1,2} \d{1,2}:\d{2})', content)
    if m:
        info["edited"] = m.group(1)
    return info


def is_version(s):
    return bool(s) and bool(re.match(r"^[vV]?\d", s))


def _game_rank(release):
    """'modVersion_098x' -> 98, 'modVersion_0951x' -> 95.1; newer game first."""
    m = re.match(r"modVersion_0(\d+)x", release.get("gameVersion", ""))
    if not m:
        return 0.0
    digits = m.group(1)
    return float(digits[:2] + "." + digits[2:]) if len(digits) > 2 else float(digits)


def pick_version(info, rule=None):
    """Choose the version a thread advertises for one mod.

    Returns (version, release): release is the entry of the download list
    that version came from, or None when it came from elsewhere (header,
    edit time, fixed value), in which case there is no download to offer.

    rule (from mods.json) may set:
      release: regex matched against the release's display name or version,
               for threads that ship several mods (e.g. a main mod + addon)
      releaseIndex: position in the release list, when names don't help
      mode: "edited" to use the thread's last edit time instead
      version: a fixed value
    """
    rule = rule or {}
    if rule.get("version"):
        return normalize(rule["version"]), None
    if rule.get("mode") == "edited":
        return edited_version(info["edited"]), None

    releases = [r for r in info["releases"] if is_version(r["modVersion"])]
    if "releaseIndex" in rule:
        all_releases = info["releases"]
        i = rule["releaseIndex"]
        if i < len(all_releases) and is_version(all_releases[i]["modVersion"]):
            return normalize(all_releases[i]["modVersion"]), all_releases[i]
        return None, None
    if rule.get("release"):
        for r in releases:
            if re.search(rule["release"], r["name"]) or re.search(rule["release"], r["modVersion"]):
                return normalize(r["modVersion"]), r
        return None, None

    # Default: the newest of the header version and the releases for the
    # newest game version. The header is sometimes stale and the release list
    # sometimes lags behind, so take whichever is ahead.
    candidates = []
    header = info["headerVersion"]
    # Some authors type the game version ("0.98") into the mod version field;
    # when a release list exists, trust it over such a header.
    if is_version(header) and not (releases and re.match(r"^0\.9\d*a?$", header)):
        candidates.append((normalize(header), None))
    if releases:
        top = max(_game_rank(r) for r in releases)
        candidates += [(normalize(r["modVersion"]), r) for r in releases if _game_rank(r) == top]
    if not candidates:
        return None, None
    best = candidates[0]
    for c in candidates[1:]:
        # On a tie prefer the release entry, which has a download.
        cmp = compare_versions(c[0], best[0])
        if cmp > 0 or (cmp == 0 and best[1] is None):
            best = c
    version, release = best
    if release is None:
        # Header won; still offer the download if a release has that version.
        release = next((r for r in releases if normalize(r["modVersion"]) == version), None)
    return version, release


def pick_post_archive(version, files):
    """Choose the in-post attachment that is this version's archive.

    files: {aid: {"filename", "ext", "dateline"}}. Only an archive whose
    name contains the version counts; a lone archive without the version in
    its name may be an old upload, and a wrong file would make TriOS
    "update" forever. Several matches are accepted only when they are
    re-uploads of one file name (the newest wins). Returns an aid or None.
    """
    if not version:
        return None
    v = re.escape(normalize(version).lower())
    pattern = re.compile(r"(?<![\d.])v?" + v + r"(?!\.?\d)")
    matches = []
    for aid, d in files.items():
        if str(d.get("ext", "")).lower() not in ARCHIVE_EXTS:
            continue
        stem = re.sub(r"\.(zip|7z|rar)$", "", str(d.get("filename", "")), flags=re.I)
        if pattern.search(normalize(stem).lower().replace("_", "-")):
            matches.append((aid, d))
    if not matches:
        return None
    if len({m[1]["filename"] for m in matches}) > 1:
        return None
    return max(matches, key=lambda m: m[1].get("dateline") or 0)[0]


def edited_version(edited):
    """"2026-10-20 13:30" -> "2026.10.20.1330", comparable as a version."""
    if not edited:
        return None
    date, _, hm = edited.partition(" ")
    y, mo, d = date.split("-")
    return f"{y}.{int(mo):02d}.{int(d):02d}.{hm.replace(':', '')}"


def normalize(version):
    """Canonical string form used on both the local and remote side."""
    v = str(version).strip()
    v = re.sub(r"^[vV](?=\d)", "", v)
    return re.sub(r"\s+", "-", v)


def version_string(raw):
    """mod_info.json "version" may be a string or a {major, minor, patch} object."""
    if isinstance(raw, dict):
        parts = [raw.get("major"), raw.get("minor"), raw.get("patch")]
        return normalize(".".join(str(p) for p in parts if p not in (None, "")))
    return normalize(raw)


def version_object(version):
    """Split "1.13.7d" into {"major":"1","minor":"13","patch":"7d"}.

    TriOS joins the three parts back with "." before comparing, so the split
    must round-trip exactly: the remainder after the second dot goes into
    patch untouched.
    """
    parts = version.split(".", 2)
    obj = {"major": parts[0], "minor": parts[1] if len(parts) > 1 else "0"}
    if len(parts) > 2:
        obj["patch"] = parts[2]
    return obj


# --- Port of TriOS lib/models/version.dart compareVersions -----------------

_TOKEN = re.compile(r"\d+|[a-zA-Z]+|[-.]+")
_SEP = re.compile(r"[\s\-–_]+")
_SUFFIX_RANK = {s: i for i, s in enumerate(["dev", "prerelease", "preview", "pre", "alpha", "beta", "rc"])}


def _is_num(s):
    return s.isdigit()


def compare_versions(a, b):
    """Returns 1 if a > b, -1 if a < b, 0 if equal, following TriOS."""
    if a == b:
        return 0
    a_orig, b_orig = a, b
    a_tokens_orig, b_tokens_orig = _TOKEN.findall(a), _TOKEN.findall(b)
    ap, bp = _TOKEN.findall(_SEP.sub(".", a)), _TOKEN.findall(_SEP.sub(".", b))

    ar, br = [], []
    for i in range(max(len(ap), len(bp))):
        x = ap[i] if i < len(ap) else ""
        y = bp[i] if i < len(bp) else ""
        x_letter, y_letter = bool(re.search("[a-zA-Z]", x)), bool(re.search("[a-zA-Z]", y))
        if x_letter and _is_num(y):
            ar.append("0")
        elif y_letter and _is_num(x):
            br.append("0")
        elif x == "" and _is_num(y):
            x = "0"
        elif y == "" and _is_num(x):
            y = "0"
        elif x == "" and y == ".":
            x = "."
        elif y == "" and x == ".":
            y = "."
        elif (x == "" and y_letter) or (y == "" and x_letter):
            pass
        elif x == "" and y:
            x = y
        elif y == "" and x:
            y = x
        ar.append(x)
        br.append(y)

    for i in range(max(len(ar), len(br))):
        x = ar[i] if i < len(ar) else ""
        y = br[i] if i < len(br) else ""
        if _is_num(x) and _is_num(y):
            if int(x) != int(y):
                return 1 if int(x) > int(y) else -1
        elif _is_num(x):
            return 1
        elif _is_num(y):
            return -1
        else:
            xi, yi = _SUFFIX_RANK.get(x.lower()), _SUFFIX_RANK.get(y.lower())
            if xi is not None and yi is not None:
                if xi != yi:
                    return 1 if xi > yi else -1
            elif xi is not None:
                return -1
            elif yi is not None:
                return 1
            if x != y:
                return 1 if x > y else -1

    if len(a_tokens_orig) != len(b_tokens_orig):
        return 1 if len(a_tokens_orig) > len(b_tokens_orig) else -1
    if a_orig != b_orig:
        return 1 if a_orig > b_orig else -1
    return 0


# --- Starsector files ---------------------------------------------------------

def load_json_lenient(path):
    """Read Starsector-style JSON: comments, trailing commas, bare keys."""
    with open(path, encoding="utf-8-sig") as f:
        text = f.read()
    # Drop "#", "//" and "/* */" comments that sit outside string literals.
    out, i, n, in_str = [], 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
            out.append(c)
        elif c == "#" or text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        else:
            out.append(c)
        i += 1
    text = "".join(out)
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    text = re.sub(r'([{,]\s*)([A-Za-z_]\w*)(\s*:)', r'\1"\2"\3', text)
    return json.loads(text, strict=False)


def catalog_key(mod_id):
    """Catalog keys and file names are case-insensitive: fossic threads spell
    the same id differently ("csp"/"Csp"), and Windows checkouts would clash.
    Mod ids may contain spaces or CJK; only strip what Windows rejects."""
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", mod_id).strip(" .").lower()


def master_file_name(mod_id, with_download=False):
    """Repost-sourced mods get two master files: "<id>.version" without a
    download link and "<id>.dl.version" with one, so a mod installed from
    its English original is never one-click-replaced by a repost's archive.
    Original-sourced mods have the link in "<id>.version" itself."""
    return catalog_key(mod_id) + (".dl.version" if with_download else ".version")


def master_url(raw_base, mod_id, with_download=False):
    return f'{raw_base.rstrip("/")}/versions/{urllib.parse.quote(master_file_name(mod_id, with_download))}'
