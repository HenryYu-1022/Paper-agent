$ErrorActionPreference = "Stop"

function Get-ScriptRoot {
    if ($PSScriptRoot) {
        return $PSScriptRoot
    }

    return (Split-Path -Parent $MyInvocation.MyCommand.Path)
}

function Ensure-Path {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        New-Item -ItemType Directory -Path $Path -Force | Out-Null
    }
}

function Read-Config {
    param([string]$Path)

    return Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
}

$ProjectRoot = Get-ScriptRoot
$WorkflowRoot = Join-Path $ProjectRoot 'pdf_to_markdown'
$ConfigPath = Join-Path $WorkflowRoot 'settings.json'

if (-not (Test-Path -LiteralPath $ConfigPath)) {
    throw "Missing required path: $ConfigPath"
}

$Config = Read-Config -Path $ConfigPath
$PythonwPath = [string]$Config.pythonw_path
$WatchScriptPath = Join-Path $WorkflowRoot 'watch_folder_resilient.py'
$WorkingDirectory = $WorkflowRoot
$ModelCacheDir = ''
if ($Config.PSObject.Properties.Name -contains 'model_cache_dir') {
    $ModelCacheDir = [string]$Config.model_cache_dir
}

$LogRoot = Join-Path ([string]$Config.work_root) 'logs'
$SupervisorLogPath = Join-Path $LogRoot 'zotero_pdf_watch_supervisor.log'
$WatcherStatePath = Join-Path $LogRoot 'zotero_pdf_watch_supervisor_state.json'
$MutexName = 'Global\ZoteroPdfMarkdownWatchSupervisor'
$WatcherCheckIntervalSeconds = 15

function Write-Log {
    param(
        [string]$Message,
        [string]$Level = 'INFO'
    )

    $timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Add-Content -LiteralPath $SupervisorLogPath -Value "$timestamp | $Level | $Message"
}

function Test-RequiredPaths {
    $required = @($PythonwPath, $WorkflowRoot, $WatchScriptPath, $ConfigPath)
    foreach ($path in $required) {
        if ([string]::IsNullOrWhiteSpace($path) -or -not (Test-Path -LiteralPath $path)) {
            throw "Missing required path: $path"
        }
    }
}

function Save-WatcherState {
    param([System.Diagnostics.Process]$Process)

    $state = @{
        pid = $Process.Id
        start_time = $Process.StartTime.ToString('o')
    }
    $state | ConvertTo-Json | Set-Content -LiteralPath $WatcherStatePath -Encoding UTF8
}

function Get-WatcherProcess {
    if (-not (Test-Path -LiteralPath $WatcherStatePath)) {
        return $null
    }

    try {
        $state = Get-Content -LiteralPath $WatcherStatePath -Raw | ConvertFrom-Json
        $process = Get-Process -Id ([int]$state.pid) -ErrorAction Stop
        $savedStart = [datetime]::Parse($state.start_time)
        if ($process.StartTime -eq $savedStart) {
            return $process
        }
    } catch {
        return $null
    }

    return $null
}

function Start-Watcher {
    $existing = Get-WatcherProcess
    if ($null -ne $existing) {
        return $existing
    }

    Write-Log "Starting pdf watch process from $WatchScriptPath."
    $process = Start-Process -FilePath $PythonwPath `
        -ArgumentList @($WatchScriptPath, '--config', $ConfigPath) `
        -WorkingDirectory $WorkingDirectory `
        -PassThru

    Start-Sleep -Seconds 2
    if ($process.HasExited) {
        throw "watch_folder_resilient.py exited immediately with code $($process.ExitCode)"
    }

    Save-WatcherState -Process $process
    Write-Log "Started pdf watch process with PID $($process.Id)."
    return $process
}

Test-RequiredPaths
Ensure-Path -Path $LogRoot
if (-not [string]::IsNullOrWhiteSpace($ModelCacheDir)) {
    Ensure-Path -Path $ModelCacheDir
    $env:MODEL_CACHE_DIR = $ModelCacheDir
}

$mutex = New-Object System.Threading.Mutex($false, $MutexName)
$hasLock = $false

try {
    $hasLock = $mutex.WaitOne(0, $false)
    if (-not $hasLock) {
        Write-Log 'Another supervisor instance is already running.' 'WARN'
        exit 0
    }

    Write-Log "Supervisor started. Workflow root: $WorkflowRoot"
    Write-Log "Config path: $ConfigPath"
    Write-Log "Log root: $LogRoot"

    while ($true) {
        try {
            $null = Start-Watcher
        } catch {
            Write-Log "Watcher start/check failed: $($_.Exception.Message)" 'ERROR'
        }

        Start-Sleep -Seconds $WatcherCheckIntervalSeconds
    }
} finally {
    if ($hasLock) {
        $mutex.ReleaseMutex() | Out-Null
    }
    $mutex.Dispose()
}
