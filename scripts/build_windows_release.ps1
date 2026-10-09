param(
    [switch]$InstallerOnly,
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$Root = (Get-Location).Path
$AppVersion = (Get-Content (Join-Path $Root "VERSION") -Raw).Trim()
$Release = Join-Path $Root "release"
$Dist = Join-Path $Root "dist"
$Build = Join-Path $Root "build"
$Venv = Join-Path $Root ".build-venv"
$InstalledPython = $false
$InstalledInno = $false
$Python = $null

function Find-Python {
    $candidates = @()
    try { $candidates += (Get-Command py -ErrorAction Stop).Source } catch {}
    try { $candidates += (Get-Command python -ErrorAction Stop).Source } catch {}
    $candidates += "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
    foreach ($c in $candidates | Select-Object -Unique) {
        if (-not (Test-Path $c) -and ([IO.Path]::GetFileName($c) -ne "py.exe")) { continue }
        try {
            if ([IO.Path]::GetFileName($c) -eq "py.exe") {
                & $c -3.12 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" 2>$null
                if ($LASTEXITCODE -eq 0) { return @{Exe=$c; Args=@('-3.12')} }
                & $c -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" 2>$null
                if ($LASTEXITCODE -eq 0) { return @{Exe=$c; Args=@('-3')} }
            } else {
                & $c -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" 2>$null
                if ($LASTEXITCODE -eq 0) { return @{Exe=$c; Args=@()} }
            }
        } catch {}
    }
    return $null
}

function Invoke-BuildPython([string[]]$CmdArgs) {
    & $Python.Exe @($Python.Args) @CmdArgs
    if ($LASTEXITCODE -ne 0) { throw "Python command failed: $($CmdArgs -join ' ')" }
}

function Find-InnoCompiler {
    return @(
      "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
      "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
      "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    ) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
}

if ($env:OS -ne "Windows_NT") { throw "This builder must be run on Windows." }

try {
    $Python = Find-Python
    if (-not $Python) {
        $winget = Get-Command winget -ErrorAction SilentlyContinue
        if (-not $winget) {
            throw "Python 3.11+ was not found and Windows Package Manager (winget) is unavailable. Install Python 3.12 once, or use a published Tertium binary release."
        }
        Write-Host "No build Python found. Acquiring a temporary per-user Python 3.12 toolchain..." -ForegroundColor Cyan
        winget install --id Python.Python.3.12 -e --scope user --silent --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -ne 0) { throw "winget could not acquire Python 3.12." }
        $InstalledPython = $true
        $Python = Find-Python
        if (-not $Python) { throw "Python was acquired but could not be located." }
    }

    Write-Host "[1/6] Creating isolated build environment..." -ForegroundColor Cyan
    if (Test-Path $Venv) { Remove-Item $Venv -Recurse -Force }
    Invoke-BuildPython @('-m','venv',$Venv)
    $Vpy = Join-Path $Venv 'Scripts\python.exe'
    & $Vpy -m pip install --disable-pip-version-check -r (Join-Path $Root 'requirements-dev.txt')
    if ($LASTEXITCODE -ne 0) { throw "Could not install isolated build dependencies." }

    if ($SkipTests) {
        Write-Host "[2/6] Tests already completed by release pipeline; skipping duplicate run." -ForegroundColor DarkGray
    } else {
        Write-Host "[2/6] Running tests..." -ForegroundColor Cyan
        & $Vpy -m pytest -q
        if ($LASTEXITCODE -ne 0) { throw "Tests failed; release build stopped." }
    }

    Write-Host "[3/6] Building native Windows application..." -ForegroundColor Cyan
    foreach ($p in @($Release,$Dist,$Build)) { if (Test-Path $p) { Remove-Item $p -Recurse -Force } }
    New-Item $Release -ItemType Directory | Out-Null
    & $Vpy -m PyInstaller --noconfirm --clean (Join-Path $Root 'TertiumModManager.spec')
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }
    $Exe = Join-Path $Dist 'TertiumModManager\TertiumModManager.exe'
    if (-not (Test-Path $Exe)) { throw "Expected executable was not produced." }

    if ($InstallerOnly) {
        Write-Host "[4/6] Installer-only mode: portable ZIP omitted." -ForegroundColor DarkGray
    } else {
        Write-Host "[4/6] Building optional portable no-Python ZIP..." -ForegroundColor Cyan
        $Portable = Join-Path $Release ("TertiumModManager-{0}-Portable-x64.zip" -f $AppVersion)
        Compress-Archive -Path (Join-Path $Dist 'TertiumModManager\*') -DestinationPath $Portable -CompressionLevel Optimal
    }

    Write-Host "[5/6] Building Windows installer..." -ForegroundColor Cyan
    $Iscc = Find-InnoCompiler
    if (-not $Iscc) {
        $winget = Get-Command winget -ErrorAction SilentlyContinue
        if ($winget) {
            Write-Host "Inno Setup not found. Acquiring it for this build..." -ForegroundColor Cyan
            winget install --id JRSoftware.InnoSetup -e --silent --accept-package-agreements --accept-source-agreements
            if ($LASTEXITCODE -eq 0) { $InstalledInno = $true }
            $Iscc = Find-InnoCompiler
        }
    }
    if (-not $Iscc) {
        throw "Inno Setup is unavailable, so Tertium could not create the Windows installer."
    }
    & $Iscc "/DMyAppVersion=$AppVersion" (Join-Path $Root 'installer\TertiumModManager.iss')
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed." }
    $Installer = Join-Path $Release 'TertiumModManager-Setup-x64.exe'
    if (-not (Test-Path $Installer)) { throw "Expected Windows installer was not produced." }

    Write-Host "[6/6] Writing SHA-256 files..." -ForegroundColor Cyan
    Get-ChildItem $Release -File | Where-Object { $_.Extension -ne '.sha256' } | ForEach-Object {
        $h=(Get-FileHash -Algorithm SHA256 $_.FullName).Hash.ToLower()
        Set-Content -NoNewline ($_.FullName + '.sha256') ($h + '  ' + $_.Name)
    }

    Write-Host "Release build complete." -ForegroundColor Green
    Write-Host "Installer: $Installer" -ForegroundColor Green
    Write-Host "Release output: $Release" -ForegroundColor Green
}
finally {
    if (Test-Path $Venv) {
        try { Remove-Item $Venv -Recurse -Force } catch { Write-Warning "Could not remove temporary build venv: $_" }
    }
    if ($InstalledInno) {
        try { winget uninstall --id JRSoftware.InnoSetup -e --silent | Out-Null } catch { Write-Warning "Could not remove temporary Inno Setup install." }
    }
    if ($InstalledPython) {
        try { winget uninstall --id Python.Python.3.12 -e --silent | Out-Null } catch { Write-Warning "Could not remove temporary Python build toolchain." }
    }
}
