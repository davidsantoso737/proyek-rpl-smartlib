[CmdletBinding()]
param(
    [switch]$Enable,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$WrapperPath = Join-Path $ProjectRoot 'scripts\run_hidden.vbs'
$WScriptPath = Join-Path $env:SystemRoot 'System32\wscript.exe'
$LogDir = Join-Path $ProjectRoot 'logs'
$LogPath = Join-Path $LogDir 'task-scheduler-bootstrap.log'
$BackupRoot = Join-Path $ProjectRoot 'backups\task-scheduler'
$ExecutionTimeLimit = 'PT15M'

$TaskSpecs = @(
    [pscustomobject]@{
        Name = 'SmartLib WhatsApp Outbox'
        BatchPath = (Join-Path $ProjectRoot 'scripts\run_whatsapp_outbox.bat')
    },
    [pscustomobject]@{
        Name = 'SmartLib WhatsApp Reminders'
        BatchPath = (Join-Path $ProjectRoot 'scripts\run_whatsapp_reminders.bat')
    }
)

function Write-Log {
    param(
        [string]$Level,
        [string]$Message
    )
    $line = "[{0}] {1} {2}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Level, $Message
    Write-Host $line
    if ($script:LogReady) {
        Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8
    }
}

$script:LogReady = $false
try {
    if (-not (Test-Path -LiteralPath $LogDir)) {
        New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
    }
    $script:LogReady = $true
} catch {
    Write-Warning "Tidak dapat menyiapkan log: $($_.Exception.Message)"
}

Write-Log 'INFO' "Mulai bootstrap task WhatsApp. Enable=$Enable DryRun=$DryRun"

if (-not (Test-Path -LiteralPath $WrapperPath)) {
    Write-Log 'ERROR' "Wrapper tidak ditemukan: $WrapperPath"
    exit 2
}
if (-not (Test-Path -LiteralPath $WScriptPath)) {
    Write-Log 'ERROR' "wscript.exe tidak ditemukan: $WScriptPath"
    exit 2
}

$failed = $false
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backupDir = Join-Path $BackupRoot $stamp

foreach ($spec in $TaskSpecs) {
    $name = $spec.Name
    $batch = $spec.BatchPath
    $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue

    if ($null -eq $task) {
        Write-Log 'ERROR' "Task tidak ditemukan: $name. Tidak membuat task baru."
        $failed = $true
        continue
    }
    if (-not (Test-Path -LiteralPath $batch)) {
        Write-Log 'ERROR' "Batch tidak ditemukan: $batch"
        $failed = $true
        continue
    }

    $currentAction = @($task.Actions)[0]
    $desiredArguments = "//B //NoLogo `"$WrapperPath`" `"$batch`""
    $actionChanged = (
        $currentAction.Execute -ne $WScriptPath -or
        $currentAction.Arguments -ne $desiredArguments -or
        $currentAction.WorkingDirectory -ne $ProjectRoot
    )

    $settings = $task.Settings
    $settingsChanged = (
        $settings.ExecutionTimeLimit -ne $ExecutionTimeLimit -or
        $settings.MultipleInstances -ne 'IgnoreNew' -or
        $settings.StartWhenAvailable -ne $true -or
        $settings.AllowHardTerminate -ne $true
    )
    $enableChanged = ($Enable -and -not $settings.Enabled)

    if (-not ($actionChanged -or $settingsChanged -or $enableChanged)) {
        Write-Log 'INFO' "Task sudah sesuai: $name (State=$($task.State))"
        continue
    }

    if ($DryRun) {
        Write-Log 'INFO' "DRY-RUN $name actionChanged=$actionChanged settingsChanged=$settingsChanged enableChanged=$enableChanged"
        continue
    }

    try {
        if (-not (Test-Path -LiteralPath $backupDir)) {
            New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
        }
        $backupFile = Join-Path $backupDir ("$name.xml")
        Export-ScheduledTask -TaskName $name |
            Set-Content -LiteralPath $backupFile -Encoding Unicode
        Write-Log 'INFO' "Backup task $name -> $backupFile"

        $action = New-ScheduledTaskAction `
            -Execute $WScriptPath `
            -Argument $desiredArguments `
            -WorkingDirectory $ProjectRoot

        $settings.ExecutionTimeLimit = $ExecutionTimeLimit
        $settings.MultipleInstances = 'IgnoreNew'
        $settings.StartWhenAvailable = $true
        $settings.AllowHardTerminate = $true
        if ($Enable) {
            $settings.Enabled = $true
        }

        Set-ScheduledTask -TaskName $name -Action $action -Settings $settings |
            Out-Null

        $verify = Get-ScheduledTask -TaskName $name
        $verifyAction = @($verify.Actions)[0]
        if (
            $verifyAction.Execute -ne $WScriptPath -or
            $verifyAction.Arguments -ne $desiredArguments
        ) {
            throw "Verifikasi action gagal untuk $name"
        }
        if (
            $verify.Settings.ExecutionTimeLimit -ne $ExecutionTimeLimit -or
            $verify.Settings.MultipleInstances -ne 'IgnoreNew'
        ) {
            throw "Verifikasi settings gagal untuk $name"
        }
        if ($Enable -and -not $verify.Settings.Enabled) {
            throw "Task $name belum aktif setelah -Enable"
        }

        Write-Log 'INFO' "Task diperbarui: $name State=$($verify.State) Enabled=$($verify.Settings.Enabled)"
    } catch {
        Write-Log 'ERROR' "Gagal memperbarui $name : $($_.Exception.Message)"
        Write-Log 'ERROR' "Jika akses ditolak, jalankan PowerShell sebagai Administrator atau ubah task melalui Task Scheduler GUI."
        $failed = $true
    }
}

if ($failed) {
    Write-Log 'ERROR' 'Bootstrap selesai dengan kegagalan.'
    exit 1
}

Write-Log 'INFO' 'Bootstrap selesai. Skrip ini tidak memulai atau menjalankan task.'
Write-Log 'INFO' 'Principal tidak diubah. Untuk berjalan tanpa sesi login, ubah manual ke "Run whether user is logged on or not" dengan password yang disimpan.'
exit 0
