This project is fork from yoyo-migrations
https://ollycope.com/software/yoyo/latest/


.env file:
    SOURCE=./migrations
    BATCH_MODE=on|off(default)
    VERBOSITY=0|1|2|3
    EDITOR=
    POST_CREATE_COMMAND=
    PREFIX=

    DB_DRIVER=pymssql
    DB_USER=
    DB_PASSWORD=
    DB_HOST=
    DB_PORT=
    DB_NAME=
    VERSION_TABLE=


Launch Commands:
    migrations  list
                new --sql -m 'example of a comment appended to a file name'
                apply
                rollback


You can add a comment to the migration in sql format migration files.
The comment will be saved in the migration history table.

-- comment: comment text...
