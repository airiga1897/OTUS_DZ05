# Перенос данных

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

migrate создаёт contenttypes/permissions. loaddata разрешает natural foreign keys
и сбрасывает счётчики средствами backend Django. MySQL AUTO_INCREMENT необходимо
дополнительно проверить вставкой после наибольшего импортированного PK.
У MySQL нет PostgreSQL sequences: не выполнять setval и не импортировать pg_dump SQL.
После импорта сравнить записи и связи, выполнить вход admin, добавление статьи
и загрузку/чтение изображения через приложение. Media хранится на ВМ приложения,
не реплицируется MySQL; резервируется отдельно.

Если дамп отсутствует, выполнить seed_lessons на целевой БД. Для демонстрации
dumpdata/loaddata можно создать временную SQLite БД из исходников, заполнить
seed_lessons и экспортировать теми же параметрами. Это реконструкция тестовых
данных, а не перенос исторических данных PostgreSQL.
