<#
.SYNOPSIS
One-click Windows installer for AI CLIs used by InstallTheCli.

.DESCRIPTION
Installs all supported AI CLIs (or one selected target) using official package sources:
- winget for Node.js, Python 3.14, and Ollama
- npm for Claude/Codex/Gemini/Grok/Qwen/Copilot
- uv/pip for Mistral Vibe

Also configures a hidden Scheduled Task (startup, logon, daily) unless disabled.

.PARAMETER Command
Subcommand: install-all (default), install, list, setup-updater, help.

.PARAMETER Target
Target for the install subcommand: claude, codex, gemini, grok, qwen, copilot, mistral, ollama, all.

.PARAMETER NoAutoUpdate
Skips creation/update of the hidden scheduled auto-update task.

.PARAMETER DryRun
Prints commands without making changes.

.PARAMETER AutoUpdateTime
Daily Scheduled Task time (default: 3:00AM).

.PARAMETER Help
Shows help.

.EXAMPLE
.\install_all_windows.ps1

.EXAMPLE
.\install_all_windows.ps1 install codex -NoAutoUpdate

.EXAMPLE
.\install_all_windows.ps1 list

.EXAMPLE
Get-Help .\install_all_windows.ps1 -Detailed
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Command = 'install-all',

    [Parameter(Position = 1)]
    [string]$Target = 'all',

    [switch]$NoAutoUpdate,
    [switch]$DryRun,

    [string]$AutoUpdateTime = '3:00AM',

    [switch]$Help
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$NodeWingetId = 'OpenJS.NodeJS.LTS'
$PythonWingetId = 'Python.Python.3.14'
$OllamaWingetId = 'Ollama.Ollama'
$AutoUpdateTaskName = 'InstallTheCli - Update AI CLIs'
$LocalAppDataRoot = if ($env:LocalAppData) { $env:LocalAppData } else { Join-Path $HOME 'AppData\Local' }
$SupportDir = Join-Path $LocalAppDataRoot 'InstallTheCli'
$AutoUpdateScriptPath = Join-Path $SupportDir 'one_click_update_windows.ps1'
$NpmFlags = @('--no-fund', '--no-audit', '--no-update-notifier', '--loglevel', 'error')
$PipFlags = @('--disable-pip-version-check', '--no-input', '--quiet')

$NpmCliSpecs = @{
    claude   = @{ Label = 'Claude CLI';  Packages = @('@anthropic-ai/claude-code') }
    codex    = @{ Label = 'Codex CLI';   Packages = @('@openai/codex') }
    gemini   = @{ Label = 'Gemini CLI';  Packages = @('@google/gemini-cli') }
    grok     = @{ Label = 'Grok CLI (Vibe Kit)'; Packages = @('@vibe-kit/grok-cli') }
    qwen     = @{ Label = 'Qwen CLI';    Packages = @('@qwen-code/qwen-code', 'qwen-code') }
    copilot  = @{ Label = 'GitHub Copilot CLI'; Packages = @('@github/copilot', '@githubnext/github-copilot-cli') }
    openclaw = @{ Label = 'OpenClaw CLI'; Packages = @('openclaw') }
    ironclaw = @{ Label = 'IronClaw CLI'; Packages = @('ironclaw') }
}

function Write-Log {
    param([string]$Message)
    Write-Host "[install] $Message"
}

function Write-WarnLog {
    param([string]$Message)
    Write-Warning $Message
}

function Throw-InstallError {
    param([string]$Message)
    throw $Message
}

function Test-CommandAvailable {
    param([string]$Name)
    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Quote-Arg {
    param([string]$Value)
    if ($Value -match '\s|"') {
        return '"' + ($Value -replace '"', '\"') + '"'
    }
    return $Value
}

function Invoke-ExternalCommand {
    param(
        [Parameter(Mandatory = $true)][string[]]$Args
    )
    $display = ($Args | ForEach-Object { Quote-Arg $_ }) -join ' '
    Write-Host "> $display"
    if ($DryRun) {
        return 0
    }
    $exe = $Args[0]
    $argList = @()
    if ($Args.Count -gt 1) {
        $argList = @($Args[1..($Args.Count - 1)])
    }
    & $exe @argList
    $exitCode = $LASTEXITCODE
    if ($null -eq $exitCode) { $exitCode = 0 }
    return [int]$exitCode
}

function Require-Admin {
    $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Throw-InstallError 'Run this script in an elevated PowerShell session (Run as Administrator).'
    }
}

