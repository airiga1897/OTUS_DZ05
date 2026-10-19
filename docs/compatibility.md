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

MySQL collation следует выбрать utf8mb4_0900_as_cs: это сохраняет различение
регистра уникальных slug, характерное для исходной PostgreSQL БД.
Создание схемы — manage.py migrate на PRIMARY через Router.
Проверки SQLite подтверждают поведение приложения, но не работу MySQL/кластера.

При поиске в ДЗ04 (включая .local, исключая инструменты, .git и .terraform)
не найдены SQL-дампы, SQLite БД или прикладные JSON-фикстуры.
JSON-каталоги облака, tfvars и отчёты не являются данными портала.
seed_lessons содержит четыре исходных материала без вложений.
Старый стенд удалён. При отсутствии другого архива данные восстанавливаются
из seed_lessons, а не переносятся из действующей PostgreSQL БД.
