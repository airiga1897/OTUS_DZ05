# ДЗ05 — отказоустойчивый MySQL для портала OTUS

Выполнено: два независимых развёртывания в Selectel с проверкой отказов
PRIMARY и SECONDARY, работы Django и восстановления узлов. После каждого
тестового цикла ресурсы ДЗ05 удалены.
[Подтверждённые результаты и ограничения](docs/results.md).

Выбран MySQL InnoDB Cluster: три узла Group Replication в single-primary
и MySQL Router на отдельной ВМ с Django/uWSGI/Nginx. Большинство 2/3
переживает потерю одной ВМ БД; Router направляет запись на текущий PRIMARY.
AdminAPI автоматизирует создание и возврат узлов, приложение получает один
endpoint записи. [Документация MySQL](https://dev.mysql.com/doc/refman/8.0/en/mysql-innodb-cluster-introduction.html).

```text
HTTP:80 → app: Nginx → uWSGI → Django → Router 127.0.0.1:6446
                                        ↓
                       db1 ↔ db2 ↔ db3 (Group Replication)
```

Selectel ru-9a: три ВМ БД по 2 vCPU/4 ГиБ и приложение 1 vCPU/2 ГиБ,
по 20 ГБ SSD; один floating IP. БД распределены anti-affinity по хостам.
Префикс otus-dz05, сеть 10.55.0.0/24, отдельные state, ключи и security groups.
Одна зона и одна ВМ приложения/Router/media ограничивают область HA базой.
Согласованная оценка — 13,82 ₽/час: [состав, квоты и расчёт](docs/cost.md).
Перед deploy повторяется проверка общих квот с резервом ДЗ06;
ресурсы других домашних заданий не изменяются.

## Воспроизведение

Для нового клона сначала выполнить [подготовку окружения](docs/operations.md).
Для уже подготовленного каталога ДЗ05 использовать короткую последовательность
ниже. Команды выполнять по очереди в PowerShell из корня репозитория:

Подготовленный каталог содержит `.tools/venv/Scripts/python.exe`,
`.tools/terraform.exe`, `.local/otus_dz05` и `.local/otus_dz05.pub`,
`terraform-selectel/terraform.tfvars.json` и инициализированный Terraform.
Managed state должен быть пустым. Ввод credentials запрашивает недостающие
данные оператора; регион определяется автоматически. Реконструкция фикстуры,
проверка квот, установка ПО, миграции и импорт выполняются внутри deploy.

```powershell
# Ввести credentials
. .\scripts\selectel-env.ps1
.\scripts\save-operator.ps1

# Развернуть
.\.tools\venv\Scripts\python.exe scripts/lab.py deploy

# Проверить приложение, отказы и восстановление
.\.tools\venv\Scripts\python.exe scripts/lab.py verify

# Получить адрес портала и открыть браузер
$lab = Get-Content .\.local\lab.json -Raw | ConvertFrom-Json
$portalUrl = 'http://' + $lab.app_public_ip
$portalUrl
Start-Process $portalUrl
```

После проверки портала в браузере:

```powershell
# Удалить — также выполнить при ошибке deploy или verify
.\.tools\venv\Scripts\python.exe scripts/lab.py destroy
if ($LASTEXITCODE -ne 0) { throw 'Удаление не подтверждено; credentials и state сохранить для повторной очистки' }

# Только после успешного destroy удалить локальные credentials
Remove-Item -LiteralPath .\.local\operator.json
Remove-Item Env:OS_PASSWORD -ErrorAction SilentlyContinue
```

Если destroy завершился ошибкой, сохранить credentials и state до успешной очистки.
deploy требует пустой managed state; для созданного стенда использовать verify.
Настройку выполняют Python-скрипты; задание допускает их вместо Ansible.

## Данные и доказательства

- [Совместимость Django/MySQL](docs/compatibility.md).
- [Миграции, dumpdata/loaddata, права, идентификаторы и media](docs/migration.md).
- [Происхождение данных](docs/reconstruction.json): исторический дамп ДЗ04 не найден;
  четыре статьи восстановлены из исходников и импортированы в MySQL.
- [Порядок испытаний](docs/testing.md), [результаты](docs/results.md),
  [обезличенный журнал](docs/verification.log), [API-аудит удаления](docs/cleanup-latest.json).

В Git не включены credentials, ключи, реальные tfvars, Terraform state,
дампы и полные локальные журналы. `.local/operator.json` используется только
на рабочем месте на время выполнения и удаляется после завершения работы;
для повторного запуска потребуется скрытый ввод credentials по инструкции.
Проверка публикации: ` .\.tools\venv\Scripts\python.exe scripts/audit_submission.py `.

## Соответствие заданию

| Требование | Реализация и подтверждение |
|---|---|
| Отказоустойчивый MySQL | Три InnoDB Cluster узла, Router; [проверка отказов и возврата](docs/results.md) |
| БД веб-проекта и перенос данных | Django migrate/loaddata; [четыре статьи восстановлены из исходников](docs/migration.md), исторический дамп отсутствует |
| Автоматизация, инструкции и логи | [Terraform](terraform-selectel/), [скрипты](scripts/), [пошаговые команды](docs/operations.md), [журнал](docs/verification.log) |
| Сдача через Git | Код и обезличенные доказательства в ветке mysqlcluster, локальные секреты исключены |

## Компоненты и размещение данных

| Компонент | Назначение и данные |
|---|---|
| Nginx / uWSGI / Django на app | HTTP портала; статика и media в /srv/otus, код в /opt/otus-dz05 |
| MySQL Router на app | Endpoint записи localhost:6446, выбор текущего PRIMARY |
| db1/db2/db3 | MySQL на отдельных SSD, репликация БД otus; публичных IP нет |
| Selectel | Облачные ВМ и сеть; выбран по среде выполнения задания |

Граница отказоустойчивости — одна ВМ БД. Одна зона не защищает от отказа ЦОД;
app/Router/media остаются единой точкой отказа. Публичное чтение использует HTTP,
административная проверка — SSH-туннель. Потеря двух узлов лишает кластер кворума.

## Структура репозитория

- [app/](app/) — портал, модели, миграции и тесты Django.
- [terraform-selectel/](terraform-selectel/) — инфраструктура и пример параметров.
- [scripts/](scripts/) — подготовка, настройка, проверка и удаление.
- [config/](config/) — шаблон подписанной пробы сетевого маршрута.
- [docs/](docs/) — [инструкция с нуля](docs/operations.md), перенос данных и доказательства.

Локальные .tools/ и .local/ создаются при подготовке и исключены из Git.
