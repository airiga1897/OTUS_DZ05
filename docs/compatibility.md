# Проверка портала ДЗ04

Изучены models.py, 0001_initial.py, views.py, tests.py и seed_lessons.
Article использует BigAutoField, CharField, SlugField, PositiveSmallIntegerField,
TextField, ImageField, BooleanField, DateTimeField. Поля поддерживаются MySQL.
У Article и стандартных таблиц Django есть первичные ключи, необходимые Group Replication.
PostgreSQL ArrayField, distinct(fields), GIN/GiST и PostgreSQL-специфичных миграций нет.
Используется обычный ORM и SELECT 1 в readiness.

Для ДЗ05 заменён драйвер на mysqlclient, порт на Router 6446, кодировка utf8mb4,
strict SQL mode, isolation level READ COMMITTED. Соединения закрываются между
запросами, чтобы следующий запрос мог подключиться к новому PRIMARY.
Незавершённая транзакция при отказе может завершиться ошибкой: произвольные
записи автоматически не повторяются, чтобы избежать дубликатов.

Используется MySQL collation utf8mb4_0900_as_cs: это сохраняет различение
регистра уникальных slug, характерное для исходной PostgreSQL БД.
Создание схемы — manage.py migrate на PRIMARY через Router.
Пять тестов Django выполнены на MySQL; работа кластера подтверждена
[проверками приложения, отказов и восстановления](results.md).

При поиске в ДЗ04 (включая .local, исключая инструменты, .git и .terraform)
не найдены SQL-дампы, SQLite БД или прикладные JSON-фикстуры.
JSON-каталоги облака, tfvars и отчёты не являются данными портала.
seed_lessons содержит четыре исходных материала без вложений.
Четыре статьи восстановлены из seed_lessons, экспортированы dumpdata
и импортированы в MySQL через loaddata. Происхождение данных зафиксировано
в [отчёте реконструкции](reconstruction.json).
