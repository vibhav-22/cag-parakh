<# Build the optional, offline Qwen model pack from already-downloaded inputs. #>
[CmdletBinding()]
param(
    [string]$ModelDir,
    [string]$ModelFile,
    [string]$VisionProjectorFile,
    [string]$LlamaCpuZip,
    [string]$LlamaVulkanZip,
    [string]$PythonExe,
    [string]$OutputDir
)

$ErrorActionPreference = "Stop"
$scriptDir = $PSScriptRoot
$repoRoot = Split-Path -Parent (Split-Path -Parent $scriptDir)
$pins = Get-Content (Join-Path $scriptDir "pins.json") -Raw | ConvertFrom-Json
if (-not $OutputDir) { $OutputDir = Join-Path $repoRoot "release\model-pack" }
$OutputDir = [IO.Path]::GetFullPath($OutputDir)
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$lockPath = Join-Path $OutputDir ".model-pack-build.lock"
try {
    $buildLock = [IO.File]::Open(
        $lockPath,
        [IO.FileMode]::OpenOrCreate,
        [IO.FileAccess]::ReadWrite,
        [IO.FileShare]::None
    )
}
catch {
    throw "Another model-pack build is already using $OutputDir. Wait for it to finish or choose another -OutputDir."
}
trap {
    if ($buildLock) { $buildLock.Dispose() }
    Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
    [Console]::Error.WriteLine($_.ToString())
    exit 1
}
if (-not $ModelDir) { $ModelDir = Join-Path $repoRoot "backend\models\qwen3-vl-4b-q4" }
if (-not $ModelFile) { $ModelFile = Join-Path $ModelDir $pins.model.file.name }
if (-not $VisionProjectorFile) { $VisionProjectorFile = Join-Path $ModelDir $pins.model.mmproj_file.name }
$archiveDir = Join-Path $repoRoot "backend\runtime\llama-b10298\archives"
if (-not $LlamaCpuZip) { $LlamaCpuZip = Join-Path $archiveDir $pins.runtime.archives.cpu.name }
if (-not $LlamaVulkanZip) { $LlamaVulkanZip = Join-Path $archiveDir $pins.runtime.archives.vulkan.name }
if (-not $PythonExe) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCommand) {
        throw "No build Python is available on PATH. Pass -PythonExe with a Python that can import the Parakh backend dependencies."
    }
    $PythonExe = $pythonCommand.Source
}
if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) { throw "Build Python not found: $PythonExe" }
$stage = Join-Path $OutputDir "$($pins.pack_id)\$($pins.pack_version)"

function Assert-Hash([string]$Path, [string]$Expected, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "$Label not found: $Path" }
    $actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $Expected.ToLowerInvariant()) {
        throw "$Label SHA-256 mismatch. Expected $Expected; got $actual."
    }
}

function Expand-Runtime([string]$Archive, [string]$Destination) {
    $scratch = Join-Path ([IO.Path]::GetTempPath()) "parakh-vlm-$([guid]::NewGuid())"
    try {
        New-Item -ItemType Directory -Force -Path $scratch | Out-Null
        Expand-Archive -LiteralPath $Archive -DestinationPath $scratch -Force
        $server = Get-ChildItem -LiteralPath $scratch -Filter "llama-server.exe" -File -Recurse | Select-Object -First 1
        if (-not $server) { throw "Runtime archive has no llama-server.exe: $Archive" }
        New-Item -ItemType Directory -Force -Path $Destination | Out-Null
        Copy-Item -Path (Join-Path $server.Directory.FullName "*") -Destination $Destination -Recurse -Force
        if (-not (Test-Path (Join-Path $Destination "llama-server.exe"))) {
            throw "Failed to stage llama-server.exe from $Archive"
        }
    }
    finally {
        if (Test-Path -LiteralPath $scratch) { Remove-Item -LiteralPath $scratch -Recurse -Force }
    }
}

Assert-Hash $ModelFile $pins.model.file.sha256 "Qwen model"
Assert-Hash $VisionProjectorFile $pins.model.mmproj_file.sha256 "Qwen vision projector"
Assert-Hash $LlamaCpuZip $pins.runtime.archives.cpu.sha256 "llama.cpp CPU archive"
Assert-Hash $LlamaVulkanZip $pins.runtime.archives.vulkan.sha256 "llama.cpp Vulkan archive"

