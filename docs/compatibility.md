# Проверка портала ДЗ04

Изучены models.py, 0001_initial.py, views.py, tests.py и начальная фикстура.
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

Начальные данные подготовлены по материалам ДЗ04 и загружаются Django-фикстурой.
Фикстура содержит пять статей без вложений; схема и стандартные права
создаются миграциями. [Загрузка и проверки](migration.md).
