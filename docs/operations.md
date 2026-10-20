# Самостоятельная проверка с нуля

Все команды ниже выполняются **на локальном компьютере Windows в PowerShell**,
из корня клона ДЗ05. Команды на сервере автоматически выполняют существующие
Python-скрипты через SSH. Доступ к исходным каталогам других ДЗ не требуется.
Обычный deploy оставляет сервис работающим; удаление выполняется отдельным шагом.

## 1. Инструменты и облачная учётная запись

Установить Git, Python 3.12+ с командой `python` в PATH, Terraform 1.6–1.x
из доверенных дистрибутивов. Проверенная комбинация: Python 3.14.2,
Terraform 1.16.3, Windows PowerShell. На ВМ используется Ubuntu 24.04.

В панели Selectel подготовить проект и сервисного пользователя оператора.
Пользователь должен иметь доступ к этому проекту и права чтения данных и каталогов API
Nova, Neutron, Cinder, создания/удаления ВМ, дисков, сетей, роутера, floating IP,
security groups, server groups, чтения консоли ВМ и остановки/запуска ВМ.
Ограниченный пользователь для fencing для этого не подходит. Назначение прав
выполняется администратором аккаунта в панели. Программные проверки имён/state
дополняют облачные права, но не ограничивают полномочия учётной записи в Selectel.

Значения для скрытого ввода:

| Значение | Где получить | Кто задаёт |
|---|---|---|
| AccountNumber | Номер аккаунта в меню панели Selectel; не email | Пользователь |
| ProjectId | UUID проекта в разделе облачных серверов | Пользователь |
| UserName и пароль | Созданный сервисный пользователь, его пароль | Пользователь |
| Region | По умолчанию `ru-9`, зона ресурсов `ru-9a` | Автоматически, необязательный `-Region` для переопределения |
| Image/flavor IDs, SSD type | Каталоги API | Автоматически prepare.py |
| Ключ SSH, секреты приложения, адрес ВМ | Генерируются/получаются сценариями | Автоматически |

Конфигурация проверена в проекте OTUS
`fdce7ecd9cf742a6a4eda91e927a09fb`. Доступ проверяющего должен быть выдан
отдельному оператору; пароль автора не требуется. **Для собственного проекта**
перед запуском заменить только этот ожидаемый UUID на UUID своего проекта
в трёх проверках: [prepare.py](../scripts/prepare.py),
[preflight.py](../scripts/preflight.py), [tf.py](../scripts/tf.py).
Это необходимая локальная адаптация ограничения текущей реализации, не отключение
проверок. Регион ru-9, префикс otus-dz05 и подсеть 10.55.0.0/24 сохранить.
Одновременно запускать два клона ДЗ05 в одном проекте нельзя.

## 2. Клонирование и локальное окружение — однократно

Каталог клона выбирается самостоятельно; персональные пути не требуются:

```powershell
git clone --branch mysqlcluster https://github.com/airiga1897/OTUS_DZ05.git
Set-Location .\OTUS_DZ05
python --version
terraform version
git --version
.\scripts\setup.ps1
if ($LASTEXITCODE -ne 0) { throw 'Ошибка подготовки окружения' }
```

Если Terraform отсутствует в PATH, вместо последней команды указать путь
к установленному бинарному файлу: ` .\scripts\setup.ps1 -TerraformPath C:\Tools\terraform.exe `.
Сценарий создаёт собственные `.tools/venv` и `.tools/terraform.exe`, устанавливает
[локальные зависимости](../requirements-local.txt). Ожидается
`Local Python and Terraform tools prepared; no cloud resources created.`
Если политика PowerShell блокирует скрипты, использовать разрешённую организацией
политику запуска локальных проверенных скриптов; не менять системную политику вслепую.

## 3. Авторизация и параметры — до создания платных ресурсов

```powershell
$accountNumber = Read-Host 'Номер аккаунта Selectel'
$projectId = Read-Host 'UUID проекта Selectel'
$operatorName = Read-Host 'Имя сервисного пользователя оператора'
. .\scripts\selectel-env.ps1 -AccountNumber $accountNumber -ProjectId $projectId -UserName $operatorName
.\scripts\save-operator.ps1
```

Регион вводить не требуется: используется значение из существующего tfvars,
затем из локального discovery; если оба отсутствуют — `ru-9`.
Явный `-Region` имеет приоритет, но должен соответствовать сохранённой
конфигурации. Другой регион требует также адаптации Terraform и проверок
prepare/preflight/tf: одного аргумента недостаточно.

