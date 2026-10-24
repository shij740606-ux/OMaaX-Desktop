param(
    [Parameter(Mandatory=$true)][string]$InstallDir,
    [string]$Manifest
)
$ErrorActionPreference = 'Stop'
$checkedDirectories = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
$missingDirectories = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)

function Get-ExistingAttributes([string]$Path) {
    try { return [IO.File]::GetAttributes($Path) }
    catch [IO.FileNotFoundException] { return $null }
    catch [IO.DirectoryNotFoundException] { return $null }
}

function Assert-PlainDirectory([string]$Path) {
    if ($checkedDirectories.Contains($Path)) { return }
    $parent = [IO.Path]::GetDirectoryName($Path)
    if ($parent) { Assert-PlainDirectory $parent }
    if ($parent -and $missingDirectories.Contains($parent)) {
        [void]$missingDirectories.Add($Path)
    } else {
        $attributes = Get-ExistingAttributes $Path
        if ($null -eq $attributes) { [void]$missingDirectories.Add($Path) }
        elseif (-not ($attributes -band [IO.FileAttributes]::Directory) -or ($attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Installation directory must not contain a link or a file: $Path"
        }
    }
    [void]$checkedDirectories.Add($Path)
}

function Assert-PayloadTarget([string]$Relative) {
    if ([string]::IsNullOrWhiteSpace($Relative) -or [IO.Path]::IsPathRooted($Relative)) {
        throw "Payload path must be relative: $Relative"
    }
    foreach ($part in $Relative.Replace('/', '\').Split('\')) {
        if (-not $part -or $part -eq '.' -or $part -eq '..' -or $part.EndsWith('.') -or $part.EndsWith(' ') -or
            $part.IndexOfAny([IO.Path]::GetInvalidFileNameChars()) -ge 0 -or $part -match '^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)') {
            throw "Unsafe payload path: $Relative"
        }
    }
    $full = [IO.Path]::GetFullPath((Join-Path $target $Relative))
    if (-not $full.StartsWith($target + '\', [StringComparison]::OrdinalIgnoreCase)) { throw "Payload path escapes installation directory: $Relative" }
    $parent = [IO.Path]::GetDirectoryName($full)
    Assert-PlainDirectory $parent
    if ($missingDirectories.Contains($parent)) { return }
    $attributes = Get-ExistingAttributes $full
    if ($null -ne $attributes -and ($attributes -band ([IO.FileAttributes]::Directory -bor [IO.FileAttributes]::ReparsePoint))) {
        throw "Installation file must not be a directory or link: $full"
    }
}

if (-not [IO.Path]::IsPathRooted($InstallDir)) { throw 'Installation directory must be an absolute path.' }
$target = [IO.Path]::GetFullPath($InstallDir).TrimEnd('\', '/')
if ($target -eq [IO.Path]::GetPathRoot($target).TrimEnd('\', '/')) {
    throw 'Installation directory must not be a drive or share root.'
}
Assert-PlainDirectory $target

# Validate every destination before stopping processes or allowing any installer writes.
Assert-PayloadTarget 'installation\verified.json'
if ($Manifest) {
    $document = [IO.File]::ReadAllText([IO.Path]::GetFullPath($Manifest)) | ConvertFrom-Json
    if (-not ($document.PSObject.Properties.Name -contains 'files') -or @($document.files).Count -eq 0) {
        throw 'Installation manifest has no payload files.'
    }
    foreach ($entry in $document.files) {
        if (-not ($entry.path -is [string])) { throw 'Installation manifest has an invalid payload path.' }
        Assert-PayloadTarget $entry.path
    }
    Assert-PayloadTarget 'installation\payload-manifest.json'
}

# Stop this installation only. Do not terminate unrelated applications spawned by an analysis.
$executables = @((Join-Path $target 'OMaaX.exe'), (Join-Path $target 'node\node.exe'))
$deadline = [DateTime]::UtcNow.AddSeconds(15)
do {
    $running = @(Get-CimInstance Win32_Process -Filter "Name = 'OMaaX.exe' OR Name = 'node.exe'" | Where-Object {
        $_.ExecutablePath -and $executables -contains $_.ExecutablePath
    })
    if (-not $running.Count) { break }
    foreach ($process in $running) {
        $live = Get-CimInstance Win32_Process -Filter "ProcessId = $($process.ProcessId)"
        if ($live -and $live.CreationDate -eq $process.CreationDate -and $live.ExecutablePath -eq $process.ExecutablePath) {
            try { Stop-Process -Id $process.ProcessId -Force -ErrorAction Stop }
            catch {
                if (Get-Process -Id $process.ProcessId -ErrorAction SilentlyContinue) {
                    throw "Could not close this installation's process: $($process.ExecutablePath) (PID $($process.ProcessId)). Close OMaaX and retry."
                }
            }
        }
    }
    if ([DateTime]::UtcNow -ge $deadline) { throw "OMaaX processes have not exited in $target. Close OMaaX and retry." }
    Start-Sleep -Milliseconds 200
} while ($true)

# Test replacement access without changing bytes; a lock must fail before extraction starts.
$coreFiles = @(
    'OMaaX.exe', 'node\node.exe',
    'dsh\node_modules\@deepseek-ai\dsh\lib\bin.js',
    'dsh\node_modules\@deepseek-ai\dsh\node_modules\@deepseek-ai\dsh-agent-loop\lib\index.js',
    'ssh\client.cjs', 'workflows\kernel.zip'
)
foreach ($relative in $coreFiles) {
    $file = Join-Path $target $relative
    if (-not (Test-Path -LiteralPath $file)) { continue }
    Assert-PlainDirectory ([IO.Path]::GetDirectoryName($file))
    $item = Get-Item -LiteralPath $file -Force
    if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "Installation file must not be a directory or link: $file"
    }
    try {
        $handle = [IO.File]::Open($file, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
        $handle.Dispose()
    } catch {
        throw "Cannot replace installation file: $file. It is locked or not writable. Close OMaaX and retry."
    }
}
