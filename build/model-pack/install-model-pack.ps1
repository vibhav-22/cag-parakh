<# Administrator utility for installing a verified offline model-pack archive. #>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$PackArchive,
    [ValidateSet("AllUsers", "CurrentUser")][string]$Scope = "AllUsers",
    [switch]$Force
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $PackArchive -PathType Leaf)) {
    throw "Model-pack archive not found: $PackArchive"
}
if ($Scope -eq "AllUsers") {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "AllUsers installation requires an elevated Administrator PowerShell window."
    }
}

$scratch = Join-Path ([IO.Path]::GetTempPath()) "parakh-model-pack-install-$([guid]::NewGuid())"
try {
    New-Item -ItemType Directory -Force -Path $scratch | Out-Null
    Expand-Archive -LiteralPath $PackArchive -DestinationPath $scratch -Force
    $manifestFile = Get-ChildItem -LiteralPath $scratch -Filter "model-pack.json" -File -Recurse | Select-Object -First 1
    if (-not $manifestFile) { throw "The archive contains no model-pack.json." }
    $source = $manifestFile.Directory.FullName
    $manifest = Get-Content -LiteralPath $manifestFile.FullName -Raw | ConvertFrom-Json
    if ($manifest.schema_version -ne 1) { throw "Unsupported model-pack schema." }
    if ($manifest.pack_id -ne "parakh-qwen3-vl-4b-instruct-q4-k-m") { throw "Unexpected model-pack id." }
    if ($manifest.pack_version -ne "1.0.0") { throw "Unsupported model-pack version." }
    if (-not $manifest.files -or $manifest.files.PSObject.Properties.Count -eq 0) {
        throw "The model-pack manifest contains no file hashes."
    }

    $resolvedSource = [IO.Path]::GetFullPath($source).TrimEnd("\") + "\"
    foreach ($property in $manifest.files.PSObject.Properties) {
        $relative = $property.Name.Replace("/", "\")
        $candidate = [IO.Path]::GetFullPath((Join-Path $source $relative))
        if (-not $candidate.StartsWith($resolvedSource, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Unsafe path in model-pack manifest: $($property.Name)"
        }
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { throw "Missing model-pack file: $($property.Name)" }
        $actual = (Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne ([string]$property.Value).ToLowerInvariant()) {
            throw "Model-pack hash mismatch: $($property.Name)"
        }
    }
    foreach ($required in @($manifest.model.file, $manifest.model.mmproj_file)) {
        if (-not $required -or -not (Test-Path -LiteralPath (Join-Path $source ([string]$required).Replace("/", "\")) -PathType Leaf)) {
            throw "A required GGUF file is missing from the model pack: $required"
        }
    }

    $base = if ($Scope -eq "AllUsers") {
        Join-Path $env:PROGRAMDATA "Parakh\ModelPacks"
    } else {
        Join-Path $env:LOCALAPPDATA "Parakh\ModelPacks"
    }
    if (-not $base) { throw "The Windows application-data directory could not be resolved." }
    $packRoot = Join-Path $base $manifest.pack_id
    $target = Join-Path $packRoot $manifest.pack_version
    New-Item -ItemType Directory -Force -Path $packRoot | Out-Null
    if (Test-Path -LiteralPath $target) {
        if (-not $Force) { throw "Model pack $($manifest.pack_version) is already installed. Use -Force to replace it." }
        Remove-Item -LiteralPath $target -Recurse -Force
    }
    $incoming = Join-Path $packRoot ".$($manifest.pack_version).incoming-$([guid]::NewGuid())"
    Copy-Item -LiteralPath $source -Destination $incoming -Recurse
    Move-Item -LiteralPath $incoming -Destination $target
    Write-Host "Installed Parakh model pack $($manifest.pack_version) to $target" -ForegroundColor Green
}
finally {
    if (Test-Path -LiteralPath $scratch) { Remove-Item -LiteralPath $scratch -Recurse -Force }
}