Пароль вводится скрыто при selectel-env.ps1. save-operator.ps1 сохраняет его
в `.local/operator.json` с ограничением доступа текущему Windows-пользователю;
каталог исключён из Git. Не выводить файл в терминал и не публиковать.
Состав стенда приведён в [описании ресурсов](resources.md).
deploy автоматически проверяет квоты и текущее потребление проекта перед
созданием ресурсов; отдельная ручная проверка не требуется.
При HTTP 403 проверить права оператора на соответствующий API Selectel.

```powershell
.\.tools\venv\Scripts\python.exe scripts/prepare.py
if ($LASTEXITCODE -ne 0) { throw 'Параметры не подготовлены' }
.\.tools\venv\Scripts\python.exe scripts/tf.py init '-input=false'
if ($LASTEXITCODE -ne 0) { throw 'Ошибка Terraform init' }
.\.tools\venv\Scripts\python.exe scripts/tf.py validate
if ($LASTEXITCODE -ne 0) { throw 'Ошибка конфигурации Terraform' }
```

prepare.py создаёт `.local/otus_dz05`, публичный ключ и
`terraform-selectel/terraform.tfvars.json`, определяет Ubuntu image, flavors,
SSD и IPv4 оператора. Платные ресурсы ещё не создаются. Повторный prepare
не перезаписывает существующие tfvars: при повторном запуске сохранённого
клона проверить параметры и пропустить эту команду. Не создавать одновременно
terraform.tfvars и terraform.tfvars.json. Безопасный формат показан в
[примере](../terraform-selectel/terraform.tfvars.example).

SSH снаружи использует TCP/443, внутри — TCP/22. Разрешены отдельные IPv4 /32.
При первом prepare можно передать несколько `--admin-cidr ADDRESS/32`.
HTTPS-проверка адреса может отличаться от прямого маршрута: deploy использует
временную HTTP-пробу с подписью nonce, добавляет подтверждённый /32 и выключает
пробу перед Nginx. Доступ SSH на /0 не открывается. init использует
[зеркало провайдера](../terraform-selectel.rc). Успешный validate подтверждает
только конфигурацию, не работу кластера.

## 4. Развёртывание — создаёт платные ресурсы

```powershell
.\.tools\venv\Scripts\python.exe scripts/lab.py deploy
if ($LASTEXITCODE -ne 0) { throw 'Развёртывание не завершено; выполнить удаление по шагу 7' }
```

Ожидаются три ONLINE в статусе кластера, `Installed 4 object(s)`,
`CRUD PASS`, пять успешных тестов Django на MySQL и `APPLICATION READY`.
Сценарий создаёт инфраструктуру, настраивает MySQL/Router, схему Django,
импортирует данные, запускает uWSGI и Nginx. Сервис остаётся работать.
deploy требует пустого managed state; для работающего стенда использовать verify.
Не выполнять второй deploy поверх частично созданного стенда: сначала destroy.

При отсутствии исторического экспорта четыре статьи восстанавливаются
из исходного seed через SQLite → dumpdata → migrate/loaddata на MySQL.
Это не перенос исторической PostgreSQL БД. [Данные и media](migration.md).

## 5. Проверка портала без остановки узлов

```powershell
$lab = Get-Content .\.local\lab.json -Raw | ConvertFrom-Json
$portalUrl = 'http://' + $lab.app_public_ip
foreach ($path in @('/', '/articles/high-load/', '/health/ready', '/static/knowledge/site.css')) {
    $response = Invoke-WebRequest -Uri ($portalUrl + $path) -UseBasicParsing
    '{0}: {1}' -f $path, $response.StatusCode
}
Start-Process $portalUrl
```

Ожидается HTTP 200 для всех четырёх адресов. В браузере проверить список
четырёх материалов и открытие статьи. `/health/ready` проверяет доступность
БД через Router. Это публичное чтение HTTP; административный пароль по внешнему
HTTP не вводить. Автоматический verify выполняет вход admin, запись статьи
и загрузку изображения через HTTP внутри доверенного SSH-туннеля.

## 6. Проверка отказоустойчивости — временно выключает ВМ БД

```powershell
.\.tools\venv\Scripts\python.exe scripts/lab.py verify
if ($LASTEXITCODE -ne 0) { throw 'Проверка не пройдена; изучить отчёт и выполнить шаг 7' }
$reportFile = Get-ChildItem .\docs\verification-*.json | Sort-Object Name | Select-Object -Last 1
$report = Get-Content $reportFile.FullName -Raw | ConvertFrom-Json
$report.success
$report.failures | Select-Object stopped_role, node, surviving_members, recovered_members
$report.migration
$report.admin_http
$report.replica_snapshots
$report.final_cleanup
```

