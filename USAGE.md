# 使用说明

让 TriOS 能检查 fossic.org（远行星号中文论坛）上发布的 mod 的更新。

## 原理

TriOS 靠 Version Checker 文件检查更新：mod 里的 `data/config/version/version_files.csv`
指向一个 `.version` 文件，文件里的 `masterVersionFile` 是远程版本号的网址。
大部分中文 mod 没有这个文件，所以 TriOS 查不到它们。

1. **GitHub Actions**（`scrape.py`，每 6 小时一次）爬取 fossic 的 Mod 发布区，
   读取每个帖子顶部的结构化发布信息（版本号 + mod ID + 下载列表），
   为每个 mod 生成 `versions/<mod id>.version` 和 `catalog.json`。
2. **本地**（`inject.py`）给本地没有 Version Checker 文件、但 fossic 上能找到的 mod
   写入 `fossic_bridge.version` 和 `version_files.csv`，指向上面的远程文件。
3. TriOS 正常检查更新即可。

## 自动运行（推荐）

```
powershell -ExecutionPolicy Bypass -File install_autostart.ps1
```

在「启动」文件夹里放一个快捷方式，登录时静默运行 `watch.py`（无窗口），并立刻启动一次。它：

- 每 30 秒检查 mods 文件夹，发现新增或变化的 mod（包括 TriOS 一键更新后的新文件夹）就自动注入；
- 每 3 小时从 GitHub 刷新一次版本目录；
- 日志写在 `watch.log`，里面也会记录哪些 mod 有更新。

**或者随 TriOS 运行**（二选一，切换时会自动移除另一种）：

```
powershell -ExecutionPolicy Bypass -File install_autostart.ps1 -WithTriOS "E:\Recreations\Starsector_Collections\TriOS\TriOS.exe"
```

在桌面创建「TriOS (fossic)」快捷方式：先补一遍注入，再启动 TriOS；TriOS 开着时持续监视，关闭后自动退出。
直接双击 TriOS.exe 启动则不会注入，下次用快捷方式启动时补上。

卸载：`powershell -ExecutionPolicy Bypass -File install_autostart.ps1 -Uninstall`

## 手动运行

装了新 mod 或更新了 mod（新文件夹里没有注入文件）后，运行一次：

```
python inject.py --mods "E:\Recreations\Starsector_Collections\Starsector_098_RC8\mods"
```

它会打印一张表：`UPDATE` = fossic 上有新版本，后面是帖子链接。
或者直接双击 `inject.bat`。

- 只看结果不写文件：加 `--dry-run`
- 撤销所有注入：加 `--remove`

**一键更新**：mod 在 fossic 上传了附件（帖子顶部的下载列表）时，远程版本文件会带上
`directDownloadURL`（fossic CDN 上的 zip 直链），TriOS 里会显示更新按钮，点一下就能下载安装，
和英文 mod 一样。用网盘发布、或附件需要登录/积分的 mod 没有直链，只会提示有更新。

TriOS 里「打开论坛」按钮指向官方英文论坛，对这些 mod 无效；帖子链接看
`inject.py` 的输出，或 GitHub 仓库首页的 README 表格。

## 修正匹配（mods.json）

大多数 mod 按帖子头部写的 mod ID 自动匹配。以下情况要在 `mods.json` 里加一条：

| 情况 | 写法 |
|---|---|
| 帖子头部的 ID 写错了（不是 mod_info.json 里的 id） | `{"id": "真实id", "thread": 帖子号}` |
| 一个帖子发布多个 mod（本体 + 拓展） | 加 `"release": "拓展"`（匹配下载项名称的正则）或 `"releaseIndex": 1` |
| 帖子没有结构化头部 | `"mode": "edited"`：用帖子最后编辑时间当版本号（作者一编辑就会提示） |
| 匹配错了，不想用 fossic 作为这个 mod 的来源 | `{"id": "mod id", "exclude": true}` |
| 你装的就是 fossic 汉化/搬运帖里的那份文件 | `{"id": "mod id", "trustRepost": true}` |

## 来源可信度

优先级从高到低：

1. **mod 自带 Version Checker 文件**（大多数英文 mod）：完全不碰，TriOS 按原作者的来源检查。
2. **fossic「Mod原创发布」区**（及其已过时区）：fossic 就是原始来源。
3. **fossic 汉化区、搬运区**：只在前两者都没有时作为**兜底**。报告里带 `*`（`UPDATE*` / `ok*`），并尽量附上英文原帖链接。
   - 本地版本比帖子新时以本地为准，不提示更新（转载常常更新不及时）。
   - 转载帖的压缩包可能是汉化或改过的，所以默认**只提示更新、不给一键更新**；
     只有本地装的就是那份中文版（mod 名字是中文），或在 mods.json 里设了 `trustRepost`，才开启一键更新。

报告里的 `check` 表示本地版本比 fossic 新：帖子可能没及时改版本号，或作者的版本号写法特殊，需要人工确认。

改完推送到 GitHub，Actions 会自动重新抓取。

## 已知限制

- 版本号由作者在 fossic 填写，有时会和 mod_info.json 不一致（例如帖子写 `0.55`、本地是 `0.355`），
  这种情况 TriOS 的比较可能不准。
- 一键更新下载的是作者上传的压缩包。个别作者的压缩包结构不规范（多套一层文件夹、或本体和拓展打在一起），
  TriOS 可能需要你确认安装哪个文件夹。
- 只抓取 Mod原创发布 / 汉化发布 / 搬运发布 三个版块及其「已过时」版块。
