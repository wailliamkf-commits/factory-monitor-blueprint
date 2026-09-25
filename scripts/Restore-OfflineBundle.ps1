[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $ManifestPath,

    [Parameter(Mandatory = $true)]
    [string] $OutputFile
)

$ErrorActionPreference = 'Stop'
$BufferBytes = 1048576
$DigestPattern = '^[0-9a-f]{64}$'
$PartPattern = '^part-(\d{6})-of-(\d{6})\.bin$'

function Stop-Bundle([string] $Message) {
    throw "Restore-OfflineBundle: $Message"
}

function Assert-RegularFile([string] $Path, [string] $Label) {
    if (-not [System.IO.File]::Exists($Path)) {
        Stop-Bundle "$Label does not exist: $Path"
    }
    $attributes = [System.IO.File]::GetAttributes($Path)
    if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        Stop-Bundle "$Label must not be a symbolic link/reparse point: $Path"
    }
    if (($attributes -band [System.IO.FileAttributes]::Directory) -ne 0) {
        Stop-Bundle "$Label must be a regular file: $Path"
    }
}

function Assert-SafeBasename([object] $Name, [string] $Label) {
    if ($Name -isnot [string] -or [string]::IsNullOrWhiteSpace($Name) -or $Name -eq '.' -or $Name -eq '..') {
        Stop-Bundle "$Label must be a non-empty safe basename"
    }
    if ($Name.Contains('/') -or $Name.Contains('\') -or $Name.Contains([char]0)) {
        Stop-Bundle "$Label must not contain a path separator"
    }
    if ([System.IO.Path]::GetFileName($Name) -cne $Name) {
        Stop-Bundle "$Label must not contain a path"
    }
}

function Get-HexDigest([System.Security.Cryptography.HashAlgorithm] $Hash) {
    $bytes = $Hash.Hash
    return ([System.BitConverter]::ToString($bytes)).Replace('-', '').ToLowerInvariant()
}

function Copy-PartAndHash(
    [string] $Path,
    [System.IO.Stream] $Output,
    [System.Security.Cryptography.HashAlgorithm] $PartHash,
    [System.Security.Cryptography.HashAlgorithm] $TotalHash
) {
    Assert-RegularFile $Path 'part'
    $sourceStream = $null
    $size = [long]0
    try {
        $sourceStream = New-Object System.IO.FileStream(
            $Path,
            [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::Read,
            [System.IO.FileShare]::Read
        )
        $buffer = New-Object byte[] $BufferBytes
        while (($read = $sourceStream.Read($buffer, 0, $buffer.Length)) -gt 0) {
            $Output.Write($buffer, 0, $read)
            [void]$PartHash.TransformBlock($buffer, 0, $read, $buffer, 0)
            [void]$TotalHash.TransformBlock($buffer, 0, $read, $buffer, 0)
            $size += $read
        }
        [void]$PartHash.TransformFinalBlock((New-Object byte[] 0), 0, 0)
        return $size
    }
    finally {
        if ($null -ne $sourceStream) { $sourceStream.Dispose() }
    }
}

$temporaryPath = $null
$wholeHash = $null
try {
    $manifestFullPath = [System.IO.Path]::GetFullPath($ManifestPath)
    Assert-RegularFile $manifestFullPath 'manifest'
    $manifest = [System.IO.File]::ReadAllText($manifestFullPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
    $manifestKeys = @($manifest.PSObject.Properties.Name | Sort-Object)
    $expectedManifestKeys = @('original_filename', 'parts', 'schema_version', 'total_sha256', 'total_size') | Sort-Object
    if (($manifestKeys -join '|') -cne ($expectedManifestKeys -join '|')) {
        Stop-Bundle 'manifest must contain exactly schema_version, original_filename, total_size, total_sha256, parts'
    }
    if (($manifest.schema_version -isnot [int] -and $manifest.schema_version -isnot [long]) -or $manifest.schema_version -ne 1) {
        Stop-Bundle 'unsupported manifest schema_version'
    }
    Assert-SafeBasename $manifest.original_filename 'original_filename'
    if ($manifest.total_size -isnot [long] -and $manifest.total_size -isnot [int]) {
        Stop-Bundle 'total_size must be a non-negative integer'
    }
    if ([long]$manifest.total_size -lt 0) { Stop-Bundle 'total_size must be a non-negative integer' }
    if ($manifest.total_sha256 -isnot [string] -or $manifest.total_sha256 -cnotmatch $DigestPattern) {
        Stop-Bundle 'total_sha256 must be 64 lowercase hexadecimal characters'
    }

    $parts = @($manifest.parts)
    if ($parts.Count -lt 1) { Stop-Bundle 'parts must be a non-empty array' }
    $seenNames = @{}
    $advertisedTotal = $null
    $orderedParts = @(
        foreach ($part in $parts) {
            $partKeys = @($part.PSObject.Properties.Name | Sort-Object)
            $expectedPartKeys = @('name', 'sha256', 'size') | Sort-Object
            if (($partKeys -join '|') -cne ($expectedPartKeys -join '|')) {
                Stop-Bundle 'each part must contain exactly name, size, sha256'
            }
            Assert-SafeBasename $part.name 'part name'
            if ($part.name -cnotmatch $PartPattern) { Stop-Bundle "invalid part name: $($part.name)" }
            if ($seenNames.ContainsKey($part.name)) { Stop-Bundle "duplicate part name: $($part.name)" }
            $seenNames[$part.name] = $true
            $sequence = [int]$Matches[1]
            $partTotal = [int]$Matches[2]
            if ($sequence -lt 1 -or $partTotal -lt 1 -or $sequence -gt $partTotal) {
                Stop-Bundle "invalid part sequence: $($part.name)"
            }
            if ($null -eq $advertisedTotal) { $advertisedTotal = $partTotal }
            elseif ($partTotal -ne $advertisedTotal) { Stop-Bundle 'part names disagree on total part count' }
            if ($part.size -isnot [long] -and $part.size -isnot [int]) { Stop-Bundle "invalid size: $($part.name)" }
            if ([long]$part.size -lt 0) { Stop-Bundle "invalid size: $($part.name)" }
            if ($part.sha256 -isnot [string] -or $part.sha256 -cnotmatch $DigestPattern) {
                Stop-Bundle "invalid sha256: $($part.name)"
            }
            [pscustomobject]@{ Sequence = $sequence; Total = $partTotal; Name = $part.name; Size = [long]$part.size; Sha256 = $part.sha256 }
        }
    ) | Sort-Object Sequence
    if ($orderedParts.Count -ne $advertisedTotal) { Stop-Bundle 'part sequence is not contiguous' }
    for ($index = 0; $index -lt $orderedParts.Count; $index++) {
        $expectedName = 'part-{0:D6}-of-{1:D6}.bin' -f ($index + 1), $advertisedTotal
        if ($orderedParts[$index].Sequence -ne ($index + 1) -or $orderedParts[$index].Name -cne $expectedName) {
            Stop-Bundle 'part sequence must be contiguous from 1 through the advertised total'
        }
    }
    $sum = [long]0
    foreach ($part in $orderedParts) { $sum += $part.Size }
    if ($sum -ne [long]$manifest.total_size) { Stop-Bundle 'part sizes do not match total_size' }
    if ([long]$manifest.total_size -eq 0) {
        if ($orderedParts.Count -ne 1 -or $orderedParts[0].Size -ne 0) { Stop-Bundle 'empty input must have one empty part' }
    }
    else {
        if (@($orderedParts | Where-Object { $_.Size -le 0 }).Count -gt 0) { Stop-Bundle 'non-empty input contains an empty part' }
        if ($orderedParts.Count -gt 1) {
            for ($index = 0; $index -lt ($orderedParts.Count - 1); $index++) {
                if ($orderedParts[$index].Size -ne $orderedParts[0].Size) { Stop-Bundle 'non-final part sizes differ' }
            }
            if ($orderedParts[-1].Size -gt $orderedParts[0].Size) { Stop-Bundle 'final part is larger than a non-final part' }
        }
    }

    $destination = [System.IO.Path]::GetFullPath($OutputFile)
    $parentDirectory = [System.IO.Path]::GetDirectoryName($destination)
    if (-not [System.IO.Directory]::Exists($parentDirectory)) { Stop-Bundle "output parent directory does not exist: $parentDirectory" }
    if ([System.IO.File]::Exists($destination) -or [System.IO.Directory]::Exists($destination)) {
        Stop-Bundle "refusing to overwrite output: $destination"
    }
    $temporaryPath = Join-Path $parentDirectory ('.' + [System.IO.Path]::GetFileName($destination) + '.' + [guid]::NewGuid().ToString('N') + '.partial')
    $output = New-Object System.IO.FileStream(
        $temporaryPath,
        [System.IO.FileMode]::CreateNew,
        [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::None
    )
    $wholeHash = [System.Security.Cryptography.SHA256]::Create()
    $totalWritten = [long]0
    try {
        foreach ($part in $orderedParts) {
            $partPath = Join-Path ([System.IO.Path]::GetDirectoryName($manifestFullPath)) $part.Name
            $partHash = [System.Security.Cryptography.SHA256]::Create()
            try {
                $partSize = Copy-PartAndHash $partPath $output $partHash $wholeHash
                if ($partSize -ne $part.Size) { Stop-Bundle "size mismatch for $($part.Name)" }
                if ((Get-HexDigest $partHash) -cne $part.Sha256) { Stop-Bundle "SHA-256 mismatch for $($part.Name)" }
                $totalWritten += $partSize
            }
            finally { $partHash.Dispose() }
        }
        [void]$wholeHash.TransformFinalBlock((New-Object byte[] 0), 0, 0)
        $output.Flush($true)
    }
    finally { $output.Dispose() }
    if ($totalWritten -ne [long]$manifest.total_size) { Stop-Bundle 'total size mismatch' }
    if ((Get-HexDigest $wholeHash) -cne $manifest.total_sha256) { Stop-Bundle 'total SHA-256 mismatch' }
    $wholeHash.Dispose()
    $wholeHash = $null
    if ([System.IO.File]::Exists($destination) -or [System.IO.Directory]::Exists($destination)) {
        Stop-Bundle "refusing to overwrite output: $destination"
    }
    [System.IO.File]::Move($temporaryPath, $destination)
    $temporaryPath = $null
    [pscustomobject]@{
        status = 'restored'
        output = $destination
        total_size = $totalWritten
        total_sha256 = $manifest.total_sha256
        part_count = $orderedParts.Count
    } | ConvertTo-Json -Compress
}
catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
finally {
    if ($null -ne $wholeHash) { $wholeHash.Dispose() }
    if ($null -ne $temporaryPath -and [System.IO.File]::Exists($temporaryPath)) {
        try { [System.IO.File]::Delete($temporaryPath) } catch { }
    }
}
