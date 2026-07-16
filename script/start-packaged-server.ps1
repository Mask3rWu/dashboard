param(
    [string]$ServerExe = "",
    [string]$ConfigPath = "",
    [string]$MySqlServiceName = "MySQL80",
    [int]$ServerPort = 9000,
    [int]$StartupTimeoutSeconds = 30
)

$ErrorActionPreference = "Stop"

if (-not $ServerExe) {
    $ServerExe = Join-Path $PSScriptRoot "FlightAnalyzerServer.exe"
}
if (-not $ConfigPath) {
    $ConfigPath = Join-Path $PSScriptRoot "flight_analyzer.ini"
}

$ServerExe = (Resolve-Path -LiteralPath $ServerExe -ErrorAction Stop).Path
$ConfigPath = (Resolve-Path -LiteralPath $ConfigPath -ErrorAction Stop).Path

if ($MySqlServiceName) {
    $mysql = Get-Service -Name $MySqlServiceName -ErrorAction Stop
    if ($mysql.Status -ne "Running") {
        Write-Host "Starting MySQL service '$MySqlServiceName'..."
        Start-Service -Name $MySqlServiceName
        $mysql.WaitForStatus("Running", [TimeSpan]::FromSeconds(30))
    }
    Write-Host "MySQL service '$MySqlServiceName' is running."
}

$healthUrl = "http://127.0.0.1:$ServerPort/api/health"
try {
    $existing = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2
    Write-Host "Flight Analyzer Server is already healthy at $healthUrl" -ForegroundColor Green
    $existing | ConvertTo-Json -Compress
    exit 0
} catch {
    # The server is not ready yet; start it below.
}

$workingDirectory = Split-Path -Parent $ServerExe
$argument = "--config=`"$ConfigPath`""
Write-Host "Starting Flight Analyzer Server..."
$process = Start-Process `
    -FilePath $ServerExe `
    -ArgumentList $argument `
    -WorkingDirectory $workingDirectory `
    -PassThru

$deadline = (Get-Date).AddSeconds($StartupTimeoutSeconds)
do {
    Start-Sleep -Milliseconds 500
    if ($process.HasExited) {
        throw "FlightAnalyzerServer.exe exited before becoming healthy (exit code $($process.ExitCode))."
    }
    try {
        $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2
        Write-Host "Flight Analyzer Server is healthy at $healthUrl (PID $($process.Id))." -ForegroundColor Green
        $health | ConvertTo-Json -Compress
        exit 0
    } catch {
        # Continue polling until the deadline.
    }
} while ((Get-Date) -lt $deadline)

throw "Server process $($process.Id) is running, but $healthUrl did not become healthy within $StartupTimeoutSeconds seconds."
