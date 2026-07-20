param(
    [string]$MySqlExe = "",
    [string]$MySqlServiceName = "MySQL80",
    [string]$MySqlAdminUser = "root",
    [string]$MySqlHost = "127.0.0.1",
    [int]$MySqlPort = 3306,
    [string]$Database = "flight_analyzer",
    [string]$AppUser = "flight",
    [string]$AppUserHost = "127.0.0.1",
    [string]$Charset = "utf8mb4",
    [string]$Collation = "utf8mb4_unicode_ci"
)

$ErrorActionPreference = "Stop"

$identifierPattern = '^[A-Za-z0-9_$]+$'
foreach ($identifier in @($Database, $Charset, $Collation)) {
    if ($identifier -notmatch $identifierPattern) {
        throw "Invalid MySQL identifier: '$identifier'"
    }
}
if ($MySqlPort -lt 1 -or $MySqlPort -gt 65535) {
    throw "MySqlPort must be between 1 and 65535."
}
if (-not $AppUser -or $AppUser.Length -gt 32) {
    throw "AppUser must contain 1 to 32 characters."
}
foreach ($literal in @($AppUser, $AppUserHost)) {
    if ($literal.IndexOfAny([char[]]@([char]0, "`r", "`n")) -ge 0) {
        throw "MySQL account values must not contain NUL or line breaks."
    }
}

if (-not $MySqlExe) {
    $command = Get-Command mysql.exe -ErrorAction SilentlyContinue
    if ($command) {
        $MySqlExe = $command.Source
    } else {
        $candidates = @(
            (Join-Path $env:ProgramFiles "MySQL\MySQL Server 8.4\bin\mysql.exe"),
            (Join-Path $env:ProgramFiles "MySQL\MySQL Server 8.0\bin\mysql.exe")
        )
        $MySqlExe = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    }
}
if (-not $MySqlExe -or -not (Test-Path -LiteralPath $MySqlExe)) {
    throw "mysql.exe was not found. Pass its full path with -MySqlExe."
}
$MySqlExe = (Resolve-Path -LiteralPath $MySqlExe -ErrorAction Stop).Path

if ($MySqlServiceName) {
    $mysqlService = Get-Service -Name $MySqlServiceName -ErrorAction Stop
    if ($mysqlService.Status -ne "Running") {
        Write-Host "Starting MySQL service '$MySqlServiceName'..."
        Start-Service -Name $MySqlServiceName
        $mysqlService.WaitForStatus("Running", [TimeSpan]::FromSeconds(30))
    }
}

function ConvertTo-MySqlLiteral([string]$Value) {
    return $Value.Replace("\", "\\").Replace("'", "''")
}

function ConvertFrom-SecureStringPlainText([Security.SecureString]$Value) {
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Value)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
}

$secureAppPassword = Read-Host "Application password configured in the packaged [mysql] section" -AsSecureString
$secureAppPasswordConfirmation = Read-Host "Confirm application password" -AsSecureString
$AppPassword = ConvertFrom-SecureStringPlainText $secureAppPassword
$appPasswordConfirmation = ConvertFrom-SecureStringPlainText $secureAppPasswordConfirmation
if (-not $AppPassword) {
    throw "The application password must not be empty."
}
if ($AppPassword -cne $appPasswordConfirmation) {
    throw "The application passwords do not match."
}
$appPasswordConfirmation = $null

$databaseSql = $Database
$userSql = ConvertTo-MySqlLiteral $AppUser
$passwordSql = ConvertTo-MySqlLiteral $AppPassword
$userHostSql = ConvertTo-MySqlLiteral $AppUserHost
$sql = @"
CREATE DATABASE IF NOT EXISTS ``$databaseSql`` CHARACTER SET $Charset COLLATE $Collation;
CREATE USER IF NOT EXISTS '$userSql'@'$userHostSql' IDENTIFIED BY '$passwordSql';
ALTER USER '$userSql'@'$userHostSql' IDENTIFIED BY '$passwordSql';
GRANT ALL PRIVILEGES ON ``$databaseSql``.* TO '$userSql'@'$userHostSql';
FLUSH PRIVILEGES;
"@

$securePassword = Read-Host "MySQL administrator password for $MySqlAdminUser" -AsSecureString
$passwordPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
$previousMySqlPassword = [Environment]::GetEnvironmentVariable("MYSQL_PWD", "Process")
try {
    $env:MYSQL_PWD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($passwordPointer)
    $sql | & $MySqlExe `
        --protocol=TCP `
        --host=$MySqlHost `
        --port=$MySqlPort `
        --user=$MySqlAdminUser `
        --default-character-set=utf8mb4 `
        --batch
    if ($LASTEXITCODE -ne 0) {
        throw "mysql.exe failed with exit code $LASTEXITCODE"
    }
} finally {
    if ($null -eq $previousMySqlPassword) {
        Remove-Item Env:\MYSQL_PWD -ErrorAction SilentlyContinue
    } else {
        $env:MYSQL_PWD = $previousMySqlPassword
    }
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordPointer)
    $AppPassword = $null
}

Write-Host "MySQL initialization complete for database '$Database'." -ForegroundColor Green
Write-Host "Remove this administrator-only script from the server after deployment." -ForegroundColor Yellow
