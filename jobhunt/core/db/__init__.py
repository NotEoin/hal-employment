"""The database layer of the rebuild: migrations (`migrate`), data tasks (`tasks`) and the schema files.

Connections are opened by `jobhunt.db.connect()`, which calls `migrate.migrate()` when the file is behind.
"""
