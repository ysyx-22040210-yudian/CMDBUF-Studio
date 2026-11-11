param([Parameter(ValueFromRemainingArguments=$true)][string[]]$MakeArguments)
$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$candidates = @()
if ($env:CMDBUF_PYTHON) { $candidates += [pscustomobject]@{File=$env:CMDBUF_PYTHON;Prefix=@()} }
$localPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $localPython) { $candidates += [pscustomobject]@{File=$localPython;Prefix=@()} }
foreach ($name in @('python','python3','py')) {
    $found = Get-Command $name -ErrorAction SilentlyContinue
    if ($found) {
        if ($name -eq 'py') { $candidates += [pscustomobject]@{File=$found.Source;Prefix=@('-3')} }
        else { $candidates += [pscustomobject]@{File=$found.Source;Prefix=@()} }
    }
}
$selected = $null
foreach ($candidate in $candidates) {
    try {
        $exe = $candidate.File
        $prefix = $candidate.Prefix
        & $exe @prefix -c 'import sys; sys.exit(0 if sys.version_info >= (3,6) else 1)' 2>$null
        if ($LASTEXITCODE -eq 0) { $selected = $candidate; break }
    } catch {}
}
if (-not $selected) {
    Write-Host 'Python 3.6+ is required. Install Python 3, or set CMDBUF_PYTHON to its python.exe path.'
    exit 2
}
$exe = $selected.File
$prefix = $selected.Prefix
$env:PYTHONIOENCODING = 'utf-8'
& $exe @prefix (Join-Path $PSScriptRoot 'launcher.py') @MakeArguments
exit $LASTEXITCODE
