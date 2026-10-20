# Запускать через dot sourcing: . .\scripts\selectel-env.ps1
# Пароль остаётся только в окружении текущего процесса.
param(
    [string]$AccountNumber,
    [string]$ProjectId,
    [string]$UserName,
    [string]$Region
)
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskDiscoveryPath = Join-Path $taskRoot '.local/selectel/discovery.json'
$taskDiscovery = $null
if (Test-Path -LiteralPath $taskDiscoveryPath) {
    $taskDiscovery = Get-Content -LiteralPath $taskDiscoveryPath -Raw | ConvertFrom-Json
}
$taskConfiguredRegion = $null
$taskHclPath = Join-Path $taskRoot 'terraform-selectel/terraform.tfvars'
$taskJsonPath = Join-Path $taskRoot 'terraform-selectel/terraform.tfvars.json'
if ((Test-Path -LiteralPath $taskHclPath) -and (Test-Path -LiteralPath $taskJsonPath)) {
    throw 'Найдены terraform.tfvars и terraform.tfvars.json. Оставьте один формат параметров.'
}
if (Test-Path -LiteralPath $taskJsonPath) {
    $taskConfiguredRegion = (Get-Content -LiteralPath $taskJsonPath -Raw | ConvertFrom-Json).region
} elseif (Test-Path -LiteralPath $taskHclPath) {
    $taskHcl = Get-Content -LiteralPath $taskHclPath -Raw
    if ($taskHcl -match '(?m)^\s*region\s*=\s*"([^"\r\n]+)"\s*(?:#.*|//.*)?$') {
        $taskConfiguredRegion = $Matches[1]
    } else {
        throw 'Не удалось прочитать region из terraform.tfvars. Укажите строковый литерал region = "ru-9" или используйте JSON tfvars.'
    }
}
if (-not $Region) {
    if ($taskConfiguredRegion) { $Region = $taskConfiguredRegion }
    elseif ($taskDiscovery.region) { $Region = $taskDiscovery.region }
    else { $Region = 'ru-9' }
}
if ($taskConfiguredRegion -and $Region -ne $taskConfiguredRegion) {
    throw "Регион $Region отличается от Terraform ($taskConfiguredRegion). Сначала согласуйте конфигурацию."
}
if ($taskDiscovery.region -and $Region -ne $taskDiscovery.region) {
    throw "Регион $Region отличается от сохранённого каталога ($($taskDiscovery.region)). Обновите каталог для выбранного проекта."
}
if (-not $ProjectId) {
    if ($taskDiscovery.project_id) { $ProjectId = $taskDiscovery.project_id }
    elseif ($env:OS_PROJECT_ID) { $ProjectId = $env:OS_PROJECT_ID }
    else { $ProjectId = Read-Host 'ID проекта: UUID из панели Selectel -> Облачные серверы -> нужный проект (не название)' }
}
if ($taskDiscovery.project_id -and $ProjectId -ne $taskDiscovery.project_id) {
    throw 'ID проекта отличается от сохранённого discovery.json. Используйте каталог нужного проекта.'
}
if (-not $AccountNumber) {
    if ($taskDiscovery.domain_name) { $AccountNumber = $taskDiscovery.domain_name }
    elseif ($env:OS_USER_DOMAIN_NAME) { $AccountNumber = $env:OS_USER_DOMAIN_NAME }
    else { $AccountNumber = Read-Host 'Номер аккаунта Selectel: числовой номер в меню аккаунта панели (не email и не ID проекта)' }
}
if (-not $UserName) {
    if ($env:OS_USERNAME -and $env:OS_USERNAME -ne 'otus-dz05-fence') { $UserName = $env:OS_USERNAME }
    else {
        $UserName = Read-Host 'Имя сервисного пользователя оператора Terraform (не fencing); Enter = otus-dz05-terraform'
        if (-not $UserName) { $UserName = 'otus-dz05-terraform' }
    }
}
if ([string]::IsNullOrWhiteSpace($AccountNumber) -or [string]::IsNullOrWhiteSpace($ProjectId) -or
    [string]::IsNullOrWhiteSpace($UserName) -or [string]::IsNullOrWhiteSpace($Region)) {
    throw 'Номер аккаунта, ID проекта, имя оператора и регион должны быть заполнены.'
}
if ($UserName -eq 'otus-dz05-fence') { throw 'Для Terraform нужен оператор, а не пользователь fencing.' }
Write-Host "Проект: $ProjectId; регион: $Region; оператор: $UserName."
$taskPassword = Read-Host "Пароль сервисного пользователя $UserName (ввод скрыт)" -AsSecureString
if ($taskPassword.Length -eq 0) { $taskPassword.Dispose(); throw 'Пароль не должен быть пустым.' }
$taskPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($taskPassword)
try {
    $env:OS_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($taskPointer)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($taskPointer)
    $taskPassword.Dispose()
}
$env:OS_AUTH_URL = 'https://cloud.api.selcloud.ru/identity/v3'
$env:OS_IDENTITY_API_VERSION = '3'
$env:OS_USER_DOMAIN_NAME = $AccountNumber
$env:OS_PROJECT_ID = $ProjectId
$env:OS_USERNAME = $UserName
$env:OS_REGION_NAME = $Region
Write-Host 'Окружение Selectel готово. Пароль не записан в файл и не выведен.'
