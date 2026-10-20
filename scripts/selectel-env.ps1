# Dot-source this script; the password is entered only through hidden input.
param([string]$AccountNumber, [string]$UserName)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskConfigPath = Join-Path $taskRoot '.local/lab-config.json'
if (-not (Test-Path -LiteralPath $taskConfigPath)) {
    New-Item -ItemType Directory -Force (Join-Path $taskRoot '.local') | Out-Null
    Copy-Item -LiteralPath (Join-Path $taskRoot 'config/lab.example.json') -Destination $taskConfigPath
}
$taskConfig = Get-Content -LiteralPath $taskConfigPath -Raw | ConvertFrom-Json
if (-not $taskConfig.project_id) {
    $taskConfig.project_id = Read-Host 'UUID своего проекта Selectel из панели облачных серверов'
    if ($taskConfig.project_id -notmatch '^[a-f0-9]{32}$') { throw 'Некорректный UUID проекта.' }
    $taskConfig | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $taskConfigPath -Encoding utf8
}
$taskCredentials = $null
$taskCredentialPath = Join-Path $taskRoot '.local/operator.json'
if (Test-Path -LiteralPath $taskCredentialPath) {
    $taskCredentials = Get-Content -LiteralPath $taskCredentialPath -Raw | ConvertFrom-Json
}
if (-not $AccountNumber) {
    if ($taskCredentials.domain_name) { $AccountNumber = $taskCredentials.domain_name }
    elseif ($env:OS_USER_DOMAIN_NAME) { $AccountNumber = $env:OS_USER_DOMAIN_NAME }
    else { $AccountNumber = Read-Host 'Номер аккаунта Selectel (не email)' }
}
if (-not $UserName) {
    if ($taskCredentials.username) { $UserName = $taskCredentials.username }
    elseif ($env:OS_USERNAME) { $UserName = $env:OS_USERNAME }
    else { $UserName = Read-Host 'Имя сервисного пользователя оператора Terraform' }
}
if ([string]::IsNullOrWhiteSpace($AccountNumber) -or [string]::IsNullOrWhiteSpace($UserName)) {
    throw 'Номер аккаунта и имя оператора должны быть заполнены.'
}
$taskPassword = Read-Host "Пароль сервисного пользователя $UserName (ввод скрыт)" -AsSecureString
if ($taskPassword.Length -eq 0) { $taskPassword.Dispose(); throw 'Пароль не должен быть пустым.' }
$taskPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($taskPassword)
try { $env:OS_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($taskPointer) }
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($taskPointer)
    $taskPassword.Dispose()
}
$env:OS_AUTH_URL = 'https://cloud.api.selcloud.ru/identity/v3'
$env:OS_IDENTITY_API_VERSION = '3'
$env:OS_USER_DOMAIN_NAME = $AccountNumber
$env:OS_USERNAME = $UserName
$env:OS_PROJECT_ID = $taskConfig.project_id
$env:OS_REGION_NAME = $taskConfig.pools | Where-Object { $_ -ne 'auto' } | Select-Object -First 1
Write-Host 'Окружение оператора готово. Настройки проекта читаются из .local/lab-config.json; пароль не выведен.'
