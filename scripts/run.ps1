$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$py = "$root\venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Write-Output '请先运行 scripts\setup_env.ps1'; exit 1 }
& $py "$root\cli.py" @args