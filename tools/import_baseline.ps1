param(
    [Parameter(Mandatory=$true)][string]$Archive,
    [Parameter(Mandatory=$true)][string]$Destination
)
$ErrorActionPreference = 'Stop'
$expectedHash = 'D3BEF0AFEA92B99626526FFCCE0508414FEB3F9E88C3EDDA1F283CE5F447BF53'
if ((Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash -ne $expectedHash) {
    throw 'Archive identity mismatch; refusing import.'
}
$root = [IO.Path]::GetFullPath($Destination).TrimEnd('\','/') + [IO.Path]::DirectorySeparatorChar
if (-not (Test-Path -LiteralPath (Join-Path $root '.git'))) { throw 'Destination must be an initialized derived repository.' }
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [IO.Compression.ZipFile]::OpenRead((Resolve-Path -LiteralPath $Archive))
try {
    $entries = @($zip.Entries | Where-Object { $_.Name })
    if ($entries.Count -ne 128) { throw 'Expected exactly 128 source entries.' }
    $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $manifest = @()
    $operations = @()
    foreach ($entry in $entries) {
        if (-not $entry.FullName.StartsWith('MiroFish-main/')) { throw 'Unexpected archive root.' }
        $relative = $entry.FullName.Substring('MiroFish-main/'.Length)
        if ($relative -match '(^|/)(\.|\.\.|\.git|node_modules|\.venv)(/|$)' -or $relative.Contains(':') -or $relative.Contains('\')) { throw "Unsafe entry: $relative" }
        # Quarantine inherited workflows as inert reference material before first push.
        # Keep inherited README/ignore files intact as references, not active new-product claims.
        $mapped = $relative
        if ($relative.StartsWith('.github/workflows/')) { $mapped = 'docs/upstream/workflows/' + [IO.Path]::GetFileName($relative) + '.reference' }
        if ($relative -in @('README.md','README-ZH.md','.gitignore')) { $mapped = 'docs/upstream/' + $relative.TrimStart('.') + '.reference' }
        $target = [IO.Path]::GetFullPath((Join-Path $root $mapped))
        if (-not $target.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) { throw 'Path escapes destination.' }
        if (-not $seen.Add($target)) { throw 'Duplicate destination.' }
        if (Test-Path -LiteralPath $target) { throw "Destination already exists: $mapped" }
        if ($entry.Length -gt 100MB) { throw 'Oversized archive entry.' }
        $operations += [pscustomobject]@{ Entry=$entry; Target=$target; Original=$relative; Imported=$mapped }
    }
    foreach ($op in $operations) {
        [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($op.Target)) | Out-Null
        [IO.Compression.ZipFileExtensions]::ExtractToFile($op.Entry, $op.Target, $false)
        $manifest += [pscustomobject]@{ path=$op.Original; imported_path=$op.Imported; size=$op.Entry.Length; sha256=(Get-FileHash -LiteralPath $op.Target -Algorithm SHA256).Hash.ToLowerInvariant() }
    }
    $result = [ordered]@{
        archive='MiroFish-main.zip'; archive_sha256=$expectedHash.ToLowerInvariant(); file_count=$entries.Count
        upstream_repository='https://github.com/666ghj/MiroFish'; archive_commit=$null
        observed_upstream_head='39d849138ef254f6c737ab4c4705e5545dbe31d4'; observed_date='2026-09-26'
        note='Snapshot commit mapping unknown. Current upstream is a separate reference, not asserted to match the ZIP. All 128 entries retained; workflows and README/ignore originals relocated as inert references.'
        files=$manifest
    }
    $manifestPath = Join-Path $root 'docs/upstream/archive-manifest.json'
    [IO.File]::WriteAllText($manifestPath, ($result | ConvertTo-Json -Depth 6), [Text.UTF8Encoding]::new($false))
    Write-Output "Imported and hashed $($entries.Count) source files. No source code executed."
} finally { $zip.Dispose() }
