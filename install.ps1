# Installer for lanshare (share-lan) — Windows PowerShell.
# Only needs Python (>=3.9) already on the system. Doesn't install any other
# package manager (uv, pipx, etc.) and doesn't need git.
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
    Write-Host "Python 3.9+ was not found on your system." -ForegroundColor Red
    Write-Host ""
    Write-Host "  Install it from https://python.org/downloads/ or the Microsoft Store," -ForegroundColor Red
    Write-Host "  then run this installer again." -ForegroundColor Red
    exit 1
}

Write-Host "-> Using $(& $Python --version) ($Python)"

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

Write-Host "-> Creating an isolated virtualenv at $VenvDir"
& $Python -m venv $VenvDir
if ($LASTEXITCODE -ne 0) {
    Write-Host "Failed to create the virtualenv." -ForegroundColor Red
    exit 1
}

$VenvPip = Join-Path $VenvDir "Scripts\pip.exe"
$VenvLanshare = Join-Path $VenvDir "Scripts\lanshare.exe"

Write-Host "-> Installing lanshare from $RepoTarball"
& $VenvPip install --quiet --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "Failed to update pip." }
& $VenvPip install --quiet --upgrade --force-reinstall $RepoTarball
if ($LASTEXITCODE -ne 0) { throw "Failed to install sharelan." }

New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
Copy-Item -Force $VenvLanshare (Join-Path $BinDir "lanshare.exe")
Copy-Item -Force (Join-Path $VenvDir "Scripts\sharelan.exe") (Join-Path $BinDir "sharelan.exe")

$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (($UserPath -split ";") -notcontains $BinDir) {
    [Environment]::SetEnvironmentVariable("Path", "$UserPath;$BinDir", "User")
    Write-Host ""
    Write-Host "  $BinDir was added to PATH. Open a new terminal for it to take effect."
}
$env:Path = "$env:Path;$BinDir"

Write-Host ""
Write-Host "lanshare installed at $BinDir\lanshare.exe" -ForegroundColor Green
Write-Host ""
Write-Host "  Try: sharelan --help (update: sharelan update)"