function Get-WingetPath {
    $cmd = Get-Command winget -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

function Get-NodePath {
    foreach ($name in @('node.exe', 'node')) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }
    }
    foreach ($candidate in @(
        (Join-Path ${env:ProgramFiles} 'nodejs\node.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'nodejs\node.exe'),
        (Join-Path $env:LocalAppData 'Programs\nodejs\node.exe')
    )) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) { return $candidate }
    }
    return $null
}

function Get-NpmPath {
    foreach ($name in @('npm.cmd', 'npm')) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }
    }
    foreach ($candidate in @(
        (Join-Path ${env:ProgramFiles} 'nodejs\npm.cmd'),
        (Join-Path ${env:ProgramFiles(x86)} 'nodejs\npm.cmd'),
        (Join-Path $env:LocalAppData 'Programs\nodejs\npm.cmd')
    )) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) { return $candidate }
    }
    return $null
}

function Ensure-NodeAndNpm {
    $node = Get-NodePath
    $npm = Get-NpmPath
    if ($node -and $npm) {
        Write-Log "Node.js is already available: $node"
        Write-Log "npm is already available: $npm"
        return $npm
    }

    $winget = Get-WingetPath
    if (-not $winget) {
        Throw-InstallError 'winget was not found. Install Microsoft App Installer / winget first.'
    }

    Write-Log 'Installing Node.js LTS via winget (includes npm)...'
    $code = Invoke-ExternalCommand -Args @($winget, 'install', '--id', $NodeWingetId, '-e', '--accept-package-agreements', '--accept-source-agreements', '--silent', '--disable-interactivity')
    if ($code -ne 0) {
        Throw-InstallError "winget Node.js install failed with exit code $code."
    }

    $npm = Get-NpmPath
    if (-not $npm) {
        Throw-InstallError 'npm was not found after Node.js setup.'
    }
    Write-Log "npm is available: $npm"
    return $npm
}

function Get-Python314Prefix {
    if (Test-CommandAvailable 'py') {
        if ($DryRun) { return @('py', '-3.14') }
        & py -3.14 -c "import sys; raise SystemExit(0 if sys.version_info[:2]==(3,14) else 1)" *> $null
        if ($LASTEXITCODE -eq 0) { return @('py', '-3.14') }
    }
    foreach ($name in @('python3.14.exe', 'python3.14', 'python.exe', 'python')) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        if ($DryRun) { return @($cmd.Source) }
        & $cmd.Source -c "import sys; raise SystemExit(0 if sys.version_info[:2]==(3,14) else 1)" *> $null
        if ($LASTEXITCODE -eq 0) { return @($cmd.Source) }
    }
    return $null
}

function Ensure-Python314ForMistral {
    $prefix = Get-Python314Prefix
    if ($prefix) {
        Write-Log "Python 3.14 is already available for Mistral Vibe: $($prefix -join ' ')"
        return $prefix
    }

    $winget = Get-WingetPath
    if (-not $winget) {
        Throw-InstallError 'winget was not found. Cannot install Python 3.14 for Mistral Vibe.'
    }
    Write-Log 'Installing Python 3.14 via winget for Mistral Vibe...'
    $code = Invoke-ExternalCommand -Args @($winget, 'install', '--id', $PythonWingetId, '-e', '--accept-package-agreements', '--accept-source-agreements', '--silent', '--disable-interactivity')
    if ($code -ne 0) {
        Throw-InstallError "winget Python 3.14 install failed with exit code $code."
    }
    $prefix = Get-Python314Prefix
    if (-not $prefix) {
        Throw-InstallError 'Python 3.14 was not found after installation.'
    }
    return $prefix
}

