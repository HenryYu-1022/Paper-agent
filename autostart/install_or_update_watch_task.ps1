param(
    [string]$TaskName = 'PaperAgentWatch'
)

$ErrorActionPreference = 'Stop'

function Get-ScriptDirectory {
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

$ScriptDirectory = Get-ScriptDirectory
$ProjectRoot = Split-Path -Parent $ScriptDirectory
$SupervisorPath = Join-Path $ScriptDirectory 'paper_agent_watch_supervisor.ps1'

if (-not (Test-Path -LiteralPath $SupervisorPath)) {
    throw "Supervisor script not found: $SupervisorPath"
}

function Stop-PaperAgentWatchProcesses {
    $patterns = @(
        'paper_agent_watch_supervisor.ps1',
        'watch_folder_resilient.py'
    )

    try {
        $targets = Get-CimInstance Win32_Process -ErrorAction Stop |
            Where-Object {
                $commandLine = $_.CommandLine
                if (-not $commandLine) {
                    return $false
                }

                foreach ($pattern in $patterns) {
                    if ($commandLine -like "*$pattern*") {
                        return $true
                    }
                }

                return $false
            }
    } catch {
        Write-Warning "Unable to enumerate existing watcher processes. Continuing without stopping them. $($_.Exception.Message)"
        return
    }

    foreach ($target in $targets) {
        try {
            Stop-Process -Id $target.ProcessId -Force -ErrorAction Stop
        } catch {
        }
    }
}

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    try {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    } catch {
    }
}

Stop-PaperAgentWatchProcesses

$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$SupervisorPath`""
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew
$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Limited

try {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Principal $principal `
        -Force | Out-Null
} catch {
    throw "Failed to register scheduled task '$TaskName'. Run PowerShell as Administrator and try again. $($_.Exception.Message)"
}

Start-ScheduledTask -TaskName $TaskName
Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State | Format-List
