"""Write updates.html: every fossic-linked mod, its versions, whether TriOS
can update it in one click, and a clickable thread link (TriOS's own
"open download page" only knows the English forum and Nexus)."""

import datetime
import html
import os

HERE = os.path.dirname(os.path.abspath(__file__))
REPORT = os.path.join(HERE, "updates.html")

STATE_LABEL = {
    "UPDATE": ("有更新", "update"),
    "UPDATE*": ("有更新 · 转载来源", "update"),
    "check": ("需核对", "check"),
    "ok": ("已是最新", "ok"),
    "ok*": ("已是最新 · 转载来源", "ok"),
    "excluded": ("已排除", "muted"),
}
ORDER = {"UPDATE": 0, "UPDATE*": 1, "check": 2, "ok": 3, "ok*": 4, "excluded": 5}

CSS = """
:root { --bg:#f6f7f9; --card:#fff; --text:#1d2330; --muted:#677084; --line:#e3e6ec;
  --update:#0a7a4b; --update-bg:#e3f5ec; --check:#9a5b00; --check-bg:#fdf1dc; --ok:#4a5568; --ok-bg:#eef0f4;
  --link:#2459c7; --warn:#b3261e; }
@media (prefers-color-scheme: dark) { :root { --bg:#12151c; --card:#1b1f29; --text:#e6e8ee; --muted:#9aa3b5;
  --line:#2c3240; --update:#5fd6a0; --update-bg:#173528; --check:#f0b860; --check-bg:#3a2c14; --ok:#b7bfcd;
  --ok-bg:#262b37; --link:#8ab0ff; --warn:#ff8a80; } }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--text);
  font:14px/1.5 "Microsoft YaHei UI","PingFang SC","Segoe UI",sans-serif; }
main { max-width:1100px; margin:0 auto; padding:24px 16px 48px; }
h1 { font-size:20px; margin:0 0 4px; }
.meta { color:var(--muted); margin:0 0 16px; }
.notice { background:var(--card); border:1px solid var(--line); border-left:4px solid var(--warn);
  padding:10px 14px; border-radius:6px; margin:0 0 16px; }
.summary { display:flex; gap:12px; flex-wrap:wrap; margin:0 0 16px; }
.summary div { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:8px 14px; }
.summary b { font-size:18px; margin-right:4px; }
.wrap { overflow-x:auto; background:var(--card); border:1px solid var(--line); border-radius:8px; }
table { border-collapse:collapse; width:100%; min-width:760px; }
th, td { text-align:left; padding:8px 12px; border-bottom:1px solid var(--line); vertical-align:top; }
th { color:var(--muted); font-weight:600; font-size:12px; }
tr:last-child td { border-bottom:none; }
td.v { font-family:Consolas,"Cascadia Mono",monospace; white-space:nowrap; }
.badge { display:inline-block; padding:1px 8px; border-radius:999px; font-size:12px; white-space:nowrap; }
.badge.update { color:var(--update); background:var(--update-bg); }
.badge.check { color:var(--check); background:var(--check-bg); }
.badge.ok, .badge.muted { color:var(--ok); background:var(--ok-bg); }
.dl { color:var(--muted); font-size:13px; }
.dl.yes { color:var(--update); }
a { color:var(--link); text-decoration:none; } a:hover { text-decoration:underline; }
footer { color:var(--muted); font-size:12px; margin-top:16px; }
"""


def _download_cell(row, local_downloads, problems):
    if row["download"]:
        return '<span class="dl yes">TriOS 一键更新</span>'
    if row["local"]:
        if local_downloads.get(row["key"]):
            return '<span class="dl yes">TriOS 一键更新（用你的登录解析）</span>'
        why = problems.get(row["key"])
        return f'<span class="dl">需手动下载{"：" + html.escape(why) if why else ""}</span>'
    return '<span class="dl">需手动下载</span>'


def write(rows, unknown, local_downloads=None, problems=None, login_state=None):
    """login_state: None (no login configured), "ok", or "expired"."""
    local_downloads, problems = local_downloads or {}, problems or {}
    rows = sorted(rows, key=lambda r: (ORDER.get(r["state"], 9), r["name"]))
    updates = sum(r["state"].startswith("UPDATE") for r in rows)
    out = [
        "<!doctype html><html lang=zh-CN><head><meta charset=utf-8>",
        '<meta name=viewport content="width=device-width,initial-scale=1">',
        f"<title>fossic mod 更新</title><style>{CSS}</style></head><body><main>",
        "<h1>fossic mod 更新</h1>",
        f'<p class="meta">生成于 {datetime.datetime.now():%Y-%m-%d %H:%M} · 数据来自 fossic.org，每 6 小时由 GitHub 抓取</p>',
    ]
    if login_state == "expired":
        out.append('<p class="notice">fossic 登录已失效：需要登录才能下载的 mod 暂时不能一键更新。'
                   "请按 fossic_login.example.txt 的说明重新填写 fossic_login.txt。</p>")
    out.append('<div class="summary">'
               f"<div><b>{updates}</b>个有更新</div>"
               f"<div><b>{len(rows)}</b>个已关联 fossic</div>"
               f"<div><b>{len(unknown)}</b>个无法检查</div></div>")
    out.append('<div class="wrap"><table><thead><tr><th>状态</th><th>Mod</th><th>本地</th><th>fossic</th>'
               "<th>更新方式</th><th>帖子</th></tr></thead><tbody>")
    for r in rows:
        label, cls = STATE_LABEL.get(r["state"], (r["state"], "muted"))
        out.append(
            f'<tr><td><span class="badge {cls}">{label}</span></td>'
            f'<td>{html.escape(r["name"])}</td>'
            f'<td class="v">{html.escape(r["installed"])}</td>'
            f'<td class="v">{html.escape(r["fossic"])}</td>'
            f"<td>{_download_cell(r, local_downloads, problems)}</td>"
            f'<td><a href="{html.escape(r["thread"])}" target="_blank" rel="noopener">打开帖子</a></td></tr>')
    out.append("</tbody></table></div>")
    if unknown:
        out.append(f'<footer>没有版本信息、fossic 上也找不到的 mod：{html.escape("、".join(unknown))}</footer>')
    out.append("<footer>「需核对」：本地版本比帖子新，可能是帖子没及时改版本号，或作者的版本号写法特殊。"
               "「转载来源」：fossic 上只有汉化/搬运帖，本地更新时以本地为准。</footer>")
    out.append("</main></body></html>")
    tmp = REPORT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    os.replace(tmp, REPORT)
