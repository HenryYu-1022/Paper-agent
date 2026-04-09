param(
    [string]$TaskName = 'ZoteroPdfMarkdownWatch'
)

$ErrorActionPreference = 'Stop'

$ProjectRoot = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$SupervisorPath = Join-Path $ProjectRoot 'zotero_pdf_watch_supervisor.ps1'

if (-not (Test-Path -LiteralPath $SupervisorPath)) {
    throw "Supervisor script not found: $SupervisorPath"
}

function Stop-ZoteroWatchProcesses {
    $patterns = @(
        'zotero_pdf_watch_supervisor.ps1',
        'watch_folder_resilient.py'
    )

    $targets = Get-CimInstance Win32_Process |
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

Stop-ZoteroWatchProcesses

$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$SupervisorPath`""
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName
Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State | Format-List
