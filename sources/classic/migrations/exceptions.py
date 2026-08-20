class BadConnectionURI(Exception):
    """
    An invalid connection URI
    """


class BadMigration(Exception):
    """
    The migration file could not be compiled
    """


class MigrationConflict(Exception):
    """
    The migration id conflicts with another migration
    """


class MigrationHashMismatch(Exception):
    """
    One or more already-applied migrations have been modified since they
    were applied.
    """

    def __init__(self, changed):
        self.changed = list(changed)
        super().__init__(
            "Migrations have changed since they were applied: {}".format(
                ", ".join(self.changed)
            )
        )
