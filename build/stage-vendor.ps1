<#
.SYNOPSIS
    Stages the native dependencies the bundle ships, into <repo>/vendor.

.DESCRIPTION
    Tesseract, Poppler, and the InsightFace buffalo_l model are not installed by
    pip and are not in the repository. On a developer machine they are on PATH
    or in the InsightFace cache; on a pilot laptop they are absent, and
    backend/dependencies.py reports that absence as lost checks rather than an
    error. That is the whole reason the bundle carries its own copies.

    This copies them out of the local installs into the layout
    backend/dependencies.py looks for, so make-bundle.ps1 can pick them up:

        vendor/tesseract/tesseract.exe   + DLLs + tessdata/
        vendor/poppler/bin/              whole directory
        vendor/insightface/models/buffalo_l/

    Run once. Re-run after upgrading any of the three.

.PARAMETER TesseractDir
    Tesseract install directory. Discovered from PATH when omitted.

.PARAMETER PopplerBinDir
    Poppler's Library\bin directory. Discovered from PATH when omitted.

.PARAMETER InsightFaceModel
    Path to a buffalo_l model directory. Defaults to ~/.insightface/models/buffalo_l,
    which InsightFace populates on first use.
#>

[CmdletBinding()]
param(
    [string]$TesseractDir,
    [string]$PopplerBinDir,
    [string]$InsightFaceModel,
    [string]$VendorDir
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $VendorDir) { $VendorDir = Join-Path $repoRoot "vendor" }

function Step($message) { Write-Host "==> $message" -ForegroundColor Cyan }

function Resolve-FromPath($exeName, $explicit, $label) {
    if ($explicit) {
        if (-not (Test-Path $explicit)) { Write-Error "$label not found at $explicit" }
        return $explicit
    }
    $command = Get-Command $exeName -ErrorAction SilentlyContinue
    if (-not $command) {
        Write-Error "$label is not on PATH and no path was given. Install it, or pass the directory explicitly."
    }
    return (Split-Path -Parent $command.Source)
}

$TesseractDir = Resolve-FromPath "tesseract" $TesseractDir "Tesseract"
$PopplerBinDir = Resolve-FromPath "pdftoppm" $PopplerBinDir "Poppler"
if (-not $InsightFaceModel) {
    $InsightFaceModel = Join-Path $HOME ".insightface\models\buffalo_l"
}
if (-not (Test-Path $InsightFaceModel)) {
    Write-Error @"
InsightFace buffalo_l not found at $InsightFaceModel.

InsightFace downloads it on first use. Seed it once on this machine with the
backend venv, then re-run:

    backend\.venv\Scripts\python.exe -c "import insightface; insightface.app.FaceAnalysis(name='buffalo_l').prepare(ctx_id=-1)"
"@
}

# --- Tesseract -------------------------------------------------------------
# The exe links against a large set of sibling DLLs and reads tessdata beside
# it, so copy the install rather than the single binary. The training and
# documentation files are dropped: they are most of the size and none of the
# runtime.
Step "Tesseract  <- $TesseractDir"
$tessOut = Join-Path $VendorDir "tesseract"
if (Test-Path $tessOut) { Remove-Item -Recurse -Force $tessOut }
New-Item -ItemType Directory -Force -Path $tessOut | Out-Null
Copy-Item (Join-Path $TesseractDir "tesseract.exe") $tessOut
Copy-Item (Join-Path $TesseractDir "*.dll") $tessOut
& robocopy (Join-Path $TesseractDir "tessdata") (Join-Path $tessOut "tessdata") /E /NFL /NDL /NJH /NJS /XF *.jar | Out-Null
if ($LASTEXITCODE -ge 8) { Write-Error "Copying tessdata failed ($LASTEXITCODE)." }
$global:LASTEXITCODE = 0

# --- Poppler ---------------------------------------------------------------
# pdftoppm and pdfinfo are dynamically linked and several detectors invoke them
# by bare name after apply_runtime_configuration() puts this directory on PATH,
# so the whole bin directory ships together.
Step "Poppler    <- $PopplerBinDir"
$popplerOut = Join-Path $VendorDir "poppler\bin"
if (Test-Path (Join-Path $VendorDir "poppler")) { Remove-Item -Recurse -Force (Join-Path $VendorDir "poppler") }
New-Item -ItemType Directory -Force -Path $popplerOut | Out-Null
& robocopy $PopplerBinDir $popplerOut /E /NFL /NDL /NJH /NJS | Out-Null
if ($LASTEXITCODE -ge 8) { Write-Error "Copying poppler failed ($LASTEXITCODE)." }
$global:LASTEXITCODE = 0

# --- InsightFace -----------------------------------------------------------
# Bundling the model is what lets a pilot laptop run face matching with no
# network: without it InsightFace would try to download on first use.
Step "InsightFace <- $InsightFaceModel"
$faceOut = Join-Path $VendorDir "insightface\models\buffalo_l"
if (Test-Path $faceOut) { Remove-Item -Recurse -Force $faceOut }
New-Item -ItemType Directory -Force -Path $faceOut | Out-Null
& robocopy $InsightFaceModel $faceOut /E /NFL /NDL /NJH /NJS | Out-Null
if ($LASTEXITCODE -ge 8) { Write-Error "Copying the InsightFace model failed ($LASTEXITCODE)." }
$global:LASTEXITCODE = 0

# --- Verify ----------------------------------------------------------------
# Same four paths backend/dependencies.py resolves. Checking them here means a
# staging mistake fails now rather than as a degraded verdict on a laptop.
$expected = @(
    "tesseract\tesseract.exe",
    "tesseract\tessdata\eng.traineddata",
    "poppler\bin\pdftoppm.exe",
    "poppler\bin\pdfinfo.exe",
    "insightface\models\buffalo_l\det_10g.onnx"
)
$missing = @($expected | Where-Object { -not (Test-Path (Join-Path $VendorDir $_)) })
if ($missing.Count -gt 0) {
    Write-Error "Staging incomplete:`n  $($missing -join "`n  ")"
}

$sizeMb = [math]::Round(((Get-ChildItem $VendorDir -Recurse -File | Measure-Object Length -Sum).Sum / 1MB), 1)
Write-Host "`nStaged $sizeMb MB into $VendorDir" -ForegroundColor Green
Write-Host "vendor/ is gitignored: these are third-party binaries, not repository content." -ForegroundColor DarkGray
