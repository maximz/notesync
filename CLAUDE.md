# NoteSync

Exports Granola meeting notes to markdown via their API. Source in `src/notesync/`, tests in `tests/`.

## Commands

- Run tests: `uv run pytest tests/ -v`
- Run single file: `uv run pytest tests/test_sync_migration.py -v`
- Run CLI: `uv run notesync <command>`

## Sync Database Schema Changes

The sync state lives in a SQLite DB (`.notesync-sync.db`). When adding or modifying columns in `synced_documents`:

1. Update the `CREATE TABLE` statement in `sync.py` `_init_database()`
2. Add an `ALTER TABLE ADD COLUMN` migration in `_init_database()` (after the PRAGMA check pattern)
3. Update `_row_to_sync_state()` and all SELECT queries to include the new column
4. Update `mark_synced()` and `mark_many_synced()` -- sync records use **dicts, not tuples**, to avoid positional field-mapping bugs
5. Add a migration round-trip test in `tests/test_sync_migration.py`:
   - Create a DB with the schema *before* your change (copy the previous `_create_vN_database` and keep it unchanged)
   - Insert a row with raw SQL using the old schema
   - Open via `SyncDatabase()` to trigger migration
   - Read the old row back -- assert the new column gets its default, other fields are intact
   - Write a new row via `mark_synced` and `mark_many_synced` with the new column populated
   - Read it back -- assert **every field** has the correct value **and type** (especially: integers are ints, timestamps are strings)
6. Run `uv run pytest tests/ -v` and confirm all tests pass before committing
