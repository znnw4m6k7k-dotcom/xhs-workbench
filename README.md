# the rumah小红书工作台

给女装团队用的小红书图文工作台：产品图（对镜自拍 / 街拍）→ 选题和文案 → 到点由浏览器自动发布 → 历史记录。出图用锁图工作台 xhs-lock-studio，代码和规则在本仓库的 `lock-studio/`，安装时自动放到「文稿 / 文档」文件夹。

**第一次用？看 [使用说明.md](使用说明.md)**：安装、第一次设置、1 款 × 1 号试跑、出问题怎么办。

## 第一次安装（把这段发给 Codex）

仓库是私有的，先让管理员把你的 GitHub 账号加成协作者，并在邮箱里接受邀请。Mac 和 Windows 都能装。

**Mac：**

```
把 https://github.com/znnw4m6k7k-dotcom/xhs-workbench 克隆到 ~/xhs-workbench，然后运行 bash ~/xhs-workbench/install.sh。
它会装到 ~/.codex/skills/xhs-autopilot，先备份旧文件，再跑测试并重启工作台。跑完把输出告诉我。
```

**Windows：**

```
把 https://github.com/znnw4m6k7k-dotcom/xhs-workbench 克隆到 %USERPROFILE%\xhs-workbench，然后在 PowerShell 里运行
powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\xhs-workbench\install.ps1"
它会装到 %USERPROFILE%\.codex\skills\xhs-autopilot，先备份旧文件，再跑测试并重启工作台。跑完把输出告诉我。
没找到 Python 时它会给出安装命令（winget install -e --id Python.Python.3.12），装好后重开 Codex 再运行一次。
```

新电脑也可以直接装。已经装过的电脑会先备份旧文件，店里自己的 fashion.json、start.sh、stop.sh 不会被覆盖。锁图工作台会一起装到 `~/Documents/xhs-lock-studio`：只更新规则和工具，不动里面的模特、产品照片、参考图和 jobs。模特照片、产品照片、参考图不进仓库（`lock-studio/.gitignore` 拦住了所有图片），新电脑要找管理员拷一份放进 `library/`。

## 更新到最新版（把这段发给 Codex）

```
Mac：     cd ~/xhs-workbench && git pull && bash install.sh
Windows： cd $env:USERPROFILE\xhs-workbench; git pull; powershell -ExecutionPolicy Bypass -File .\install.ps1
```

## 打开

安装完会自动打开浏览器。以后要打开就让 Codex 运行 `bash ~/.codex/skills/xhs-autopilot/panel/start.sh`（Windows：`python "$env:USERPROFILE\.codex\skills\xhs-autopilot\panel\start.py"`，或双击那个文件夹里的 `start.cmd`），它会给出地址（端口被占用时会自动换一个）。左侧四大板块：产品图、文案、发布、历史记录。

## 注意

- 到点发帖靠这台电脑：**电脑要开机、别合盖、工作台要开着**（有待发时工作台会让 Mac / Windows 别自己睡着）。错过 30 分钟以上不会补发，在「历史记录 › 没发出」里点「重新排期」。
- 第一次发之前，在「发布 › 待发布」点「只填不发试一次」检查。
- AI 标注默认打开，每篇发布前可以取消勾选。
- 登录资料在 `~/.xhs-studio/publisher/profiles`，只存在本机，不要提交到仓库。

## 回退

安装时的备份在 `~/.codex/skills/xhs-autopilot/panel/backup-时间-before-update/`，把里面的文件拷回原位置，再关掉、打开一次工作台（Mac：stop.sh / start.sh；Windows：stop.py / start.py）。

## Mac 和 Windows 的区别

| | Mac | Windows |
|---|---|---|
| 安装 | `bash install.sh` | `powershell -ExecutionPolicy Bypass -File install.ps1`（两者都是跑 `install.py`） |
| 打开 / 关掉 | `panel/start.sh` / `panel/stop.sh` | `panel/start.py` / `panel/stop.py`，或双击 `start.cmd` / `stop.cmd` |
| 不让电脑睡 | caffeinate | 系统的 SetThreadExecutionState（合盖都会睡） |
| 暂停发布通知 | 系统通知 | Windows 通知中心 |
| 发布用的浏览器 | Chrome，没有就用 Playwright 的 Chromium | Chrome，没有就用自带的 Edge |
| 交给 Codex 的任务 | 原样 | 提示词从标准输入送进去（避开 codex.cmd 对多行中文的截断），并提醒 Codex 把 python3 换成 python |

## 开发

```
cd panel && python3 -m unittest test_server -q
cd publisher && python3 -m unittest test_publish -q
# Windows 上把 python3 换成 python，最好先 set PYTHONUTF8=1
```
