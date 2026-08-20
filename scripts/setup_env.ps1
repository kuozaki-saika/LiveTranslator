$ErrorActionPreference = 'Continue'   # cmd 探测 Python 时禁止终止
$root = Split-Path -Parent $PSScriptRoot
if (-not $root) { $root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path) }
$py = $null
foreach ($cand in @('py -3.12','py -3.11','py -3.10','py -3.13','py -3.14','python')) {
  $v = & cmd /c "$cand --version" 2>$null
  if ($v -match '3\.1[0-4]') { $py = $cand; Write-Output "USE $cand ($v)"; break }
}
if (-not $py) { Write-Output "NO PYTHON"; exit 1 }
if (-not (Test-Path "$root\venv\Scripts\python.exe")) {
  Invoke-Expression "$py -m venv `"$root\venv`""
}
& "$root\venv\Scripts\python.exe" -m pip install --upgrade pip -q
& "$root\venv\Scripts\python.exe" -m pip install -q --timeout 120 -r "$root\requirements.txt"
Write-Output 'ENV DONE'
& "$root\venv\Scripts\python.exe" -c "import av, faster_whisper, numpy, PySide6, pyaudiowpatch; print('IMPORTS OK')"
