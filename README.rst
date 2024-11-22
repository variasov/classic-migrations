==================
classic-migrations
==================

Проект является форком yoyo-migrations
https://ollycope.com/software/yoyo/latest/

Компонент позволяет работать с миграциями баз данных posgtresql и mssql с использованием файлов sql

Установка
---------

При установке компонента для миграций в postgres, можно использовать команду

 pip install classic-migrations[postgres]

для MSSQL

 pip install classic-migrations[pymssql]

Настройка
---------

Все настройки берутся из переменных окружения или .env файла

.env file:

#папка с файлами миграций

SOURCE=./migrations

#пакетный режим исполнения, при котором нет диалоговых вопросов

BATCH_MODE=on|off(default)

#уровень вывода информационных сообщений

VERBOSITY=0|1|2|3

#настройки подключения к базе

#драйвер

DATABASE_DRIVER=pymssql|postgres|pyodbc

#имя пользователя БД, допустимо в формате <домен>\<пользователь>

DATABASE_USER=

#пароль

DATABASE_PASSWORD=

#хост

DATABASE_HOST=

#порт

DATABASE_PORT=

#имя БД

DATABASE_NAME=

Команды запуска
---------------

migrations list

migrations new --sql -m 'комментарий, который будет добавлен к имени файла'

migrations apply

migrations rollback

Для выполнения команды rollback, должен иметься .sql файл миграции, у которого между именем оригинального фала и расширением имеется вставка .rollback

Примечания
----------
В SQL файле миграции можно указать комментарий, который будет добавлен в таблицу истории миграций.

-- comment: текст комментария


