param(
    [Parameter(Mandatory = $true)][string]$InstallDir,
    [Parameter(Mandatory = $true)][string]$Manifest,
    [string]$Receipt,
    [string]$ExpectedVersion
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2

# Check the opened handle as well as the requested path: Windows app virtualization
# can otherwise make an installer read back its own redirected copy successfully.
try {
    Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Text;
using System.Security.Cryptography;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

public static class OMaaXPayloadCheck {
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern SafeFileHandle CreateFile(string name, uint access, uint share,
        IntPtr security, uint disposition, uint flags, IntPtr template);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern uint GetFinalPathNameByHandle(SafeFileHandle file, StringBuilder path,
        uint length, uint flags);
    static readonly HashSet<string> checkedDirectories = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

    public static string Normalize(string path) {
        if (path.StartsWith(@"\\?\UNC\", StringComparison.OrdinalIgnoreCase)) path = @"\\" + path.Substring(8);
        else if (path.StartsWith(@"\\?\", StringComparison.OrdinalIgnoreCase)) path = path.Substring(4);
        return Path.GetFullPath(path).TrimEnd(Path.DirectorySeparatorChar);
    }
    static string FinalPath(SafeFileHandle handle) {
        StringBuilder text = new StringBuilder(1024);
        uint size = GetFinalPathNameByHandle(handle, text, (uint)text.Capacity, 0);
        if (size == 0) throw new IOException("Cannot resolve actual file location: " + Marshal.GetLastWin32Error());
        if (size >= text.Capacity) {
            text = new StringBuilder(checked((int)size + 1));
            size = GetFinalPathNameByHandle(handle, text, (uint)text.Capacity, 0);
            if (size == 0 || size >= text.Capacity) throw new IOException("Cannot resolve actual file location");
        }
        return Normalize(text.ToString());
    }
    static void RequireActualPath(SafeFileHandle handle, string expected) {
        string actual = FinalPath(handle);
        if (!String.Equals(actual, Normalize(expected), StringComparison.OrdinalIgnoreCase))
            throw new IOException("Installation path redirected. Expected: " + expected + "; actual: " + actual);
    }
    public static void NoReparse(string path) {
        string current = Path.GetFullPath(path);
        while (!String.IsNullOrEmpty(current)) {
            if (checkedDirectories.Contains(current)) return;
            FileAttributes attributes = File.GetAttributes(current);
            if ((attributes & FileAttributes.ReparsePoint) != 0)
                throw new IOException("Reparse point is not an installation directory/file: " + current);
            if ((attributes & FileAttributes.Directory) != 0) checkedDirectories.Add(current);
            current = Path.GetDirectoryName(current);
        }
    }
    public static string Root(string path) {
        if (!Path.IsPathRooted(path)) throw new IOException("InstallDir must be an absolute path");
        string full = Path.GetFullPath(path);
        NoReparse(full);
        if (!Directory.Exists(full)) throw new IOException("Installation directory is missing");
        using (SafeFileHandle handle = CreateFile(full, 0, 7, IntPtr.Zero, 3, 0x02000000, IntPtr.Zero)) {
            if (handle.IsInvalid) throw new IOException("Cannot open installation directory: " + Marshal.GetLastWin32Error());
            RequireActualPath(handle, full);
            return FinalPath(handle);
        }
    }
    public static string Child(string root, string relative) {
        if (String.IsNullOrWhiteSpace(relative) || Path.IsPathRooted(relative))
            throw new IOException("Payload path must be relative: " + relative);
        string[] parts = relative.Replace('/', '\\').Split('\\');
        foreach (string part in parts) {
            if (part.Length == 0 || part == "." || part == ".." || part.EndsWith(".") || part.EndsWith(" ") ||
                part.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0 ||
                System.Text.RegularExpressions.Regex.IsMatch(part, @"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", System.Text.RegularExpressions.RegexOptions.IgnoreCase))
                throw new IOException("Unsafe payload path: " + relative);
        }
        string full = Path.GetFullPath(Path.Combine(root, String.Join("\\", parts)));
        if (!full.StartsWith(Normalize(root) + "\\", StringComparison.OrdinalIgnoreCase))
            throw new IOException("Payload path escapes installation directory: " + relative);
        return full;
    }
    public static void FileMatches(string full, long bytes, string sha256) {
        NoReparse(full);
        using (FileStream stream = new FileStream(full, FileMode.Open, FileAccess.Read, FileShare.Read, 131072, FileOptions.SequentialScan)) {
            RequireActualPath(stream.SafeFileHandle, full);
            if (stream.Length != bytes) throw new IOException("Size mismatch: " + full);
            using (SHA256 hash = SHA256.Create()) {
                string actual = BitConverter.ToString(hash.ComputeHash(stream)).Replace("-", "");
                if (!actual.Equals(sha256, StringComparison.OrdinalIgnoreCase)) throw new IOException("SHA256 mismatch: " + full);
            }
        }
    }
    public static void ClearReceipt(string path) {
        NoReparse(Path.GetDirectoryName(path));
        if (File.Exists(path)) {
            NoReparse(path);
            using (FileStream stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read))
                RequireActualPath(stream.SafeFileHandle, path);
            File.Delete(path);
        }
    }
    public static void WriteReceipt(string path, string json) {
        NoReparse(Path.GetDirectoryName(path));
        string temporary = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
        try {
            using (FileStream stream = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None)) {
                RequireActualPath(stream.SafeFileHandle, temporary);
                byte[] bytes = new UTF8Encoding(false).GetBytes(json + "\n");
                stream.Write(bytes, 0, bytes.Length);
                stream.Flush(true);
            }
            File.Move(temporary, path);
        } finally {
            if (File.Exists(temporary)) File.Delete(temporary);
        }
    }
}
'@

    $requestedRoot = [IO.Path]::GetFullPath($InstallDir)
    $actualRoot = [OMaaXPayloadCheck]::Root($InstallDir)
    $document = [IO.File]::ReadAllText([IO.Path]::GetFullPath($Manifest)) | ConvertFrom-Json
    if (-not ($document.PSObject.Properties.Name -contains 'version') -or
        -not ($document.version -is [string]) -or [string]::IsNullOrWhiteSpace($document.version)) {
        throw 'Manifest version is missing'
    }
    if ($ExpectedVersion -and $document.version -ne $ExpectedVersion) { throw 'Manifest version does not match installer version' }
    if (-not ($document.PSObject.Properties.Name -contains 'files') -or @($document.files).Count -eq 0) {
        throw 'Manifest has no payload files'
    }
    if ([string]::IsNullOrWhiteSpace($Receipt)) { $Receipt = 'install-verification.json' }
    if ([IO.Path]::IsPathRooted($Receipt)) {
        $receiptFull = [IO.Path]::GetFullPath($Receipt)
        $prefix = $actualRoot + '\'
        if (-not $receiptFull.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'Receipt must be inside InstallDir' }
        $receiptFull = [OMaaXPayloadCheck]::Child($actualRoot, $receiptFull.Substring($prefix.Length))
    } else { $receiptFull = [OMaaXPayloadCheck]::Child($actualRoot, $Receipt) }
    if ([String]::Equals($receiptFull, [IO.Path]::GetFullPath($Manifest), [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Receipt cannot replace the manifest'
    }

    $seen = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    $entries = New-Object 'System.Collections.Generic.List[object]'
    foreach ($entry in $document.files) {
        if (-not ($entry.path -is [string]) -or -not ($entry.sha256 -is [string]) -or $entry.sha256 -notmatch '^[0-9a-fA-F]{64}$') {
            throw 'Manifest entry has an invalid path or SHA256'
        }
        $full = [OMaaXPayloadCheck]::Child($actualRoot, $entry.path)
        if (-not $seen.Add($full)) { throw ('Duplicate payload path: ' + $entry.path) }
        if ([String]::Equals($receiptFull, $full, [StringComparison]::OrdinalIgnoreCase)) { throw 'Receipt cannot replace a payload file' }
        [long]$bytes = 0
        if (-not [long]::TryParse([string]$entry.bytes, [ref]$bytes) -or $bytes -lt 0) { throw ('Invalid payload size: ' + $entry.path) }
        $entries.Add(@{ full = $full; bytes = $bytes; sha256 = $entry.sha256 })
    }
    [OMaaXPayloadCheck]::ClearReceipt($receiptFull)
    foreach ($entry in $entries) { [OMaaXPayloadCheck]::FileMatches($entry.full, $entry.bytes, $entry.sha256) }
    $receiptJson = [ordered]@{
        version = $document.version
        count = $entries.Count
        verifiedAt = [DateTime]::UtcNow.ToString('o')
        installDir = $requestedRoot
        actualInstallDir = $actualRoot
    } | ConvertTo-Json -Depth 4
    [OMaaXPayloadCheck]::WriteReceipt($receiptFull, $receiptJson)
    Write-Output ('Verified OMaaX ' + $document.version + ': ' + $entries.Count + ' payload files')
    exit 0
} catch {
    [Console]::Error.WriteLine('Installation verification failed: ' + $_.Exception.GetBaseException().Message)
    exit 1
}