function Ensure-PipAndUvForMistral {
    param([string[]]$PythonPrefix)
    [void](Invoke-ExternalCommand -Args (@($PythonPrefix) + @('-m', 'pip', 'install', '--user', '--upgrade') + $PipFlags + @('pip')))
    [void](Invoke-ExternalCommand -Args (@($PythonPrefix) + @('-m', 'pip', 'install', '--user', '--upgrade') + $PipFlags + @('uv')))
}

function Install-MistralVibe {
    $pythonPrefix = Ensure-Python314ForMistral
    Ensure-PipAndUvForMistral -PythonPrefix $pythonPrefix

    $uvCmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($uvCmd) {
        $code = Invoke-ExternalCommand -Args @($uvCmd.Source, 'tool', 'install', '--upgrade', 'mistral-vibe')
        if ($code -eq 0) {
            Write-Log 'Installed Mistral Vibe CLI using uv.'
            return
        }
        Write-WarnLog 'uv tool install failed; falling back to pip.'
    }

    $code = Invoke-ExternalCommand -Args (@($pythonPrefix) + @('-m', 'pip', 'install', '--user', '--upgrade') + $PipFlags + @('mistral-vibe'))
    if ($code -ne 0) {
        Throw-InstallError "Failed to install Mistral Vibe (exit code $code)."
    }
    Write-Log 'Installed Mistral Vibe CLI using pip.'
}

function Install-OllamaOfficial {
    $winget = Get-WingetPath
    if (-not $winget) {
        Throw-InstallError 'winget was not found. Cannot install Ollama.'
    }
    Write-Log 'Installing official Ollama via winget...'
    $code = Invoke-ExternalCommand -Args @($winget, 'install', '--id', $OllamaWingetId, '-e', '--accept-package-agreements', '--accept-source-agreements', '--silent', '--disable-interactivity')
    if ($code -ne 0) {
        Write-WarnLog "winget install failed (exit $code). Trying winget upgrade..."
        $code = Invoke-ExternalCommand -Args @($winget, 'upgrade', '--id', $OllamaWingetId, '-e', '--accept-package-agreements', '--accept-source-agreements', '--silent', '--disable-interactivity')
        if ($code -ne 0) {
            Throw-InstallError "Failed to install/update Ollama (exit code $code)."
        }
    }
    Write-Log 'Installed/updated Ollama (official).'
}

function Install-NpmCliTarget {
    param(
        [Parameter(Mandatory = $true)][string]$Key,
        [Parameter(Mandatory = $true)][string]$NpmPath
    )
    if (-not $NpmCliSpecs.ContainsKey($Key)) {
        Throw-InstallError "Unknown npm target: $Key"
    }

    $spec = $NpmCliSpecs[$Key]
    $npmDir = Split-Path -Parent $NpmPath
    if ($npmDir) {
        $env:PATH = $npmDir + ';' + [string]$env:PATH
    }
    $env:npm_config_update_notifier = 'false'
    foreach ($pkg in $spec.Packages) {
        Write-Log "Trying npm package for $($spec.Label): $pkg"
        $code = Invoke-ExternalCommand -Args (@($NpmPath) + $NpmFlags + @('install', '-g', $pkg))
        if ($code -eq 0) {
            Write-Log "Installed $($spec.Label) using package $pkg"
            return
        }
    }
    Throw-InstallError "Failed to install $($spec.Label) via npm."
}

