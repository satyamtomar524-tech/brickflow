$ErrorActionPreference = 'Stop'
$brickflowRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$brickflowPython = Join-Path $brickflowRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $brickflowPython)) {
    $brickflowPython = (Get-Command python -ErrorAction Stop).Source
}
& $brickflowPython -B (Join-Path $PSScriptRoot 'server.py') --port 8770
