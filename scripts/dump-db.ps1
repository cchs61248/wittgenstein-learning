# dump-db.ps1 — 從 docker named volume 拷一份 learning.db 到 host
#
# 用途：驗收 / 離線 SQL 查詢 / DB Browser 開來看
# 注意：snapshot 是檔案複本；要看最新資料須重跑此 script
#
# 用法：
#   .\scripts\dump-db.ps1                              # 預設輸出到 data\learning.snapshot.db
#   .\scripts\dump-db.ps1 -Out data\backup\db.bak.db   # 自訂輸出路徑

[CmdletBinding()]
param(
    [string]$Out = "data\learning.snapshot.db",
    [string]$Volume = "wittgenstein-learning_learning_db"
)

$ErrorActionPreference = "Stop"

# 確認 repo 根目錄
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $repoRoot

# 確認 docker 跑得起來
$null = docker version 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Error "docker 沒在跑或 PATH 沒設"
    exit 1
}

# 確認 volume 存在
$volJson = docker volume inspect $Volume 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Error "找不到 volume '$Volume'。先確認 docker compose up 跑過、volume 已建。"
    exit 1
}

# 警告：worker 還在跑時 dump 可能拿到中間狀態
$busy = docker compose ps --status=running --services 2>$null | Select-String -Pattern "curriculum-worker|api"
if ($busy) {
    Write-Warning "api / curriculum-worker 還在跑。建議等 stage 完成再 dump，不然可能拿到寫到一半的 DB（讀 OK，但 stage_progress 等可能缺最新 row）。"
}

# 確認輸出目錄存在
$outFull = Resolve-Path -LiteralPath (Split-Path $Out -Parent) -ErrorAction SilentlyContinue
if (-not $outFull) {
    $outDir = Split-Path $Out -Parent
    if ($outDir) {
        New-Item -ItemType Directory -Force -Path $outDir | Out-Null
    }
}

# 用 alpine container 把 DB 從 volume 拷到 host
# 必須拷 .db + .db-shm + .db-wal 三個檔案：
#   WAL mode 下 main DB 只在 checkpoint 後更新，未 checkpoint 的頁面留在 .db-wal；
#   只拷 .db 會看到上次 checkpoint 的狀態（可能漏掉最新 session 數據）。
#   sqlite3 開 snapshot 時會自動 replay -wal 到主 DB。
$outDirAbs = (Resolve-Path -LiteralPath (Split-Path $Out -Parent)).Path
$outFileName = Split-Path $Out -Leaf
docker run --rm `
    -v "${Volume}:/src:ro" `
    -v "${outDirAbs}:/dest" `
    alpine sh -c "cp /src/learning.db /dest/$outFileName && (cp /src/learning.db-shm /dest/$outFileName-shm 2>/dev/null || true) && (cp /src/learning.db-wal /dest/$outFileName-wal 2>/dev/null || true)"

if ($LASTEXITCODE -ne 0) {
    Write-Error "snapshot 失敗"
    exit 1
}

$item = Get-Item $Out
Write-Host ""
Write-Host "✓ DB snapshot 完成" -ForegroundColor Green
Write-Host "  -> $($item.FullName)"
Write-Host "  size=$([Math]::Round($item.Length/1MB, 2)) MB  mtime=$($item.LastWriteTime)"
Write-Host ""
Write-Host "後續用法：" -ForegroundColor Yellow
Write-Host "  .\backend\.venv\Scripts\python.exe `$env:USERPROFILE\.claude\skills\wittgenstein-verify\scripts\db_check.py --session sess_XXX --db $Out"
Write-Host "  .\backend\.venv\Scripts\python.exe `$env:USERPROFILE\.claude\skills\wittgenstein-verify\scripts\inspect_session.py --session sess_XXX --db $Out"
