param([Parameter(Mandatory=$true)][string]$InstallDir)
$ErrorActionPreference = 'Stop'
# Upgrade older versions which did not tie the agent lifetime to the desktop.
# Only this install's bundled DSH web process is eligible; never other Node apps.
$nodePath = [IO.Path]::GetFullPath((Join-Path $InstallDir 'node\node.exe'))
$agents = Get-CimInstance Win32_Process -Filter "Name = 'node.exe'" | Where-Object {
    $_.ExecutablePath -and $_.ExecutablePath.Equals($nodePath, [StringComparison]::OrdinalIgnoreCase) -and
    $_.CommandLine -match 'dsh[\\/]lib[\\/]bin\.js"?\s+web(?:\s|$)'
}
foreach ($agent in $agents) {
    $live = Get-CimInstance Win32_Process -Filter "ProcessId = $($agent.ProcessId)"
    if ($live -and $live.CreationDate -eq $agent.CreationDate -and $live.ExecutablePath -eq $agent.ExecutablePath) {
        & "$env:SystemRoot\System32\taskkill.exe" /PID $agent.ProcessId /T /F | Out-Null
        if ($LASTEXITCODE -ne 0 -and (Get-Process -Id $agent.ProcessId -ErrorAction SilentlyContinue)) {
            throw 'Could not stop the previous OMaaX agent. Close OMaaX and retry the update.'
        }
    }
}
