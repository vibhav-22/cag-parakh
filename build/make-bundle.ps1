<#
.SYNOPSIS
    DEPRECATED: assembles the legacy portable development bundle.

.DESCRIPTION
    Produces a self-contained folder that runs on a Windows machine with no
    Python, no Node, and no internet connection. The folder is zipped and handed
    to a tester, who unzips it and double-clicks Parakh.exe.

    The bundle ships a relocatable CPython rather than a PyInstaller executable
    on purpose. backend/service.py runs every detector as a separate process
    built from `[sys.executable, ROOT/tools/.../script.py]`; frozen, sys.executable
    is the Electron binary and those scripts do not exist as files, so analysis
    would fail while /health still answered 200. Shipping a real interpreter and
    the real source tree keeps that contract intact.

    Layout produced under -OutputDir:

        Parakh/
          Parakh.exe
          resources/app/            frontend build + electron shell + node_modules
          resources/app-config.json build-time settings read by main.cjs
          resources/runtime/python/ embeddable CPython + site-packages
          resources/parakh/         backend/, tools/, vendor/  <- the backend's ROOT

.PARAMETER AuthUrl
    HTTPS URL of the authorization service. Baked into app-config.json. The
    packaged app refuses to start without it, and backend/access_control.py
    rejects a non-HTTPS non-local value.

.PARAMETER PythonEmbedZip
    Path to python-3.13.x-embed-amd64.zip, downloaded from python.org. Not
    fetched automatically: the bundle's interpreter is a deliberate choice, not
    a build-time surprise.

.PARAMETER VendorDir
    Directory holding the native dependencies to bundle, in the layout
    backend/dependencies.py expects:
        tesseract/tesseract.exe  (+ tessdata/)
        poppler/bin/             (whole directory - the exes need sibling DLLs)
        insightface/models/buffalo_l/
    Defaults to <repo>/vendor.

.EXAMPLE
    .\build\make-bundle.ps1 -AuthUrl https://parakh-auth.example.com `
        -PythonEmbedZip C:\downloads\python-3.13.14-embed-amd64.zip
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$AuthUrl,
    [Parameter(Mandatory = $true)][string]$PythonEmbedZip,
    [string]$VendorDir,
    [string]$OutputDir,
    [switch]$SkipSmokeTest
)

$ErrorActionPreference = "Stop"
Write-Warning "make-bundle.ps1 is retained only for legacy development compatibility. Employee releases must use build\make-installer.ps1; this script does not produce the offline-auth NSIS installer."
throw "Portable cloud-auth bundles are retired. Use build\make-installer.ps1."
$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $VendorDir) { $VendorDir = Join-Path $repoRoot "vendor" }
if (-not $OutputDir) { $OutputDir = Join-Path $repoRoot "release" }

$frontendDir = Join-Path $repoRoot "frontend"
$stageDir = Join-Path $OutputDir "Parakh"

function Step($message) { Write-Host "`n==> $message" -ForegroundColor Cyan }
function Fail($message) { Write-Error $message }

# --------------------------------------------------------------------------
# 1. Preflight. Every check here is a failure that would otherwise surface as a
#    confusing error on a tester's laptop rather than on the build machine.
# --------------------------------------------------------------------------
Step "Preflight"

# Mirrors the exemption in backend/access_control.py's is_loopback check: only
# loopback may skip HTTPS. Anything else must be https:// or the packaged app
# fails closed at first launch, which looks like a bundle bug rather than a
# config mistake.
$isLoopbackAuthUrl = $AuthUrl -match '^http://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?(/|$)'
if ($AuthUrl -notmatch '^https://' -and -not $isLoopbackAuthUrl) {
    Fail "AuthUrl must be HTTPS (loopback http://127.0.0.1 is exempt, for local pilot testing only). backend/access_control.py rejects anything else, and the packaged app fails closed, which looks like a bundle bug."
}
if ($isLoopbackAuthUrl) {
    Write-Warning "AuthUrl is loopback ($AuthUrl) -- this bundle will only work on THIS machine. Fine for a local Stage A dry run, not for a real pilot laptop."
}
if (-not (Test-Path $PythonEmbedZip)) {
    Fail "Python embeddable distribution not found at $PythonEmbedZip. Download python-3.13.x-embed-amd64.zip from https://www.python.org/downloads/windows/ (the 'Windows embeddable package (64-bit)' link)."
}

