<#
.SYNOPSIS
    Register the watcher as a logon scheduled task, so a project is watched without anyone
    remembering to start it.

.DESCRIPTION
    Runs `pythonw -m rl_researcher.watcher` from the project root at logon, windowless. Every
    event it sees goes to <paths.logs>/watcher.log; the toast is the courtesy channel and the
    log is the record.

    It starts nothing. `--launch` does not exist here and `[watcher] launch` is off by default,
    because starting queued work is a decision and a decision needs a person. The watcher's job
    is to make sure the person finds out that there is one to make.

    A task registered under the user's own account (not SYSTEM) is deliberate: the toast has to
    reach the interactive session, and the run has to see the same Python and the same
    filesystem the person does.

.PARAMETER Project
    The project root -- the directory holding rl-researcher.toml. Defaults to the repository
    this script is in, two levels up.

.PARAMETER Name
    The scheduled-task name. One per project, so two projects can both be watched.

.PARAMETER Python
    The pythonw.exe to run. Defaults to the pythonw beside whatever `python` resolves to,
    falling back to `python` itself (which costs a console window and is still better than not
    being watched).

.PARAMETER Remove
    Unregister the task instead of creating it.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install_watcher.ps1 -Project C:\code\Auto-SM64
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install_watcher.ps1 -Remove
#>
[CmdletBinding()]
param(
    [string]$Project = (Split-Path -Parent (Split-Path -Parent $PSCommandPath)),
    [string]$Name = "",
    [string]$Python = "",
    [switch]$Remove
)

$ErrorActionPreference = "Stop"

$Project = (Resolve-Path -LiteralPath $Project).Path
if (-not $Name) { $Name = "rl-researcher watch ({0})" -f (Split-Path -Leaf $Project) }

if ($Remove) {
    if (Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $Name -Confirm:$false
        Write-Host "removed scheduled task: $Name"
    } else {
        Write-Host "no scheduled task named: $Name"
    }
    exit 0
}

$config = Join-Path $Project "rl-researcher.toml"
if (-not (Test-Path -LiteralPath $config)) {
    Write-Error "no rl-researcher.toml in $Project -- that is not a project root"
    exit 1
}

if (-not $Python) {
    $found = Get-Command python -ErrorAction SilentlyContinue
    if (-not $found) {
        Write-Error "no python on PATH; pass -Python C:\path\to\pythonw.exe"
        exit 1
    }
    # pythonw runs it with no console window. If this build has none, python will do: a visible
    # console is a smaller problem than an unwatched project.
    $candidate = Join-Path (Split-Path -Parent $found.Source) "pythonw.exe"
    $Python = if (Test-Path -LiteralPath $candidate) { $candidate } else { $found.Source }
}

Write-Host "project : $Project"
Write-Host "python  : $Python"
Write-Host "task    : $Name"

$action  = New-ScheduledTaskAction -Execute $Python `
                                   -Argument "-m rl_researcher.watcher" `
                                   -WorkingDirectory $Project
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
# StartWhenAvailable so a machine that was asleep at the trigger still gets watched; no
# ExecutionTimeLimit because a watcher is meant to outlive any one run.
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
                                         -DontStopIfGoingOnBatteries `
                                         -StartWhenAvailable `
                                         -ExecutionTimeLimit ([TimeSpan]::Zero) `
                                         -RestartCount 3 `
                                         -RestartInterval (New-TimeSpan -Minutes 5)

Register-ScheduledTask -TaskName $Name -Action $action -Trigger $trigger `
                       -Settings $settings -Force | Out-Null

Write-Host ""
Write-Host "registered. It starts at your next logon; to start it now:"
Write-Host "  Start-ScheduledTask -TaskName '$Name'"
Write-Host "to see what it has done:"
Write-Host "  Get-Content -Wait (Join-Path '$Project' 'docs\measurements\logs\watcher.log')"
Write-Host "to remove it:"
Write-Host "  powershell -File '$PSCommandPath' -Remove -Name '$Name'"
exit 0
