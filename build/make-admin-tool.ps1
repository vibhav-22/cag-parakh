<# Build the administrator-only offline authorization utility as one Windows executable. #>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$BuildPythonExe,
    [string]$OutputDir
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $OutputDir) { $OutputDir = Join-Path $repoRoot "release\admin" }
$workDir = Join-Path $repoRoot "tmp\pyinstaller-admin"
$entryPoint = Join-Path $repoRoot "license_server\manage.py"

if (-not (Test-Path -LiteralPath $BuildPythonExe -PathType Leaf)) {
    throw "Build Python not found: $BuildPythonExe"
}
if (Test-Path -LiteralPath $workDir) {
    Remove-Item -LiteralPath $workDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $workDir, $OutputDir | Out-Null

& $BuildPythonExe -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --console `
    --name "Parakh-Authorization-Manager" `
    --paths $repoRoot `
    --distpath $OutputDir `
    --workpath (Join-Path $workDir "work") `
    --specpath $workDir `
    $entryPoint
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE." }

$executable = Join-Path $OutputDir "Parakh-Authorization-Manager.exe"
if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    throw "Administrator utility was not produced: $executable"
}
& $executable --help | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Administrator utility smoke test failed." }

$signature = Get-AuthenticodeSignature -LiteralPath $executable
Write-Host "Administrator utility ready: $executable" -ForegroundColor Green
Write-Host "SHA-256: $((Get-FileHash -LiteralPath $executable -Algorithm SHA256).Hash)"
Write-Host "Authenticode: $($signature.Status)"
