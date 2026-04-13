param(
    [ValidateSet('install', 'remove', 'status')]
    [string]$Action = 'install',
    [string]$TaskName = 'PaperAgentWatch'
)

$ErrorActionPreference = 'Stop'

function Get-ProjectRoot {
    if ($PSScriptRoot) {
        return $PSScriptRoot
    }

    if ($PSCommandPath) {
        return (Split-Path -Parent $PSCommandPath)
    }

    if ($MyInvocation.MyCommand.Path) {
        return (Split-Path -Parent $MyInvocation.MyCommand.Path)
    }

    throw 'Unable to determine the script directory. Run this script from a saved .ps1 file.'
}

$ProjectRoot = Get-ProjectRoot
$AutostartRoot = Join-Path $ProjectRoot 'autostart'
$InstallScriptPath = Join-Path $AutostartRoot 'install_or_update_watch_task.ps1'
$RemoveScriptPath = Join-Path $AutostartRoot 'remove_watch_task.ps1'

switch ($Action) {
    'install' {
        & $InstallScriptPath -TaskName $TaskName
    }

    'remove' {
        & $RemoveScriptPath -TaskName $TaskName
        Write-Output "Scheduled task '$TaskName' has been removed."
    }

    'status' {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if ($null -eq $task) {
            Write-Output "Scheduled task '$TaskName' is not installed."
            exit 0
        }

        $taskInfo = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction SilentlyContinue
        [pscustomobject]@{
            TaskName = $task.TaskName
            State = $task.State
            LastRunTime = $taskInfo.LastRunTime
            NextRunTime = $taskInfo.NextRunTime
        } | Format-List
    }
}