Ожидаются `LIVE VERIFICATION PASSED` и success=true. Назначение проверок:

| Проверка verify | Ожидаемое поведение |
|---|---|
| Исходный статус | Три ONLINE, один PRIMARY и два SECONDARY |
| Импорт | Совпали все поля четырёх статей, новые PK выше импортированных |
| Права и admin | Работают contenttypes/permissions, связи user/group, вход и создание статьи |
| Media | Загрузка/чтение изображения, совпадение байтов по SHA256 |
| Отказ PRIMARY | UUID из собственного state выключается; два оставшихся узла обслуживают чтение и запись через тот же Router |
| Возврат PRIMARY | ВМ запускается в finally; вновь три ONLINE |
| Отказ SECONDARY | Запись и HTTP продолжают работать на большинстве; затем три ONLINE |
| Сохранность | Подтверждённые записи сохраняются между отказами, статьи на трёх репликах совпадают |
| Завершение | Тестовые статьи, редактор и группа удалены; остаются четыре исходные статьи |

Краткий 503 при смене PRIMARY допустим и фиксируется в readiness_samples;
нулевой простой не заявляется. Force quorum и полная переинициализация
не используются. Испытание предназначено для свежего учебного стенда с четырьмя
исходными статьями; пользовательские данные перед проверкой не добавлять.
Новый отчёт сохраняется в docs; существующие отчёты — результаты предыдущего
выполнения, не доказательство текущего запуска. [Подтверждённые результаты](results.md).

## 7. Удаление — необратимо удаляет БД, media и ВМ стенда

После проверки, а также после неудачного deploy/verify:

```powershell
.\.tools\venv\Scripts\python.exe scripts/lab.py destroy
if ($LASTEXITCODE -ne 0) { throw 'Удаление не подтверждено; credentials и state сохранить для повторной очистки' }
.\.tools\venv\Scripts\python.exe scripts/tf.py state list
if ($LASTEXITCODE -ne 0) { throw 'Не удалось проверить state' }
$cleanup = Get-Content .\docs\cleanup-latest.json -Raw | ConvertFrom-Json
$cleanup.own_state_empty
$cleanup.remaining_by_prefix
if (-not $cleanup.own_state_empty) { throw 'State не пуст' }
if (@($cleanup.remaining_by_prefix.PSObject.Properties | Where-Object { $_.Value -ne 0 }).Count -ne 0) { throw 'Остались ресурсы ДЗ05' }
```

Ожидается `DZ05 cleanup: own Terraform state has no managed resources.`,
пустой вывод state list, own_state_empty=true и нули во всех remaining_by_prefix,
включая own_floating_ips. Проверить свежую time_utc отчёта. destroy проверяет
план удаления собственного state и затем API-инвентарь с префиксом ДЗ05.
Чужие ресурсы не удаляются. Остановка ВМ не заменяет destroy: диски и IP платные.
Если cleanup завершился ошибкой, не удалять state и ключи; устранить причину
и повторить destroy из этого же клона. Не использовать cleanup других проектов.

После подтверждения удаления очистить локальную копию credentials и окружение:

```powershell
Remove-Item -LiteralPath .\.local\operator.json
Remove-Item Env:OS_PASSWORD -ErrorAction SilentlyContinue
Test-Path -LiteralPath .\.local\operator.json
```

Ожидается False. Для следующего запуска повторить скрытый ввод и save-operator;
при сохранённых ключах/tfvars шаг prepare пропустить. State, ключи, дампы и
полные `.local/terraform.log`, `.local/remote.log` не публиковать.

## Дополнительные проверки

Перед публикацией выполнить существующий аудит:

```powershell
.\.tools\venv\Scripts\python.exe scripts/audit_submission.py
git diff --check
git status --short
```

Аудит проверяет Git-кандидаты и известные локальные значения секретов;
для сравнения с паролем запускать до удаления operator.json. Он не доказывает
отсутствие всех возможных секретов во всей истории Git: историю перед публикацией
также следует проверять. Проверенные отчёты обезличены.

`lab.py test-cycle` предназначен для автоматических испытаний воспроизводимости:
deploy → verify → destroy в finally, включая ошибки. Он не используется вместо
обычной последовательности запуска с оставленным для проверки сервисом.