if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
New-Item -ItemType Directory -Force -Path (Join-Path $stage "model") | Out-Null
Copy-Item -LiteralPath $ModelFile -Destination (Join-Path $stage "model\$($pins.model.file.name)")
Copy-Item -LiteralPath $VisionProjectorFile -Destination (Join-Path $stage "model\$($pins.model.mmproj_file.name)")
Expand-Runtime $LlamaCpuZip (Join-Path $stage "runtime\cpu")
Expand-Runtime $LlamaVulkanZip (Join-Path $stage "runtime\vulkan")
Copy-Item -LiteralPath (Join-Path $scriptDir "licenses") -Destination (Join-Path $stage "licenses") -Recurse
$requiredStageFiles = @(
    "model\$($pins.model.file.name)",
    "model\$($pins.model.mmproj_file.name)",
    "runtime\cpu\llama-server.exe",
    "runtime\vulkan\llama-server.exe"
)
$missingStageFiles = @($requiredStageFiles | Where-Object { -not (Test-Path -LiteralPath (Join-Path $stage $_) -PathType Leaf) })
if ($missingStageFiles.Count -gt 0) { throw "Model-pack staging is incomplete:`n  $($missingStageFiles -join "`n  ")" }

$hashes = [ordered]@{}
$resolvedStage = (Resolve-Path -LiteralPath $stage).Path.TrimEnd("\") + "\"
Get-ChildItem -LiteralPath $stage -File -Recurse | Sort-Object FullName | ForEach-Object {
    $resolvedFile = (Resolve-Path -LiteralPath $_.FullName).Path
    if (-not $resolvedFile.StartsWith($resolvedStage, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Staged file escaped the model-pack root: $resolvedFile"
    }
    # System.IO.Path.GetRelativePath is unavailable in Windows PowerShell 5.1.
    $relative = $resolvedFile.Substring($resolvedStage.Length).Replace("\", "/")
    $hashes[$relative] = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
}
$manifest = [ordered]@{
    schema_version = 1
    pack_id = $pins.pack_id
    pack_version = $pins.pack_version
    model = [ordered]@{
        alias = $pins.model.alias
        source = $pins.model.source
        revision = $pins.model.revision
        quantization = $pins.model.quantization
        tokenizer = $pins.model.tokenizer
        configuration = $pins.model.configuration
        file = "model/$($pins.model.file.name)"
        sha256 = $pins.model.file.sha256
        mmproj_file = "model/$($pins.model.mmproj_file.name)"
        mmproj_quantization = $pins.model.mmproj_file.quantization
        mmproj_sha256 = $pins.model.mmproj_file.sha256
    }
    runtime = [ordered]@{
        name = $pins.runtime.name
        version = $pins.runtime.version
        commit = $pins.runtime.commit
        context_size = $pins.runtime.context_size
        backend_preference = @($pins.runtime.backend_preference)
        executables = [ordered]@{
            vulkan = "runtime/vulkan/llama-server.exe"
            cpu = "runtime/cpu/llama-server.exe"
        }
        source_archives = $pins.runtime.archives
    }
    files = $hashes
}
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllText((Join-Path $stage "model-pack.json"), ($manifest | ConvertTo-Json -Depth 12), $utf8NoBom)

# Reuse the backend's runtime validator, including a full hash pass, before an
# archive can be described as successful.
$oldPackPath = $env:PARAKH_VLM_MODEL_PACK
$env:PARAKH_VLM_MODEL_PACK = $stage
Push-Location $repoRoot
try {
    & $PythonExe -c "import sys; from backend.vlm_model_pack import discover_model_pack, verify_file_hashes; p=discover_model_pack(); errors=verify_file_hashes(p); print('model-pack hashes verified' if not errors else '; '.join(errors)); sys.exit(1 if errors else 0)"
    if ($LASTEXITCODE -ne 0) { throw "backend.vlm_model_pack rejected the staged pack." }
}
finally {
    Pop-Location
    if ($null -eq $oldPackPath) { Remove-Item Env:\PARAKH_VLM_MODEL_PACK -ErrorAction SilentlyContinue }
    else { $env:PARAKH_VLM_MODEL_PACK = $oldPackPath }
}

# Normalize timestamps so identical pinned inputs produce a stable archive.
$epoch = [datetime]::SpecifyKind([datetime]"2000-01-01T00:00:00", [DateTimeKind]::Utc)
Get-ChildItem -LiteralPath $stage -Recurse -Force | ForEach-Object { $_.LastWriteTimeUtc = $epoch }
(Get-Item -LiteralPath $stage).LastWriteTimeUtc = $epoch

$archive = Join-Path $OutputDir "Parakh-Qwen3-VL-4B-Instruct-Q4_K_M-$($pins.pack_version).zip"
if (Test-Path -LiteralPath $archive) { Remove-Item -LiteralPath $archive -Force }
$tar = Get-Command tar.exe -ErrorAction SilentlyContinue
if (-not $tar) { throw "tar.exe is required because the model pack exceeds Compress-Archive's size limit." }
Push-Location (Split-Path -Parent $stage)
try {
    & tar.exe -a -c -f $archive (Split-Path -Leaf $stage)
    if ($LASTEXITCODE -ne 0) { throw "tar.exe failed with exit code $LASTEXITCODE." }
}
finally { Pop-Location }

$archiveEntries = @(& tar.exe -tf $archive)
if ($LASTEXITCODE -ne 0) { throw "The completed model-pack archive could not be listed." }
$archiveRoot = Split-Path -Leaf $stage
$requiredArchiveFiles = @(
    "$archiveRoot/model/$($pins.model.file.name)",
    "$archiveRoot/model/$($pins.model.mmproj_file.name)"
)
$missingArchiveFiles = @($requiredArchiveFiles | Where-Object { $_ -notin $archiveEntries })
if ($missingArchiveFiles.Count -gt 0) {
    throw "The completed archive is missing required model files:`n  $($missingArchiveFiles -join "`n  ")"
}
# A concurrent or external mutation after archiving must not be hidden either.
$missingAfterArchive = @($requiredStageFiles | Where-Object { -not (Test-Path -LiteralPath (Join-Path $stage $_) -PathType Leaf) })
if ($missingAfterArchive.Count -gt 0) { throw "Staged files disappeared during archiving:`n  $($missingAfterArchive -join "`n  ")" }

Write-Host "Model pack ready: $archive" -ForegroundColor Green
Write-Host "SHA256: $((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant())"
$buildLock.Dispose()
Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