function Build-WindowsUpdaterScript {
@'
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$NpmFlags = @("--no-fund","--no-audit","--no-update-notifier","--loglevel","error")
$PipFlags = @("--disable-pip-version-check","--no-input","--quiet")
$NpmPackages = @(
  "@anthropic-ai/claude-code",
  "@openai/codex",
  "@google/gemini-cli",
  "@vibe-kit/grok-cli",
  "@qwen-code/qwen-code",
  "qwen-code",
  "@github/copilot",
  "@githubnext/github-copilot-cli",
  "openclaw",
  "ironclaw"
)

function Test-Cmd([string]$Name) { return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue) }
function Get-NpmCmd() { return Get-Command npm -ErrorAction SilentlyContinue }
$npmCmd = Get-NpmCmd
if ($npmCmd) {
  $npmDir = Split-Path -Parent $npmCmd.Source
  if ($npmDir) { $env:PATH = $npmDir + ';' + [string]$env:PATH }
}
$env:npm_config_update_notifier = 'false'
function Update-NpmCli([string[]]$Candidates) {
  if (-not $npmCmd) { return }
  foreach ($pkg in $Candidates) {
    & $npmCmd.Source list -g --depth=0 $pkg *> $null
    if ($LASTEXITCODE -ne 0) { continue }
    & $npmCmd.Source @NpmFlags update -g $pkg *>&1 | Out-Null
    return
  }
}

if ($npmCmd) {
  Update-NpmCli @("@anthropic-ai/claude-code")
  Update-NpmCli @("@openai/codex")
  Update-NpmCli @("@google/gemini-cli")
  Update-NpmCli @("@vibe-kit/grok-cli")
  Update-NpmCli @("@qwen-code/qwen-code","qwen-code")
  Update-NpmCli @("@github/copilot","@githubnext/github-copilot-cli")
  Update-NpmCli @("openclaw")
  Update-NpmCli @("ironclaw")
}

if (Test-Cmd "py") {
  & py -3.14 -m pip install --user --upgrade @PipFlags pip *>&1 | Out-Null
  & py -3.14 -m pip install --user --upgrade @PipFlags uv *>&1 | Out-Null
}
if (Test-Cmd "uv") {
  & uv tool install --upgrade mistral-vibe *>&1 | Out-Null
} elseif (Test-Cmd "py") {
  & py -3.14 -m pip install --user --upgrade @PipFlags mistral-vibe *>&1 | Out-Null
}

if (Test-Cmd "winget") {
  & winget upgrade --id Ollama.Ollama -e --accept-package-agreements --accept-source-agreements --silent --disable-interactivity *>&1 | Out-Null
}
'@
}

