param(
    [int]$WaitForPid = 0,
    [UInt64]$WaitForCreated = 0
)

$ErrorActionPreference = 'Stop'

$projectDirectory = Split-Path -Parent $PSScriptRoot
$pythonExecutable = Join-Path $projectDirectory '.venv\Scripts\pythonw.exe'
$runnerScript = Join-Path $PSScriptRoot 'windows_runner.pyw'
$taskName = 'Express Telegram Tracker'
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()

if (-not (Test-Path -LiteralPath $pythonExecutable)) {
    throw "Python environment not found: $pythonExecutable"
}
if (-not (Test-Path -LiteralPath (Join-Path $projectDirectory '.env'))) {
    throw 'Create .env and configure TELEGRAM_BOT_TOKEN first.'
}

$runnerArguments = '"{0}"' -f $runnerScript
if ($WaitForPid -gt 0) {
    if ($WaitForCreated -eq 0) {
        throw 'WaitForCreated is required with WaitForPid to prevent waiting for a reused PID.'
    }
    $runnerArguments += ' --wait-for-pid {0} --wait-for-created {1}' -f $WaitForPid, $WaitForCreated
}
$action = New-ScheduledTaskAction -Execute $pythonExecutable -Argument $runnerArguments -WorkingDirectory $projectDirectory
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity.User.Value
$trigger.Delay = 'PT30S'
$principal = New-ScheduledTaskPrincipal -UserId $identity.User.Value -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RunOnlyIfNetworkAvailable
$task = New-ScheduledTask -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'Run the Telegram parcel tracker hidden, 30 seconds after this user signs in. Restart after failures.'

Register-ScheduledTask -TaskName $taskName -InputObject $task -Force | Select-Object TaskName, State
Write-Output 'Registered. The bot will start at the next sign-in; the current manually started bot is left running.'
