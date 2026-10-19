# ДЗ05 — отказоустойчивый MySQL для портала OTUS

Выполнено: два независимых развёртывания в Selectel с проверкой отказов
PRIMARY и SECONDARY, работы Django и восстановления узлов. После каждого
тестового цикла ресурсы ДЗ05 удалены, включая неудачные попытки.
[Результаты и ограничения](docs/results.md) · [Журнал циклов](docs/test-runs.md).

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

Сначала выполнить [подготовку нового клона](docs/operations.md): собственные
Python/Terraform, скрытый ввод credentials, генерация ключей и tfvars, init.
Из корня репозитория команды выполняются раздельно:

```powershell
# Развёртывание; оставить стенд для проверки
.\.tools\venv\Scripts\python.exe scripts/lab.py deploy

# Проверки приложения, отказов двух ролей по очереди и возврата узлов
.\.tools\venv\Scripts\python.exe scripts/lab.py verify

# Удаление только собственного стенда и проверка отсутствия остатков
.\.tools\venv\Scripts\python.exe scripts/lab.py destroy
```

Для тестового запуска с обязательным удалением даже при ошибке:
` .\.tools\venv\Scripts\python.exe scripts/lab.py test-cycle `.
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
