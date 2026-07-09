# 작업 스케줄러가 매일 이 스크립트를 실행한다.
# 1) lavangba_scraper.py로 수집 (결과: data/, 로그: logs/)
# 2) 성공하면 로컬 클론(C:\Users\mj204\hdhs-repo)의 lavangba/data/ 로 복사 후 commit & push

Set-Location -Path $PSScriptRoot

$logDir = Join-Path $PSScriptRoot "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logFile = Join-Path $logDir "$stamp.log"

function Log($msg) { $msg | Out-File -FilePath $logFile -Encoding utf8 -Append }

# git이 stderr로 진행 메시지를 써도 PowerShell이 에러로 오인하지 않게 cmd 경유로 실행
function RunGit($repoDir, $gitArgs) {
    $out = cmd /c "git -C `"$repoDir`" $gitArgs 2>&1"
    Log ($out | Out-String)
    return $LASTEXITCODE
}

& "$PSScriptRoot\venv\Scripts\python.exe" "$PSScriptRoot\lavangba_scraper.py" *>&1 | Out-File -FilePath $logFile -Encoding utf8
$scrapeOk = ($LASTEXITCODE -eq 0)

# ===== GitHub 자동 업로드 =====
$repoDir = "C:\Users\mj204\hdhs-repo"
if ($scrapeOk -and (Test-Path (Join-Path $repoDir ".git"))) {
    # 원격 최신화 (GitHub Actions 쪽 커밋과 충돌 방지)
    [void](RunGit $repoDir "pull --rebase origin main")

    New-Item -ItemType Directory -Force -Path (Join-Path $repoDir "lavangba\data") | Out-Null
    Copy-Item -Path (Join-Path $PSScriptRoot "data\*") -Destination (Join-Path $repoDir "lavangba\data\") -Force

    [void](RunGit $repoDir "add lavangba/data")
    $changed = RunGit $repoDir "diff --cached --quiet"
    if ($changed -ne 0) {
        $today = Get-Date -Format "yyyy-MM-dd"
        [void](RunGit $repoDir "-c user.name=mj204 -c user.email=mj2040354@gmail.com commit -m `"lavangba data update ($today)`"")
        $pushResult = RunGit $repoDir "push origin main"
        if ($pushResult -eq 0) { Log "GitHub push 완료" } else { Log "GitHub push 실패 (종료코드 $pushResult) - 인증 상태 확인 필요" }
    } else {
        Log "변경분 없음 - push 생략"
    }
} elseif (-not $scrapeOk) {
    Log "수집 실패 - GitHub 업로드 생략"
}

# 로그 30일치만 보관
Get-ChildItem $logDir -Filter "*.log" | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) } | Remove-Item -Force
