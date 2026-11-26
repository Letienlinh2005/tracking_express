param(
    [int]$WaitForPid = 0,
    [UInt64]$WaitForCreated = 0
)

$ErrorActionPreference = 'Stop'

$projectDirectory = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $projectDirectory '.venv\Scripts\python.exe'
$runnerScript = Join-Path $PSScriptRoot 'windows_runner.pyw'
$taskName = 'Express Telegram Tracker'
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()

if (-not (Test-Path -LiteralPath $venvPython)) {
    throw "Python environment not found: $venvPython"
}
$pythonExecutable = & $venvPython -c "import pathlib,sys; print(pathlib.Path(sys.base_prefix) / 'pythonw.exe')"
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $pythonExecutable)) {
    throw 'The virtual environment base interpreter has no pythonw.exe.'
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
$recoveryTrigger = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(1)) -RepetitionInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId $identity.User.Value -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RunOnlyIfNetworkAvailable
$task = New-ScheduledTask -Action $action -Trigger @($trigger, $recoveryTrigger) -Principal $principal -Settings $settings -Description 'Run the Telegram parcel tracker hidden at sign-in; recover a stopped task every minute. Ignore new launches while already running.'

Register-ScheduledTask -TaskName $taskName -InputObject $task -Force | Select-Object TaskName, State
Write-Output 'Registered. Starts at sign-in and checks every minute for a stopped task. Running instances are left alone.'
