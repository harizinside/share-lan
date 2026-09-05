# Installer buat lanshare (share-lan) — Windows PowerShell.
# Cuma butuh Python (>=3.9) yang udah ada di sistem. Nggak install package
# manager lain (uv, pipx, dll) dan nggak butuh git.
#
#   powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/harizinside/share-lan/main/install.ps1 | iex"

$ErrorActionPreference = "Stop"

$RepoTarball = "https://github.com/harizinside/share-lan/archive/refs/heads/main.tar.gz"
$InstallDir  = Join-Path $env:LOCALAPPDATA "lanshare"
$VenvDir     = Join-Path $InstallDir "venv"
$BinDir      = Join-Path $InstallDir "bin"

function Find-Python {
    foreach ($candidate in @("py", "python", "python3")) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        try {
            $verOk = & $candidate -c "import sys; print(1 if sys.version_info >= (3, 9) else 0)" 2>$null
        } catch {
            continue
        }
        if ($verOk -eq "1") { return $candidate }
    }
    return $null
}

$Python = Find-Python
if (-not $Python) {
    Write-Host "Python 3.9+ nggak ketemu di sistem lu." -ForegroundColor Red
    Write-Host ""
    Write-Host "  Install dari https://python.org/downloads/ atau Microsoft Store," -ForegroundColor Red
    Write-Host "  terus jalanin lagi installer ini." -ForegroundColor Red
    exit 1
}

Write-Host "-> Pakai $(& $Python --version) ($Python)"

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

Write-Host "-> Bikin virtualenv terisolasi di $VenvDir"
& $Python -m venv $VenvDir
if ($LASTEXITCODE -ne 0) {
    Write-Host "Gagal bikin virtualenv." -ForegroundColor Red
    exit 1
}

$VenvPip = Join-Path $VenvDir "Scripts\pip.exe"
$VenvLanshare = Join-Path $VenvDir "Scripts\lanshare.exe"

Write-Host "-> Install lanshare dari $RepoTarball"
& $VenvPip install --quiet --upgrade pip
& $VenvPip install --quiet --upgrade --force-reinstall $RepoTarball

New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
Copy-Item -Force $VenvLanshare (Join-Path $BinDir "lanshare.exe")

$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (($UserPath -split ";") -notcontains $BinDir) {
    [Environment]::SetEnvironmentVariable("Path", "$UserPath;$BinDir", "User")
    Write-Host ""
    Write-Host "  $BinDir ditambahin ke PATH. Buka terminal baru biar kepakai."
}
$env:Path = "$env:Path;$BinDir"

Write-Host ""
Write-Host "lanshare kepasang di $BinDir\lanshare.exe" -ForegroundColor Green
Write-Host ""
Write-Host "  Coba: lanshare --help"
