# 출석 앱 실행 (.env 읽어 환경변수 설정 후 서버 기동)
# 사용: 프로젝트 폴더에서  .\run.ps1   또는  .\run.ps1 -Serve  (운영 waitress)
param([switch]$Serve)

$envFile = Join-Path $PSScriptRoot ".env"
if (-not (Test-Path $envFile)) { Write-Error ".env 없음"; exit 1 }

# .env 파싱 → 프로세스 환경변수
Get-Content $envFile | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
        $i = $line.IndexOf("=")
        $name = $line.Substring(0, $i).Trim()
        $value = $line.Substring($i + 1).Trim()
        Set-Item -Path "Env:$name" -Value $value
    }
}

$port = if ($env:ATTENDANCE_PORT) { $env:ATTENDANCE_PORT } else { '5000' }
Write-Host "서버: http://localhost:$port  → /login (계정으로 로그인)" -ForegroundColor Cyan

if ($Serve) { python serve.py } else { python app.py }
