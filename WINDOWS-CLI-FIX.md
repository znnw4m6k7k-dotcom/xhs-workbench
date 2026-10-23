# Windows 写档故障修复（2026-10-23）

## 现场原因

- PATH 中的 `codex.exe` 是 standalone 0.149.0；桌面程序实际使用的是 0.163.0-alpha.11.2。旧 CLI 请求 `gpt-6.1-sol` 被 ChatGPT 服务拒绝。新版 CLI 使用同一模型和现有 ChatGPT 登录已通过真实请求验证。
- WebSocket 连接超时，重试五次后才回退 HTTPS。新版 CLI 直接使用 HTTPS 的测试成功。
- 桌面程序在安装 Python 前已启动，继承的 PATH 找不到 `python` 或 `py`。工作台现在将当前 Python 解释器及 Scripts 目录加入子进程 PATH，并设置 UTF-8。

## 实施

`panel/server.py` 优先使用显式配置的 CLI；Windows 自动发现优先使用 `CODEX_CLI_PATH` 和桌面运行时目录，最后回退 PATH。日志记录实际可执行文件，失败摘要显示模型拒绝或连接错误。

本机在 `%USERPROFILE%\.codex\xhs-workbench.config.toml` 保存了仅供工作台使用的 profile：

```toml
model_provider = "xhs_https"

[model_providers.xhs_https]
name = "OpenAI"
base_url = "https://chatgpt.com/backend-api/codex"
wire_api = "responses"
requires_openai_auth = true
supports_websockets = false
```

工作台「设置」中的 Codex 参数为：

```text
exec --skip-git-repo-check -s danger-full-access -p xhs-workbench
```

该 profile 使用现有 ChatGPT 登录和用户原有模型，不写入 API key，也不修改全局模型设置。profile 是本机文件，复制仓库到另一台电脑时需要按需要另外配置。原文件及工作台 state 已备份到 `%USERPROFILE%\.xhs-studio\backup-windows-cli-*`。

## 验证

```powershell
python -m unittest discover -s "$env:USERPROFILE\xhs-workbench\panel" -p test_server.py -q
```

新增回归覆盖：桌面 CLI 优先级、显式路径覆盖、无环境变量时的桌面目录发现、PATH 回退、失败摘要、Python PATH 传递。真实验收通过工作台 `/api/job` 提交 `spec` 任务，而非模拟模型输出。

图标、PowerShell shell snapshot、其他 skill 的 frontmatter 警告是单独的启动提示；不能据此判断写档失败，应以任务退出码、真实错误和生成文件为准。
