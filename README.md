# 小红书女装工作台

给女装团队用的小红书图文工作台：产品图（对镜自拍 / 街拍）→ 选题和文案 → 到点由浏览器自动发布 → 历史记录。出图依赖 [xhs-lock-studio](https://github.com/znnw4m6k7k-dotcom/xhs-lock-studio)。

## 第一次安装（把这段发给 Codex）

```
把 https://github.com/znnw4m6k7k-dotcom/xhs-workbench 克隆到 ~/xhs-workbench，然后运行 bash ~/xhs-workbench/install.sh。
它会装到 ~/.codex/skills/xhs-autopilot，先备份旧文件，再跑测试并重启工作台。跑完把输出告诉我。
```

新电脑也可以直接装。已经装过的电脑会先备份旧文件，店里自己的 fashion.json、start.sh、stop.sh 不会被覆盖。出图要另外装好 xhs-lock-studio。

## 更新到最新版（把这段发给 Codex）

```
cd ~/xhs-workbench && git pull && bash install.sh
```

## 打开

安装完会自动打开浏览器。以后要打开就让 Codex 运行 `bash ~/.codex/skills/xhs-autopilot/panel/start.sh`，它会给出地址（端口被占用时会自动换一个）。左侧四大板块：产品图、文案、发布、历史记录。

## 注意

- 到点发帖靠这台 Mac：**Mac 要开机、工作台要开着**。错过 30 分钟以上不会补发，在「历史记录 › 没发出」里点「重新排期」。
- 第一次发之前，在「发布 › 待发布」点「只填不发试一次」检查。
- AI 标注默认打开，每篇发布前可以取消勾选。
- 登录资料在 `~/.xhs-studio/publisher/profiles`，只存在本机，不要提交到仓库。

## 回退

安装时的备份在 `~/.codex/skills/xhs-autopilot/panel/backup-时间-before-update/`，把里面的文件拷回原位置，再运行 stop.sh / start.sh。

## 开发

```
cd panel && python3 -m unittest test_server -q
cd publisher && python3 -m unittest test_publish -q
```
