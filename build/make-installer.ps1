<#
.SYNOPSIS
    Builds the self-contained Parakh 1.0.0-pilot Windows installer.

.DESCRIPTION
    Stages immutable runtimes under frontend/.packaging, runs the test suites,
    builds an NSIS installer, and emits SHA-256/provenance manifests. Employee
    documents, sessions, authorization.json, and the authorization private key
    are forbidden from the staging tree.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$PythonEmbedZip,
    [Parameter(Mandatory = $true)][string]$BuildPythonExe,
    [Parameter(Mandatory = $true)][string]$PublicKeyFile,
    [string]$PythonEmbedSha256 = "90B4E5B9898B72D744650524BFF92377C367F44BD5FBD09E3148656C080AD907",
    [string]$VendorDir,
    [string]$Wheelhouse,
    [switch]$SkipTests,
    [switch]$SkipNpmCi,
    [switch]$RequireSigned
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$frontendDir = Join-Path $repoRoot "frontend"
$packagingDir = Join-Path $frontendDir ".packaging"
$runtimeRequirements = Join-Path $PSScriptRoot "requirements-runtime.lock"
if (-not $VendorDir) { $VendorDir = Join-Path $repoRoot "vendor" }
$OutputDir = Join-Path $repoRoot "release\windows"

function Step([string]$Message) { Write-Host "`n==> $Message" -ForegroundColor Cyan }
function Fail([string]$Message) { throw $Message }
function Assert-File([string]$Path, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { Fail "$Label not found: $Path" }
}
function Copy-Tree([string]$Source, [string]$Destination, [object[]]$ExtraArgs = @()) {
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    & robocopy $Source $Destination /E /NFL /NDL /NJH /NJS @ExtraArgs | Out-Null
    $code = $LASTEXITCODE
    $global:LASTEXITCODE = 0
    if ($code -ge 8) { Fail "robocopy failed ($code): $Source -> $Destination" }
}
function File-Hash([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToUpperInvariant()
}
function Directory-Manifest([string]$Root) {
    $resolved = (Resolve-Path -LiteralPath $Root).Path
    return @(
        Get-ChildItem -LiteralPath $resolved -Recurse -File | Sort-Object FullName | ForEach-Object {
            [ordered]@{
                path = $_.FullName.Substring($resolved.Length).TrimStart('\').Replace('\', '/')
                bytes = $_.Length
                sha256 = File-Hash $_.FullName
            }
        }
    )
}
function Write-JsonNoBom([string]$Path, [object]$Value) {
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($Path, ($Value | ConvertTo-Json -Depth 8), $encoding)
}

Step "Preflight"
Assert-File $PythonEmbedZip "Python embeddable archive"
Assert-File $BuildPythonExe "Build Python"
Assert-File $PublicKeyFile "Authorization public key"
Assert-File $runtimeRequirements "Pinned runtime requirements"
$actualPythonHash = File-Hash $PythonEmbedZip
if ($actualPythonHash -ne $PythonEmbedSha256.ToUpperInvariant()) {
    Fail "Python archive SHA-256 mismatch. Expected $PythonEmbedSha256, got $actualPythonHash."
}
$publicKeyText = Get-Content -LiteralPath $PublicKeyFile -Raw
if (-not $publicKeyText.Trim()) { Fail "Authorization public key is empty." }
if ($publicKeyText -match 'PRIVATE KEY') { Fail "PublicKeyFile contains private-key material; refusing to package it." }

$requiredVendor = @(
    "tesseract\tesseract.exe",
    "tesseract\tessdata\eng.traineddata",
    "poppler\bin\pdftoppm.exe",
    "poppler\bin\pdfinfo.exe",
    "insightface\models\buffalo_l\det_10g.onnx"
)
foreach ($relative in $requiredVendor) { Assert-File (Join-Path $VendorDir $relative) "Vendored dependency" }

$buildPythonInfo = (& $BuildPythonExe -c "import json,platform,sys; print(json.dumps({'major':sys.version_info.major,'minor':sys.version_info.minor,'bits':platform.architecture()[0],'version':platform.python_version()}))") | ConvertFrom-Json
if ($buildPythonInfo.major -ne 3 -or $buildPythonInfo.minor -ne 13 -or $buildPythonInfo.bits -ne "64bit") {
    Fail "BuildPythonExe must be 64-bit Python 3.13; found $($buildPythonInfo.version) $($buildPythonInfo.bits)."
}
$nodeVersion = (& node --version).Trim()
if ([int](($nodeVersion -replace '^v','').Split('.')[0]) -lt 22) { Fail "Node 22 or newer is required; found $nodeVersion." }

$gitSha = (& git -C $repoRoot rev-parse HEAD).Trim()
$dirty = [bool](& git -C $repoRoot status --porcelain --untracked-files=all)
if ($dirty) { Write-Warning "Working tree is dirty. BUILD.json records this; release approval should use a clean commit." }

Step "Preparing isolated staging tree"
if (Test-Path -LiteralPath $packagingDir) { Remove-Item -LiteralPath $packagingDir -Recurse -Force }
$runtimeDir = Join-Path $packagingDir "runtime\python"
$appRoot = Join-Path $packagingDir "parakh"
$authorizationDir = Join-Path $packagingDir "authorization"
New-Item -ItemType Directory -Force -Path $runtimeDir, $appRoot, $authorizationDir | Out-Null
Expand-Archive -LiteralPath $PythonEmbedZip -DestinationPath $runtimeDir -Force

$embeddedPython = Join-Path $runtimeDir "python.exe"
$embedInfo = (& $embeddedPython -c "import json,platform,sys; print(json.dumps({'major':sys.version_info.major,'minor':sys.version_info.minor,'bits':platform.architecture()[0],'version':platform.python_version()}))") | ConvertFrom-Json
if ($embedInfo.major -ne $buildPythonInfo.major -or $embedInfo.minor -ne $buildPythonInfo.minor -or $embedInfo.bits -ne $buildPythonInfo.bits) {
    Fail "Build Python $($buildPythonInfo.version) $($buildPythonInfo.bits) does not match embedded Python $($embedInfo.version) $($embedInfo.bits)."
}

$pthFile = Get-ChildItem -LiteralPath $runtimeDir -Filter "python*._pth" | Select-Object -First 1
if (-not $pthFile) { Fail "The embeddable archive contains no python*._pth file." }
$pth = @(Get-Content -LiteralPath $pthFile.FullName)
$pth = $pth -replace '^#\s*import site', 'import site'
if ($pth -notcontains "Lib\site-packages") { $pth += "Lib\site-packages" }
if ($pth -notcontains "..\..\parakh") { $pth += "..\..\parakh" }
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllLines($pthFile.FullName, $pth, $utf8NoBom)

$sitePackages = Join-Path $runtimeDir "Lib\site-packages"
New-Item -ItemType Directory -Force -Path $sitePackages | Out-Null
$pipArgs = @("-m", "pip", "install", "--disable-pip-version-check", "--no-warn-script-location", "--target", $sitePackages)
if ($Wheelhouse) {
    if (-not (Test-Path -LiteralPath $Wheelhouse -PathType Container)) { Fail "Wheelhouse not found: $Wheelhouse" }
    $pipArgs += @("--no-index", "--find-links", $Wheelhouse)
} else {
    Write-Warning "No Wheelhouse supplied; PyPI availability can affect reproducibility. Production releases should use a reviewed offline wheelhouse."
}
$pipArgs += @("-r", $runtimeRequirements)
Step "Installing Python dependencies"
& $BuildPythonExe @pipArgs
if ($LASTEXITCODE -ne 0) { Fail "pip install into embedded runtime failed." }

$copyExcludes = @(
    "/XD", "data", ".venv", "__pycache__", "models", "runtime", "weights", ".pytest_cache", "tests",
    "/XF", "*.log", "*.pyc", "*.job.json", "*.batch.json", ".env", "authorization.json", "*.sqlite3"
)
Step "Copying application and native dependencies"
Copy-Tree (Join-Path $repoRoot "backend") (Join-Path $appRoot "backend") $copyExcludes
Copy-Tree (Join-Path $repoRoot "tools") (Join-Path $appRoot "tools") $copyExcludes
Copy-Tree $VendorDir (Join-Path $appRoot "vendor")
# Immutable defense in depth: backend/app_paths.py treats this resource marker
# as packaged mode even if somebody starts bundled python.exe directly.
New-Item -ItemType File -Force -Path (Join-Path $appRoot ".parakh-packaged") | Out-Null
Copy-Item -LiteralPath $PublicKeyFile -Destination (Join-Path $authorizationDir "public-key.pem")

$forbidden = @(Get-ChildItem -LiteralPath $packagingDir -Recurse -File | Where-Object {
    $relativePath = $_.FullName.Substring($packagingDir.Length).TrimStart('\').Replace('/', '\')
    $isTrustedMatplotlibIcon =
        $_.Extension -eq ".pdf" -and
        $relativePath -like "runtime\python\Lib\site-packages\matplotlib\mpl-data\images\*.pdf"
    ($_.Extension -eq ".pdf" -and -not $isTrustedMatplotlibIcon) -or
        $_.Extension -eq ".sqlite3" -or
        $_.Name -eq "authorization.json" -or
        $_.Name -match '(?i)private.*\.(pem|key)$'
})
if ($forbidden) { Fail "Forbidden user/private material in staging:`n  $(($forbidden.FullName) -join "`n  ")" }
$privateMarker = Get-ChildItem -LiteralPath $packagingDir -Recurse -File |
    Where-Object { $_.Extension -in ".pem", ".key" } |
    Select-String -SimpleMatch "PRIVATE KEY" -ErrorAction SilentlyContinue
if ($privateMarker) { Fail "Private-key marker found in installer staging: $($privateMarker.Path)" }

if (-not $SkipNpmCi) {
    Step "Installing locked frontend dependencies"
    Push-Location $frontendDir
    try { & npm ci --include=dev; if ($LASTEXITCODE -ne 0) { Fail "npm ci failed." } }
    finally { Pop-Location }
}
if (-not $SkipTests) {
    Step "Running backend tests"
    Push-Location $repoRoot
    try {
        & $BuildPythonExe -m pytest (Join-Path $repoRoot "backend\tests")
        if ($LASTEXITCODE -ne 0) { Fail "Backend tests failed." }
    }
    finally { Pop-Location }
    Step "Running frontend tests"
    Push-Location $frontendDir
    try { & npm test; if ($LASTEXITCODE -ne 0) { Fail "Frontend tests failed." } }
    finally { Pop-Location }
}

$pythonDistributions = @(
    Get-ChildItem -LiteralPath $sitePackages -Directory -Filter "*.dist-info" |
        Sort-Object Name | ForEach-Object { $_.Name -replace '\.dist-info$','' }
)
$buildInfo = [ordered]@{
    schemaVersion = 1
    product = "Parakh"
    version = "1.0.0-pilot"
    gitSha = $gitSha
    gitDirty = $dirty
    builtAtUtc = (Get-Date).ToUniversalTime().ToString("o")
    nodeVersion = $nodeVersion
    buildPython = "$($buildPythonInfo.version) $($buildPythonInfo.bits)"
    embeddedPython = "$($embedInfo.version) $($embedInfo.bits)"
    inputs = [ordered]@{
        pythonEmbed = [ordered]@{ name = Split-Path -Leaf $PythonEmbedZip; sha256 = $actualPythonHash }
        publicKeySha256 = File-Hash $PublicKeyFile
        packageLockSha256 = File-Hash (Join-Path $frontendDir "package-lock.json")
        requirementsSha256 = File-Hash $runtimeRequirements
        vendor = Directory-Manifest $VendorDir
        wheelhouse = if ($Wheelhouse) { Directory-Manifest $Wheelhouse } else { @() }
    }
    pythonDistributions = $pythonDistributions
}
Write-JsonNoBom (Join-Path $packagingDir "BUILD.json") $buildInfo

Step "Building NSIS installer"
Push-Location $frontendDir
try { & npm run desktop:installer; if ($LASTEXITCODE -ne 0) { Fail "electron-builder NSIS build failed." } }
finally { Pop-Location }

$installer = Get-ChildItem -LiteralPath $OutputDir -File -Filter "Parakh-1.0.0-pilot-Setup-x64.exe" | Select-Object -First 1
if (-not $installer) { Fail "NSIS installer was not produced under $OutputDir." }

Step "Verifying release"
& (Join-Path $PSScriptRoot "verify-release.ps1") `
    -InstallerPath $installer.FullName `
    -UnpackedDir (Join-Path $OutputDir "win-unpacked") `
    -RequireSigned:$RequireSigned
if ($LASTEXITCODE -ne 0) { Fail "Release verification failed." }

Write-Host "`nInstaller ready: $($installer.FullName)" -ForegroundColor Green
Write-Host "SHA-256: $(File-Hash $installer.FullName)" -ForegroundColor Green
