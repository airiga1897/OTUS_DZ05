# Перенос данных

## Выполненная загрузка

В выполненном развёртывании используется [reconstruct.py](../scripts/reconstruct.py):
временная SQLite БД создаётся миграциями, заполняется seed_lessons и экспортируется
dumpdata. deploy автоматически вызывает этот сценарий, если локальной фикстуры
ещё нет. Затем app.py выполняет migrate и loaddata на MySQL через Router.
Это реконструкция четырёх исходных статей, а не перенос исторических данных
PostgreSQL: исторический дамп не найден. Исходных media — 0; загрузка нового
изображения и его выдача проверены через admin. [Фактические результаты](results.md).

## Экспорт при наличии сохранённого источника

Следующие команды описывают перенос собственного архива. Они выполняются
на Linux-сервере источника из каталога Django с загруженным окружением PostgreSQL,
затем на целевом сервере из каталога Django с окружением MySQL. Каталог /secure
должен быть создан заранее с доступом только оператору; его путь можно заменить.
Этот порядок не требуется для обычного deploy, который загружает восстановленную фикстуру.

Дампы, media и пароли не коммитить. Остановить запись в источник перед экспортом.
На сохранённой PostgreSQL БД с исходным приложением:

```bash
python manage.py dumpdata --natural-foreign --exclude contenttypes --exclude auth.permission --exclude sessions --indent 2 --output /secure/portal.json
tar -C /srv/otus/media -czf /secure/media.tar.gz .
```

Сохраняются PK пользователей и статей; ссылки на contenttypes и permissions
передаются natural keys. Не использовать --natural-primary для пользователей:
нужно сохранить числовые ссылки admin.LogEntry. Сессии не переносить.
До экспорта зафиксировать число строк, PK и контрольные суммы данных/media.
Проверить уникальность slug с целевой collation, длины строк, временные зоны
и существование каждого файла ImageField.

На пустой целевой БД с utf8mb4_0900_as_cs:

```bash
python manage.py migrate --noinput
python manage.py loaddata /secure/portal.json
tar -C /srv/otus/media -xzf /secure/media.tar.gz
python manage.py verify_database
```

migrate создаёт contenttypes/permissions. loaddata разрешает natural foreign keys.
В MySQL вставка явных PK продвигает AUTO_INCREMENT; корректность следующего
идентификатора проверяется вставкой после наибольшего импортированного PK.
У MySQL нет PostgreSQL sequences: не выполнять setval и не импортировать pg_dump SQL.
После импорта сравнить записи и связи, выполнить вход admin, добавление статьи
и загрузку/чтение изображения через приложение. Media хранится на ВМ приложения,
не реплицируется MySQL; резервируется отдельно.
