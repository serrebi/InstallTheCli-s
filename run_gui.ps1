param()

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$guiScript = Join-Path $scriptDir "ai_cli_installer_gui.py"

if (-not (Test-Path -LiteralPath $guiScript)) {
    Write-Error "GUI script not found: $guiScript"
    exit 1
}

& py -3.14 $guiScript
exit $LASTEXITCODE
