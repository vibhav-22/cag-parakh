<# Verifies the unpacked application and installer without launching either. #>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$InstallerPath,
    [Parameter(Mandatory = $true)][string]$UnpackedDir,
    [switch]$RequireSigned
)

$ErrorActionPreference = "Stop"
function Fail([string]$Message) { throw $Message }
function Assert-File([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { Fail "Required release file is missing: $Path" }
}

Assert-File $InstallerPath
$InstallerPath = (Resolve-Path -LiteralPath $InstallerPath).Path
if (-not (Test-Path -LiteralPath $UnpackedDir -PathType Container)) {
    Fail "Unpacked release directory is missing: $UnpackedDir"
}
$UnpackedDir = (Resolve-Path -LiteralPath $UnpackedDir).Path
foreach ($relative in @(
    "Parakh.exe",
    "resources\BUILD.json",
    "resources\authorization\public-key.pem",
    "resources\runtime\python\python.exe",
    "resources\parakh\.parakh-packaged",
    "resources\parakh\backend\app.py",
    "resources\parakh\vendor\tesseract\tesseract.exe",
    "resources\parakh\vendor\poppler\bin\pdftoppm.exe",
    "resources\parakh\vendor\poppler\bin\pdfinfo.exe",
    "resources\parakh\vendor\insightface\models\buffalo_l\det_10g.onnx"
)) { Assert-File (Join-Path $UnpackedDir $relative) }

$forbidden = @(Get-ChildItem -LiteralPath $UnpackedDir -Recurse -File | Where-Object {
    $relativePath = $_.FullName.Substring($UnpackedDir.Length).TrimStart('\').Replace('/', '\')
    $isTrustedMatplotlibIcon =
        $_.Extension -eq ".pdf" -and
        $relativePath -like "resources\runtime\python\Lib\site-packages\matplotlib\mpl-data\images\*.pdf"
    ($_.Extension -eq ".pdf" -and -not $isTrustedMatplotlibIcon) -or
        $_.Extension -eq ".sqlite3" -or
        $_.Name -eq "authorization.json" -or
        $_.Name -match '(?i)private.*\.(pem|key)$'
})
if ($forbidden) { Fail "Release contains user/private material:`n  $(($forbidden.FullName) -join "`n  ")" }
$privateMarker = Get-ChildItem -LiteralPath $UnpackedDir -Recurse -File |
    Where-Object { $_.Extension -in ".pem", ".key" } |
    Select-String -SimpleMatch "PRIVATE KEY" -ErrorAction SilentlyContinue
if ($privateMarker) { Fail "Release contains a private-key marker: $($privateMarker.Path)" }

$build = Get-Content -LiteralPath (Join-Path $UnpackedDir "resources\BUILD.json") -Raw | ConvertFrom-Json
if ($build.version -ne "1.0.0-pilot") { Fail "BUILD.json has unexpected version: $($build.version)" }
$signature = Get-AuthenticodeSignature -LiteralPath $InstallerPath
$appSignature = Get-AuthenticodeSignature -LiteralPath (Join-Path $UnpackedDir "Parakh.exe")
if ($RequireSigned -and $signature.Status -ne "Valid") { Fail "Installer signature is $($signature.Status), not Valid." }
if ($RequireSigned -and $appSignature.Status -ne "Valid") { Fail "Packaged Parakh.exe signature is $($appSignature.Status), not Valid." }
if (-not $RequireSigned -and $signature.Status -ne "Valid") {
    Write-Warning "Installer is unsigned. SmartScreen warnings are expected until the release is code-signed."
}

$installer = Get-Item -LiteralPath $InstallerPath
$manifest = [ordered]@{
    schemaVersion = 1
    product = "Parakh"
    version = "1.0.0-pilot"
    installer = [ordered]@{
        name = $installer.Name
        bytes = $installer.Length
        sha256 = (Get-FileHash -LiteralPath $installer.FullName -Algorithm SHA256).Hash.ToUpperInvariant()
        authenticodeStatus = [string]$signature.Status
        signer = if ($signature.SignerCertificate) { $signature.SignerCertificate.Subject } else { $null }
    }
    applicationAuthenticodeStatus = [string]$appSignature.Status
    build = $build
    checks = [ordered]@{
        immutablePackagedMarker = $true
        publicKeyPresent = $true
        privateKeyAbsent = $true
        authorizationFileAbsent = $true
        userDocumentsAbsent = $true
        jobDatabaseAbsent = $true
        bundledDependenciesPresent = $true
    }
    verifiedAtUtc = (Get-Date).ToUniversalTime().ToString("o")
}
$manifestPath = Join-Path (Split-Path -Parent $InstallerPath) "RELEASE-MANIFEST.json"
$encoding = New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllText($manifestPath, ($manifest | ConvertTo-Json -Depth 10), $encoding)
Write-Host "Release verification passed. Manifest: $manifestPath" -ForegroundColor Green
