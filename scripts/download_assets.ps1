param([switch]$SkipModels)
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
if (-not $root) { $root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path) }
New-Item -ItemType Directory -Force -Path "$root\assets\fonts","$root\models\asr","$root\models\translate","$root\llama" | Out-Null

function Get-WithMirror($Url, $Mirror, $Out) {
  $ok = $false
  foreach ($u in @($Url, $Mirror)) {
    if (-not $u) { continue }
    Write-Output "DL $u"
    curl.exe -sL -C - --retry 3 --connect-timeout 20 --max-time 7200 -o $Out $u   # -C - 断点续传
    if ($LASTEXITCODE -eq 0 -and (Test-Path $Out) -and ((Get-Item $Out).Length -gt 50)) {
      $ok = $true; Write-Output "OK $Out ($((Get-Item $Out).Length) bytes)"; break
    }
    # 失败只清掉垃圾小文件，半截文件保留供续传
    if ((Test-Path $Out) -and ((Get-Item $Out).Length -le 50)) { Remove-Item $Out -Force -ErrorAction SilentlyContinue }
  }
  if (-not $ok) { Write-Output "FAIL $Out" }
}

# 1) fonts（官方 zip 解出用得到的字重）
function Get-AllWeights($ZipUrl, $Prefix, $Label) {
  $zip = "$root\assets\fonts\_full_tmp.zip"
  $tmp = "$root\assets\fonts\_full"
  curl.exe -sL -C - --retry 3 --connect-timeout 20 --max-time 3600 -o $zip $ZipUrl   # -C - 断点续传
  if ((Test-Path $zip) -and ((Get-Item $zip).Length -gt 1000000)) {
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    Expand-Archive -Path $zip -DestinationPath $tmp -Force
    $otfs = Get-ChildItem $tmp -Recurse -Filter "$Prefix-*.otf" | Where-Object { $_.Name -notmatch 'Light' }   # 只留 400/500/600/700/900
    foreach ($otf in $otfs) {
      $dest = Join-Path "$root\assets\fonts" $otf.Name
      if (-not (Test-Path $dest)) { Copy-Item $otf.FullName $dest -Force }
    }
    Write-Output "$Label 字重: $($otfs.Count) 个"
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
  } else { Write-Output "$Label zip 下载失败" }
  Remove-Item $zip -Force -ErrorAction SilentlyContinue
}
Get-AllWeights "https://github.com/adobe-fonts/source-han-serif/releases/download/2.003R/09_SourceHanSerifSC.zip" "SourceHanSerifSC" "SC"
Get-AllWeights "https://github.com/adobe-fonts/source-han-serif/releases/download/2.003R/12_SourceHanSerifJP.zip" "SourceHanSerifJP" "JP"

if (-not $SkipModels) {
  # 2) kotoba-whisper-v2.0-faster (ASR)
  $hf  = "https://huggingface.co/kotoba-tech/kotoba-whisper-v2.0-faster/resolve/main"
  $mir = "https://hf-mirror.com/kotoba-tech/kotoba-whisper-v2.0-faster/resolve/main"
  foreach ($f in @('config.json','model.bin','preprocessor_config.json','tokenizer.json','vocabulary.json')) {
    Get-WithMirror "$hf/$f" "$mir/$f" "$root\models\asr\$f"
  }
  # 3) Sakura GalTransl-v4-4B-2601 Q6K (translation)
  Get-WithMirror "https://huggingface.co/SakuraLLM/GalTransl-v4-4B-2601/resolve/main/Galtransl-v4-4B-2601.gguf" "https://hf-mirror.com/SakuraLLM/GalTransl-v4-4B-2601/resolve/main/Galtransl-v4-4B-2601.gguf" "$root\models\translate\Galtransl-v4-4B-2601.gguf"
}

# 4) llama.cpp server (CUDA win build)
if (-not (Test-Path "$root\llama\llama-server.exe")) {
  $rel = curl.exe -sL --max-time 30 "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest" | ConvertFrom-Json
  $asset = $rel.assets | Where-Object { $_.name -match '^llama-b[0-9]+-bin-win-cuda-12\.4-x64\.zip$' } | Select-Object -First 1
  if ($asset) {
    Write-Output "llama asset: $($asset.name)"
    curl.exe -sL -C - --retry 3 --max-time 7200 -o "$root\llama\llama.zip" $asset.browser_download_url   # -C - 断点续传
    Expand-Archive -Path "$root\llama\llama.zip" -DestinationPath "$root\llama" -Force
    Remove-Item "$root\llama\llama.zip" -Force
  } else { Write-Output 'NO llama asset found' }
}
Write-Output 'ALL DONE'
