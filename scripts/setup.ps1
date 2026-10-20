param([string]$TerraformPath)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
New-Item -ItemType Directory -Force (Join-Path $taskRoot '.tools') | Out-Null
New-Item -ItemType Directory -Force (Join-Path $taskRoot '.local/tmp') | Out-Null
$taskConfigPath = Join-Path $taskRoot '.local/lab-config.json'
if (-not (Test-Path -LiteralPath $taskConfigPath)) {
    Copy-Item -LiteralPath (Join-Path $taskRoot 'config/lab.example.json') -Destination $taskConfigPath
}
$taskPreviousTemp = $env:TEMP
$taskPreviousTmp = $env:TMP
$taskPreviousPipCache = $env:PIP_CACHE_DIR
try {
$env:TEMP = Join-Path $taskRoot '.local/tmp'
$env:TMP = $env:TEMP
$env:PIP_CACHE_DIR = Join-Path $taskRoot '.local/pip-cache'
python -m venv (Join-Path $taskRoot '.tools/venv')
if ($LASTEXITCODE) { throw 'Python venv creation failed.' }
& (Join-Path $taskRoot '.tools/venv/Scripts/python.exe') -m pip install -r (Join-Path $taskRoot 'requirements-local.txt')
if ($LASTEXITCODE) { throw 'Local dependency installation failed.' }
if ($TerraformPath) {
    Copy-Item -LiteralPath $TerraformPath -Destination (Join-Path $taskRoot '.tools/terraform.exe')
} else {
    $taskTerraform = (Get-Command terraform -ErrorAction Stop).Source
    Copy-Item -LiteralPath $taskTerraform -Destination (Join-Path $taskRoot '.tools/terraform.exe')
}
Write-Host 'Local Python and Terraform tools prepared; no cloud resources created.'
} finally {
    $env:TEMP = $taskPreviousTemp
    $env:TMP = $taskPreviousTmp
    $env:PIP_CACHE_DIR = $taskPreviousPipCache
}