# These four degrade silently: a missing one does not raise, it just produces a
# softer verdict. That is exactly the failure the pilot exists to rule out, so
# refuse to build a bundle that is already missing them.
$requiredVendor = @(
    "tesseract\tesseract.exe",
    "poppler\bin\pdftoppm.exe",
    "poppler\bin\pdfinfo.exe",
    "insightface\models\buffalo_l"
)
$missingVendor = @()
foreach ($item in $requiredVendor) {
    if (-not (Test-Path (Join-Path $VendorDir $item))) { $missingVendor += $item }
}
if ($missingVendor.Count -gt 0) {
    Fail "Missing native dependencies under ${VendorDir}:`n  $($missingVendor -join "`n  ")`n`nRun build\stage-vendor.ps1 first. Without these the app still runs but silently loses checks."
}

$gitSha = (& git -C $repoRoot rev-parse HEAD).Trim()
$dirty = (& git -C $repoRoot status --porcelain)
if ($dirty) {
    Write-Warning "Working tree is dirty. The bundle will not correspond to a single commit."
}

# --------------------------------------------------------------------------
# 2. Frontend + Electron shell.
# --------------------------------------------------------------------------
Step "Building frontend and packaging Electron shell"

Push-Location $frontendDir
try {
    & npm run desktop:pack
    if ($LASTEXITCODE -ne 0) { Fail "electron-builder failed." }
}
finally { Pop-Location }

$unpacked = Join-Path $frontendDir "release\win-unpacked"
if (-not (Test-Path $unpacked)) { Fail "electron-builder produced no win-unpacked directory at $unpacked." }

if (Test-Path $stageDir) { Remove-Item -Recurse -Force $stageDir }
New-Item -ItemType Directory -Force -Path $stageDir | Out-Null
Copy-Item -Recurse -Force (Join-Path $unpacked "*") $stageDir

$resources = Join-Path $stageDir "resources"

# --------------------------------------------------------------------------
# 3. Python runtime.
# --------------------------------------------------------------------------
Step "Installing Python runtime"

$pythonDir = Join-Path $resources "runtime\python"
New-Item -ItemType Directory -Force -Path $pythonDir | Out-Null
Expand-Archive -Path $PythonEmbedZip -DestinationPath $pythonDir -Force

# The embeddable build ships with site-packages off, and its ._pth file is the
# only switch for that. The file also becomes the *entire* sys.path: it disables
# PYTHONPATH and the working directory, so the application root has to be listed
# here or `import backend.app` cannot resolve however uvicorn is invoked.
$pthFile = Get-ChildItem -Path $pythonDir -Filter "python*._pth" | Select-Object -First 1
if (-not $pthFile) { Fail "No python*._pth in the embeddable distribution; the archive may be the wrong one." }
$pth = @(Get-Content $pthFile.FullName)
$pth = $pth -replace '^#\s*import site', 'import site'
if ($pth -notcontains "Lib\site-packages") { $pth += "Lib\site-packages" }
# Relative to the interpreter directory: resources\runtime\python -> resources\parakh
if ($pth -notcontains "..\..\parakh") { $pth += "..\..\parakh" }
# WriteAllLines, not Set-Content: a UTF-8 BOM here corrupts the first entry
# (python313.zip), and Python then fails to find its own stdlib with the
# thoroughly unhelpful "Failed to import encodings module".
$utf8NoBomPth = New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllLines($pthFile.FullName, $pth, $utf8NoBomPth)

$sitePackages = Join-Path $pythonDir "Lib\site-packages"
New-Item -ItemType Directory -Force -Path $sitePackages | Out-Null

# Installed with the build machine's own interpreter so wheels are selected for
# this platform, then targeted at the embedded runtime. insightface has no
# prebuilt Windows wheel and compiles on install; if this step fails, that is
# almost always why, and the fix is to build the wheel once and pass a local
# --find-links directory.
$buildPython = (Get-Command python).Source
Step "  pip install -r backend/requirements.txt --target"
& $buildPython -m pip install --quiet --no-warn-script-location `
    --target $sitePackages -r (Join-Path $repoRoot "backend\requirements.txt")
if ($LASTEXITCODE -ne 0) { Fail "pip install into the bundle runtime failed." }

# --------------------------------------------------------------------------
# 4. Application source. This directory becomes the ROOT that
#    backend/app_paths.py resolves against, so it must keep the repo's shape.
# --------------------------------------------------------------------------
Step "Copying backend, tools, and vendored natives"

$appRoot = Join-Path $resources "parakh"
New-Item -ItemType Directory -Force -Path $appRoot | Out-Null

# /XD excludes directories, /XF files. Three of these are correctness matters
# rather than size: backend\data holds real screened documents, backend\models
# and backend\runtime hold multi-GB local VLM weights that the bundle does not
# use, and tools\signature_analysis\weights holds AGPL-3.0-origin weights that
# must not be redistributed and that the current analyzer does not load.
$robocopyExcludes = @(
    "/XD", "data", ".venv", "__pycache__", "models", "runtime", "weights", ".pytest_cache", "tests",
    "/XF", "*.log", "*.pyc", "*.job.json", "*.batch.json", ".env"
)
& robocopy (Join-Path $repoRoot "backend") (Join-Path $appRoot "backend") /E /NFL /NDL /NJH /NJS @robocopyExcludes | Out-Null
& robocopy (Join-Path $repoRoot "tools") (Join-Path $appRoot "tools") /E /NFL /NDL /NJH /NJS @robocopyExcludes | Out-Null
& robocopy $VendorDir (Join-Path $appRoot "vendor") /E /NFL /NDL /NJH /NJS | Out-Null
# robocopy exits 0-7 for success; 8+ is a real failure.
if ($LASTEXITCODE -ge 8) { Fail "robocopy failed with exit code $LASTEXITCODE." }
$global:LASTEXITCODE = 0

# The pilot ships to laptops that will screen real documents. Shipping ours with
# it would be a data incident, so assert rather than trust the exclude list.
$leakedDocs = Get-ChildItem -Path $appRoot -Recurse -Include *.pdf, *.sqlite3 -File -ErrorAction SilentlyContinue
if ($leakedDocs) {
    Fail "Bundle contains documents or a job database:`n  $(($leakedDocs | Select-Object -First 10 | ForEach-Object { $_.FullName }) -join "`n  ")"
}

# --------------------------------------------------------------------------
# 5. Build-time settings and provenance.
# --------------------------------------------------------------------------
Step "Writing app-config.json and BUILD.json"

# WriteAllText rather than Out-File: Windows PowerShell's -Encoding utf8 emits a
# BOM, and JSON.parse in the launcher rejects it. The failure reads as "no
# authorization service configured", which sends you looking in the wrong place.
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllText(
    (Join-Path $resources "app-config.json"),
    (@{ authUrl = $AuthUrl } | ConvertTo-Json),
    $utf8NoBom
)

$buildInfo = [ordered]@{
    gitSha       = $gitSha
    gitDirty     = [bool]$dirty
    builtAt      = (Get-Date).ToUniversalTime().ToString("o")
    builtBy      = "$env:COMPUTERNAME\$env:USERNAME"
    authUrl      = $AuthUrl
    pythonEmbed  = (Split-Path -Leaf $PythonEmbedZip)
    nodeVersion  = (& node --version).Trim()
}
[IO.File]::WriteAllText((Join-Path $stageDir "BUILD.json"), ($buildInfo | ConvertTo-Json), $utf8NoBom)

# --------------------------------------------------------------------------
# 6. Smoke test. The bundle's own backend answers whether it can find every
#    native dependency it shipped with. A build that cannot is a failed build:
#    it would run, look healthy, and quietly under-report suspicion.
# --------------------------------------------------------------------------
if (-not $SkipSmokeTest) {
    Step "Smoke test: /api/v1/diagnostics/dependencies"

    $bundledPython = Join-Path $pythonDir "python.exe"
    $port = 8731
    $env:PARAKH_PACKAGED = ""      # skip the launch-token header for this probe
    $env:PARAKH_DATA_DIR = Join-Path $env:TEMP "parakh-smoke-$([guid]::NewGuid())"

    # Output goes to a file rather than being discarded: when the bundled
    # interpreter cannot start, the traceback is the whole diagnosis, and
    # "never became ready" on its own sends you looking in the wrong place.
    $smokeLog = Join-Path $OutputDir "smoke-test.log"
    $proc = Start-Process -FilePath $bundledPython `
        -ArgumentList "-m", "uvicorn", "backend.app:app", "--host", "127.0.0.1", "--port", $port `
        -WorkingDirectory $appRoot -PassThru -WindowStyle Hidden `
        -RedirectStandardError $smokeLog -RedirectStandardOutput "$smokeLog.out"
    try {
        $deadline = (Get-Date).AddSeconds(90)
        $status = $null
        while ((Get-Date) -lt $deadline) {
            try {
                $status = Invoke-RestMethod "http://127.0.0.1:$port/api/v1/diagnostics/dependencies" -TimeoutSec 5
                break
            } catch { Start-Sleep -Milliseconds 700 }
            if ($proc.HasExited) { break }
        }
        if (-not $status) {
            $detail = if (Test-Path $smokeLog) { Get-Content $smokeLog -Tail 25 | Out-String } else { "(no output captured)" }
            Fail "Bundled backend never became ready:`n$detail"
        }
        if (-not $status.complete) {
            Fail "Bundled backend cannot find its own native dependencies. Degraded checks:`n  $($status.degraded_checks -join "`n  ")"
        }
        Write-Host "    all native dependencies resolved" -ForegroundColor Green
    }
    finally {
        if ($proc -and -not $proc.HasExited) { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue }
        Remove-Item -Recurse -Force $env:PARAKH_DATA_DIR -ErrorAction SilentlyContinue
        Remove-Item Env:\PARAKH_PACKAGED, Env:\PARAKH_DATA_DIR -ErrorAction SilentlyContinue
    }
}

# --------------------------------------------------------------------------
# 7. Zip.
# --------------------------------------------------------------------------
Step "Compressing"

$zipPath = Join-Path $OutputDir "Parakh-$($gitSha.Substring(0,8)).zip"
if (Test-Path $zipPath) { Remove-Item -Force $zipPath }

# bsdtar (shipped with Windows 10 1803+) rather than Compress-Archive: the
# bundle is ~2 GB, and Compress-Archive buffers in memory and slows to a crawl
# at that size. Fall back only if tar is genuinely absent.
$tar = Get-Command tar.exe -ErrorAction SilentlyContinue
if ($tar) {
    Push-Location $OutputDir
    try {
        & tar.exe -a -c -f $zipPath "Parakh"
        if ($LASTEXITCODE -ne 0) { Fail "tar failed with exit code $LASTEXITCODE." }
    }
    finally { Pop-Location }
} else {
    Write-Warning "tar.exe not found; falling back to Compress-Archive, which is slow at this size."
    Compress-Archive -Path $stageDir -DestinationPath $zipPath -CompressionLevel Optimal
}

$sizeMb = [math]::Round((Get-Item $zipPath).Length / 1MB, 1)
Write-Host "`nBundle ready: $zipPath ($sizeMb MB)" -ForegroundColor Green
Write-Host "Next: unzip it under a second Windows user account and work through build/PILOT.md Stage A." -ForegroundColor Green
