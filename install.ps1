# Windows 安装：找到 Python 3.9+ 后运行 install.py（把仓库里的工作台装到 %USERPROFILE%\.codex\skills\xhs-autopilot）
# 用法：powershell -ExecutionPolicy Bypass -File install.ps1
$ErrorActionPreference = "Continue"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$src = Split-Path -Parent $MyInvocation.MyCommand.Path
$check = "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)"
$py = $null
if (Get-Command py -ErrorAction SilentlyContinue) {
  & py -3 -c $check 2>$null
  if ($LASTEXITCODE -eq 0) { $py = @("py", "-3") }
}
if (-not $py) {
  foreach ($name in @("python", "python3")) {
    if (Get-Command $name -ErrorAction SilentlyContinue) {
      # 微软商店的 python 占位程序跑不起来，这里会失败，接着找下一个
      & $name -c $check 2>$null
      if ($LASTEXITCODE -eq 0) { $py = @($name); break }
    }
  }
}
if (-not $py) {
  Write-Host "没找到 Python 3.9 或更新的版本。先运行下面这行装 Python，装好后关掉再重新打开 PowerShell，再运行本脚本："
  Write-Host "  winget install -e --id Python.Python.3.12"
  Write-Host "（或者到 https://www.python.org/downloads/ 下载，安装时勾选 Add python.exe to PATH）"
  exit 1
}
$exe = $py[0]
$rest = @($py | Select-Object -Skip 1)
& $exe @rest (Join-Path $src "install.py") @args
exit $LASTEXITCODE