function Ensure-HiddenAutoUpdateTask {
    if ($NoAutoUpdate) {
        Write-Log 'Hidden auto-update task disabled for this run.'
        return
    }
    if ($DryRun) {
        Write-Log "Dry-run: would configure hidden Scheduled Task '$AutoUpdateTaskName'."
        return
    }

    New-Item -ItemType Directory -Force -Path $SupportDir | Out-Null
    Set-Content -LiteralPath $AutoUpdateScriptPath -Value (Build-WindowsUpdaterScript) -Encoding UTF8

    $actionArgs = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$AutoUpdateScriptPath`""
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $actionArgs
    $triggerStartup = New-ScheduledTaskTrigger -AtStartup
    $triggerLogon = New-ScheduledTaskTrigger -AtLogOn
    $triggerDaily = New-ScheduledTaskTrigger -Daily -At $AutoUpdateTime
    $settings = New-ScheduledTaskSettingsSet -Hidden -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    $principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $AutoUpdateTaskName -Action $action -Trigger @($triggerStartup, $triggerLogon, $triggerDaily) -Settings $settings -Principal $principal -Description 'Hidden AI CLI auto-update task created by InstallTheCli one-click PowerShell script.' -Force | Out-Null
    Write-Log "Configured hidden auto-update task (startup, logon, daily $AutoUpdateTime)."
}

function Show-Targets {
    @(
        'claude', 'codex', 'gemini', 'grok', 'qwen', 'copilot', 'openclaw', 'ironclaw', 'mistral', 'ollama', 'all'
    ) | ForEach-Object { Write-Host $_ }
}

function Show-Usage {
@"
Usage:
  .\install_all_windows.ps1 [command] [target] [-NoAutoUpdate] [-DryRun] [-AutoUpdateTime "3:00AM"]

Commands:
  install-all              Install all supported CLIs (default)
  install <target>         Install one target (claude/codex/gemini/grok/qwen/copilot/mistral/ollama/all)
  setup-updater            Configure hidden auto-update Scheduled Task only
  list                     List supported targets
  help                     Show help (or use: Get-Help .\install_all_windows.ps1 -Detailed)
"@ | Write-Host
}

function Install-Target {
    param([Parameter(Mandatory = $true)][string]$NormalizedTarget)
    switch ($NormalizedTarget) {
        'claude'   { $npm = Ensure-NodeAndNpm; Install-NpmCliTarget -Key 'claude' -NpmPath $npm }
        'codex'    { $npm = Ensure-NodeAndNpm; Install-NpmCliTarget -Key 'codex' -NpmPath $npm }
        'gemini'   { $npm = Ensure-NodeAndNpm; Install-NpmCliTarget -Key 'gemini' -NpmPath $npm }
        'grok'     { $npm = Ensure-NodeAndNpm; Install-NpmCliTarget -Key 'grok' -NpmPath $npm }
        'qwen'     { $npm = Ensure-NodeAndNpm; Install-NpmCliTarget -Key 'qwen' -NpmPath $npm }
        'copilot'  { $npm = Ensure-NodeAndNpm; Install-NpmCliTarget -Key 'copilot' -NpmPath $npm }
        'openclaw' { $npm = Ensure-NodeAndNpm; Install-NpmCliTarget -Key 'openclaw' -NpmPath $npm }
        'ironclaw' { $npm = Ensure-NodeAndNpm; Install-NpmCliTarget -Key 'ironclaw' -NpmPath $npm }
        'mistral' { Install-MistralVibe }
        'mistral-vibe' { Install-MistralVibe }
        'vibe'    { Install-MistralVibe }
        'ollama'  { Install-OllamaOfficial }
        'all'     { Install-AllTargets }
        default   { Throw-InstallError "Unknown target: $NormalizedTarget" }
    }
}

function Install-AllTargets {
    $npm = Ensure-NodeAndNpm
    foreach ($key in @('claude','codex','gemini','grok','qwen','copilot','openclaw','ironclaw')) {
        Install-NpmCliTarget -Key $key -NpmPath $npm
    }
    Install-MistralVibe
    Install-OllamaOfficial
}

function Normalize-Subcommand {
    param([string]$Value)
    $normalizedValue = if ([string]::IsNullOrWhiteSpace($Value)) { 'install-all' } else { $Value.ToLowerInvariant() }
    switch ($normalizedValue) {
        '' { 'install-all' }
        'all' { 'install-all' }
        'install-all' { 'install-all' }
        'install' { 'install' }
        'setup-updater' { 'setup-updater' }
        'setup-auto-update' { 'setup-updater' }
        'updater' { 'setup-updater' }
        'list' { 'list' }
        'help' { 'help' }
        '?' { 'help' }
        default { $normalizedValue }
    }
}

function Main {
    if ($Help) {
        Show-Usage
        return
    }

    $normalizedCommand = Normalize-Subcommand $Command
    $normalizedTarget = if ([string]::IsNullOrWhiteSpace($Target)) { 'all' } else { $Target.ToLowerInvariant() }

    switch ($normalizedCommand) {
        'help' { Show-Usage; return }
        'list' { Show-Targets; return }
        default { }
    }

    Require-Admin
    Write-Log 'Windows one-click AI CLI installer started.'
    Write-Log "Subcommand: $normalizedCommand"
    if ($normalizedCommand -eq 'install') { Write-Log "Target: $normalizedTarget" }
    if ($DryRun) { Write-Log 'Dry-run enabled. Commands will be printed only.' }

    switch ($normalizedCommand) {
        'install-all' {
            Install-AllTargets
            Ensure-HiddenAutoUpdateTask
        }
        'install' {
            Install-Target -NormalizedTarget $normalizedTarget
            Ensure-HiddenAutoUpdateTask
        }
        'setup-updater' {
            Ensure-HiddenAutoUpdateTask
        }
        default {
            Throw-InstallError "Unknown command: $Command"
        }
    }

    Write-Log 'Done.'
}

try {
    Main
}
catch {
    Write-Error $_
    exit 1
}
