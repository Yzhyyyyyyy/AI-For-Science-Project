$ErrorActionPreference = "SilentlyContinue"

$candidates = [System.Collections.Generic.List[string]]::new()

function Add-PythonCandidate([string]$path) {
    if ([string]::IsNullOrWhiteSpace($path)) { return }
    $expanded = [Environment]::ExpandEnvironmentVariables($path.Trim('"'))
    if (Test-Path -LiteralPath $expanded -PathType Leaf) {
        $candidates.Add((Resolve-Path -LiteralPath $expanded).Path)
    }
}

# Future portable releases may place a complete Python runtime here.
Add-PythonCandidate (Join-Path $PSScriptRoot "runtime\python\python.exe")

# Python Launcher can find installations that are not on PATH.
$launcher = Get-Command py.exe -ErrorAction SilentlyContinue
if ($launcher) {
    $launcherResult = & $launcher.Source -3 -c "import sys; print(sys.executable)" 2>$null
    if ($LASTEXITCODE -eq 0) { Add-PythonCandidate ($launcherResult | Select-Object -Last 1) }
}

foreach ($commandName in @("python.exe", "python3.exe")) {
    $command = Get-Command $commandName -ErrorAction SilentlyContinue
    if ($command) { Add-PythonCandidate $command.Source }
}

# Standard per-user and machine-wide installer locations.
foreach ($root in @(
    (Join-Path $env:LOCALAPPDATA "Programs\Python"),
    $env:ProgramFiles,
    ${env:ProgramFiles(x86)}
)) {
    if (-not $root -or -not (Test-Path -LiteralPath $root)) { continue }
    Get-ChildItem -LiteralPath $root -Directory -Filter "Python3*" -ErrorAction SilentlyContinue |
        ForEach-Object { Add-PythonCandidate (Join-Path $_.FullName "python.exe") }
}

# PEP 514 registry entries used by python.org and other distributors.
foreach ($registryRoot in @(
    "HKCU:\Software\Python\PythonCore",
    "HKLM:\Software\Python\PythonCore",
    "HKLM:\Software\WOW6432Node\Python\PythonCore"
)) {
    if (-not (Test-Path $registryRoot)) { continue }
    Get-ChildItem $registryRoot -ErrorAction SilentlyContinue | ForEach-Object {
        $installPath = (Get-ItemProperty -LiteralPath (Join-Path $_.PSPath "InstallPath") -ErrorAction SilentlyContinue).'(default)'
        if ($installPath) { Add-PythonCandidate (Join-Path $installPath "python.exe") }
    }
}

foreach ($candidate in $candidates | Select-Object -Unique) {
    $probe = & $candidate -c "import sys; print(sys.executable); raise SystemExit(0 if sys.version_info >= (3,10) else 1)" 2>$null
    if ($LASTEXITCODE -eq 0) {
        $resolved = $probe | Select-Object -Last 1
        if ($resolved -and (Test-Path -LiteralPath $resolved -PathType Leaf)) {
            Write-Output (Resolve-Path -LiteralPath $resolved).Path
            exit 0
        }
    }
}

exit 1
