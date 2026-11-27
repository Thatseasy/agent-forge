param(
    [string]$TaskName = "AgentForgeDailyDiscovery",
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$RunAt = "08:30"
)

$ErrorActionPreference = "Stop"

$Action = New-ScheduledTaskAction `
    -Execute "uv.exe" `
    -Argument "run python forge.py discover" `
    -WorkingDirectory $ProjectRoot

$Trigger = New-ScheduledTaskTrigger -Daily -At $RunAt
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "Zero-token daily discovery radar for agent-forge staging/inbox" `
    -Force

Write-Host "Registered Windows Scheduled Task '$TaskName' running daily at $RunAt in $ProjectRoot"
