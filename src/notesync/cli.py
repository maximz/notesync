"""
Command-line interface for NoteSync.
"""

import re
import sys
from pathlib import Path
from typing import List, Optional

import click
from rich.console import Console
from rich.table import Table

from . import __version__
from .api import GranolaAPI
from .auth import GranolaAccount, GranolaAuth
from .export import ExportEngine
from .sync import SYNC_DB_FILENAME, SyncDatabase


console = Console()
# Errors go to stderr so shell wrappers (and cron capture) can distinguish
# failure output from normal progress reporting.
err_console = Console(stderr=True)


def _account_subdir(account: GranolaAccount) -> str:
    """
    Map an account email to a filesystem-safe subdirectory name. Used as the
    per-account directory inside the user-provided output_dir so notes from
    different Granola accounts don't collide.
    """
    return re.sub(r"[^a-z0-9]+", "_", account.email.lower()).strip("_") or "account"


def _resolve_accounts(account: Optional[str]) -> List[GranolaAccount]:
    """
    Load every Granola account on disk and, if --account was given, filter to
    a single match (case-insensitive on email, whitespace-tolerant). Prints a
    user-facing error to stderr and exits non-zero on any failure or no-match;
    never returns empty.
    """
    try:
        accounts = GranolaAuth.list_accounts()
    except (FileNotFoundError, ValueError) as e:
        err_console.print(f"[bold red]Error: {e}[/bold red]")
        sys.exit(1)

    if not accounts:
        err_console.print("[bold red]Error: No Granola accounts found.[/bold red]")
        console.print("[yellow]Make sure Granola is installed and you're logged in.[/yellow]")
        sys.exit(1)

    if account is None:
        return accounts

    needle = account.strip().lower()
    matches = [a for a in accounts if a.email.strip().lower() == needle]
    if not matches:
        available = ", ".join(a.email for a in accounts)
        err_console.print(
            f"[bold red]Error: No account matching '{account}'. Available: {available}[/bold red]"
        )
        sys.exit(1)
    return matches


def _check_subdir_collisions(accounts: List[GranolaAccount]) -> None:
    """
    Two accounts whose sanitized-email subdirs collide would silently share a
    sync DB — leaking sync state between identities. Refuse to start in that
    case rather than corrupt either tree.
    """
    by_subdir: dict = {}
    for acc in accounts:
        by_subdir.setdefault(_account_subdir(acc), []).append(acc.email)
    collisions = [(sub, emails) for sub, emails in by_subdir.items() if len(emails) > 1]
    if not collisions:
        return
    lines = [f"  {sub}/  ← {', '.join(emails)}" for sub, emails in collisions]
    err_console.print(
        "[bold red]Error: account email collision after sanitization.[/bold red]\n"
        "Two or more accounts would share the same per-account subdirectory:\n"
        + "\n".join(lines)
        + "\nUse --account <email> to sync them one at a time, or rename one upstream."
    )
    sys.exit(1)


@click.group()
@click.version_option(version=__version__, prog_name="notesync")
def cli():
    """
    NoteSync - Export and sync Granola notes and transcripts to markdown files.

    Export your Granola meeting notes to disk with incremental sync support.
    Perfect for backing up notes, searching with local tools, or integrating with other systems.
    """
    pass


@cli.command()
@click.option(
    "--json",
    "output_json",
    is_flag=True,
    help="Output as JSON (for scripting).",
)
def accounts(output_json: bool):
    """
    List Granola accounts NoteSync can authenticate as.

    Reads `stored-accounts.json` and `supabase.json` from Granola's config
    and prints one row per account: the email, the source file, and the
    sanitized subdirectory name `sync` would use for it under OUTPUT_DIR.
    Useful for figuring out the exact value for `--account`.
    """
    all_accounts = _resolve_accounts(None)

    if output_json:
        import json
        click.echo(
            json.dumps(
                {
                    "count": len(all_accounts),
                    "accounts": [
                        {
                            "email": a.email,
                            "user_id": a.user_id,
                            "source": a.source,
                            "subdir": _account_subdir(a),
                        }
                        for a in all_accounts
                    ],
                }
            )
        )
        sys.exit(0)

    table = Table(title=f"Granola accounts ({len(all_accounts)})")
    table.add_column("Email", style="cyan")
    table.add_column("Subdir", style="green")
    table.add_column("Source", style="magenta")
    table.add_column("User ID", style="dim")
    for acc in all_accounts:
        table.add_row(acc.email, _account_subdir(acc), acc.source, acc.user_id or "—")
    console.print(table)
    sys.exit(0)


@cli.command()
@click.argument(
    "output_dir",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    required=True,
)
@click.option(
    "--force",
    is_flag=True,
    help="Re-export all notes, ignoring sync state (overwrites existing files)",
)
@click.option(
    "--since",
    type=int,
    help="Force re-export notes updated in the last N days (e.g., --since 7 for last week)",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would be synced without actually writing files",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Show detailed logging for each note",
)
@click.option(
    "--debug",
    is_flag=True,
    help="Show debug output for markdown conversion",
)
@click.option(
    "--account",
    type=str,
    default=None,
    help="Sync only the account with this email (default: sync every Granola account).",
)
def sync(
    output_dir: Path,
    force: bool,
    since: int,
    dry_run: bool,
    verbose: bool,
    debug: bool,
    account: Optional[str],
):
    """
    Sync Granola notes to a local directory.

    OUTPUT_DIR: Base directory to export notes to. Each Granola account gets
    its own subdirectory (e.g. OUTPUT_DIR/<email>/) so multi-account users
    don't collide. Will be created if it doesn't exist.

    Examples:

      \b
      # Sync all accounts
      notesync sync ~/Documents/notesync-notes

      \b
      # Sync only one account
      notesync sync ~/Documents/notesync-notes --account user@example.com

      \b
      # Force re-export all notes
      notesync sync ~/Documents/notesync-notes --force

      \b
      # Re-export notes from the last 7 days
      notesync sync ~/Documents/notesync-notes --since 7

      \b
      # Preview what would be synced
      notesync sync ~/Documents/notesync-notes --dry-run

    The sync command:
    - Organizes notes by account, then Granola folder structure
    - Uses timestamp-prefixed filenames (YYYYMMDD_HHMM_Title_abc12345.md)
    - Includes user notes, AI-generated panels, and transcripts
    - Tracks sync state per account to avoid re-exporting unchanged notes
    """
    try:
        accounts_to_sync = _resolve_accounts(account)
        _check_subdir_collisions(accounts_to_sync)

        # Refuse to run against a legacy single-account layout. Notes synced
        # before the multi-account migration live at output_dir root with a
        # sibling .notesync-sync.db; if we silently started writing to a
        # subdir alongside them, those 800+ files would orphan from the DB
        # and get re-exported into a new tree (data duplication, git churn,
        # possible cross-account contamination on the next run).
        legacy_db = output_dir / SYNC_DB_FILENAME
        if legacy_db.exists():
            email_list = "\n".join(f"  - {a.email}" for a in accounts_to_sync)
            err_console.print(
                "[bold red]Error: legacy single-account layout detected.[/bold red]\n"
                f"Found {legacy_db}. NoteSync now writes per-account subdirectories\n"
                "to support multiple Granola accounts in one OUTPUT_DIR.\n\n"
                "Migrate before re-running. Pick the email that owns the existing notes\n"
                "(typically your historical Granola account), then move everything into\n"
                f"OUTPUT_DIR/<that-email-sanitized>/. Detected accounts:\n{email_list}\n\n"
                "Example (zsh/bash) — adjust <email> to the correct subdir name:\n"
                f"  cd {output_dir}\n"
                "  mkdir <email>\n"
                "  for f in *; do [ \"$f\" = \"<email>\" ] || mv \"$f\" \"<email>/\"; done\n"
                f"  mv {SYNC_DB_FILENAME} <email>/"
            )
            sys.exit(1)

        # Per-account isolation: one account's failure shouldn't abort the
        # others (common case: one stale token in a multi-account setup).
        # Exit non-zero whenever *any* account fails so the wrapper's alert
        # path fires; a complete outage is just the worst case of that.
        failures: list = []
        for acc in accounts_to_sync:
            subdir = output_dir / _account_subdir(acc)
            console.print(
                f"[bold blue]── Syncing account: {acc.email} → {subdir} ──[/bold blue]"
            )
            try:
                engine = ExportEngine(api=GranolaAPI(access_token=acc.access_token))
                engine.sync_all_notes(
                    output_dir=str(subdir),
                    force=force,
                    dry_run=dry_run,
                    verbose=verbose,
                    debug=debug,
                    since=since,
                )
            except KeyboardInterrupt:
                raise
            except Exception as e:
                failures.append((acc.email, e))
                err_console.print(
                    f"[bold red]Error syncing {acc.email}: {e}[/bold red]"
                )
                if verbose:
                    import traceback
                    console.print(f"[dim]{traceback.format_exc()}[/dim]")

        if failures:
            err_console.print(
                f"[bold red]{len(failures)}/{len(accounts_to_sync)} account(s) failed:[/bold red] "
                + ", ".join(email for email, _ in failures)
            )
        sys.exit(0 if not failures else 1)

    except KeyboardInterrupt:
        console.print("\n[yellow]Sync interrupted by user[/yellow]")
        sys.exit(130)
    except Exception as e:
        err_console.print(f"\n[bold red]Error: {e}[/bold red]")
        if verbose:
            import traceback
            console.print(f"[dim]{traceback.format_exc()}[/dim]")
        sys.exit(1)


@cli.command("list-folders")
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Show detailed information about each folder",
)
@click.option(
    "--account",
    type=str,
    default=None,
    help="Only show folders from this account (default: all accounts).",
)
def list_folders(verbose: bool, account: Optional[str]):
    """
    List all Granola folders.

    Shows your Granola folders with document counts and metadata. With
    multiple accounts on disk, lists each account's folders in turn.
    """
    try:
        accounts = _resolve_accounts(account)

        for idx, acc in enumerate(accounts):
            if len(accounts) > 1:
                if idx > 0:
                    console.print()
                console.print(f"[bold blue]── Account: {acc.email} ──[/bold blue]")

            api = GranolaAPI(access_token=acc.access_token)
            console.print("[blue]Fetching folders from Granola...[/blue]")
            folders_response = api.get_folders()
            folders = list(folders_response.lists.values())

            if not folders:
                console.print("[yellow]No folders found[/yellow]")
                continue

            folders.sort(key=lambda f: f.title)

            table = Table(title=f"Granola Folders ({len(folders)} total)")
            table.add_column("Title", style="cyan", no_wrap=False)
            table.add_column("Documents", justify="right", style="green")
            table.add_column("Updated", style="yellow")
            if verbose:
                table.add_column("Visibility", style="magenta")
                table.add_column("Shared", style="blue")

            for folder in folders:
                doc_count = len(folder.document_ids) if folder.document_ids else 0
                updated = folder.updated_at[:10] if folder.updated_at else "N/A"

                row = [
                    folder.title,
                    str(doc_count),
                    updated,
                ]

                if verbose:
                    row.append(folder.visibility)
                    row.append("Yes" if folder.is_shared else "No")

                table.add_row(*row)

            console.print(table)
        sys.exit(0)

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
        sys.exit(130)
    except Exception as e:
        err_console.print(f"\n[bold red]Error: {e}[/bold red]")
        sys.exit(1)


@cli.command("list-notes")
@click.option(
    "--folder",
    help="Filter by folder name (case-insensitive partial match)",
)
@click.option(
    "--limit",
    type=int,
    default=50,
    help="Maximum number of notes to display (default: 50)",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Show detailed information about each note",
)
@click.option(
    "--account",
    type=str,
    default=None,
    help="Only show notes from this account (default: all accounts).",
)
def list_notes(folder: str, limit: int, verbose: bool, account: Optional[str]):
    """
    List Granola notes.

    Shows your Granola notes with metadata. Optionally filter by folder.
    With multiple accounts on disk, lists each account's notes in turn.

    Examples:

      \b
      # List recent notes across all accounts
      notesync list-notes

      \b
      # List notes in a specific folder
      notesync list-notes --folder "Team Meetings"

      \b
      # List notes from one account
      notesync list-notes --account user@example.com
    """
    try:
        accounts = _resolve_accounts(account)

        for idx, acc in enumerate(accounts):
            if len(accounts) > 1:
                if idx > 0:
                    console.print()
                console.print(f"[bold blue]── Account: {acc.email} ──[/bold blue]")

            api = GranolaAPI(access_token=acc.access_token)
            console.print("[blue]Fetching notes from Granola...[/blue]")
            response = api.get_documents()
            documents = response.docs

            folder_filter = folder.lower() if folder else None
            if folder_filter:
                folders_response = api.get_folders()
                folders = folders_response.lists

                matching_folder_ids = set()
                for folder_obj in folders.values():
                    if folder_filter in folder_obj.title.lower():
                        matching_folder_ids.update(folder_obj.document_ids)

                documents = [doc for doc in documents if doc.id in matching_folder_ids]

                if not documents:
                    console.print(f"[yellow]No notes found in folders matching '{folder}'[/yellow]")
                    continue

            documents.sort(key=lambda d: d.updated_at, reverse=True)
            total = len(documents)
            documents = documents[:limit]

            title_text = f"Granola Notes ({len(documents)}"
            if folder:
                title_text += f" in folders matching '{folder}'"
            title_text += ")"

            table = Table(title=title_text)
            table.add_column("Title", style="cyan", no_wrap=False, max_width=50)
            table.add_column("Created", style="green")
            table.add_column("Updated", style="yellow")
            if verbose:
                table.add_column("Source", style="magenta")
                table.add_column("ID", style="dim")

            for doc in documents:
                created = doc.created_at[:10] if doc.created_at else "N/A"
                updated = doc.updated_at[:10] if doc.updated_at else "N/A"

                row = [
                    doc.title[:50],
                    created,
                    updated,
                ]

                if verbose:
                    row.append(doc.creation_source)
                    row.append(doc.id[:8])

                table.add_row(*row)

            console.print(table)

            if total > limit:
                console.print(
                    f"\n[dim]Showing {limit} of {total} total notes. Use --limit to see more.[/dim]"
                )

        sys.exit(0)

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
        sys.exit(130)
    except Exception as e:
        err_console.print(f"\n[bold red]Error: {e}[/bold red]")
        sys.exit(1)


@cli.command()
@click.argument("file_path", type=str)
@click.option(
    "--output-dir",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    help=f"Output directory where notes are synced (contains {SYNC_DB_FILENAME}). If not specified, looks for {SYNC_DB_FILENAME} in parent directories.",
)
@click.option(
    "--delete-file",
    is_flag=True,
    help="Also delete the markdown file from disk",
)
def forget(file_path: str, output_dir: Path, delete_file: bool):
    """
    Remove a note from sync state to allow re-syncing.

    FILE_PATH: Path to the note file (e.g., Uncategorized/20240101_2100.Meeting_Title.7ab123dd.md)

    This command removes a note from the sync database, allowing it to be re-synced
    on the next run. Useful for testing or when you want to regenerate a specific note.

    Each Granola account has its own .notesync-sync.db under
    OUTPUT_DIR/<account-email>/, so --output-dir should point at the
    per-account subdirectory (not the base OUTPUT_DIR used by `notesync sync`).

    Examples:

      \b
      # Forget a note in a specific account's tree (keeps the file)
      notesync forget "Uncategorized/20240101_2100.Meeting.7ab123dd.md" \
          --output-dir ~/Documents/notesync-notes/user_example_com

      \b
      # Forget a note and delete the file
      notesync forget "Uncategorized/20240101_2100.Meeting.7ab123dd.md" \
          --output-dir ~/Documents/notesync-notes/user_example_com --delete-file

      \b
      # Auto-detect from current directory (must be inside the per-account tree)
      cd ~/Documents/notesync-notes/user_example_com
      notesync forget "Uncategorized/20240101_2100.Meeting.7ab123dd.md"
    """
    try:
        # Find the sync database
        db_path = None
        if output_dir:
            output_dir = output_dir.expanduser()
            db_path = output_dir / SYNC_DB_FILENAME
            if not db_path.exists():
                err_console.print(f"[bold red]Error: Sync database not found at {db_path}[/bold red]")
                console.print("[yellow]Make sure you've run 'notesync sync' at least once in this directory.[/yellow]")
                sys.exit(1)
        else:
            # Search for sync DB in current directory and parents.
            current = Path.cwd()
            while current != current.parent:
                potential_db = current / SYNC_DB_FILENAME
                if potential_db.exists():
                    db_path = potential_db
                    output_dir = current
                    break
                current = current.parent

            if not db_path:
                err_console.print(f"[bold red]Error: Could not find {SYNC_DB_FILENAME}[/bold red]")
                console.print(
                    f"[yellow]Please specify --output-dir or run from a directory containing {SYNC_DB_FILENAME}[/yellow]"
                )
                sys.exit(1)

        output_root = output_dir.resolve()

        # Open sync database
        sync_db = SyncDatabase(str(db_path))

        # Look up the document by file path
        sync_state = sync_db.get_sync_state_by_path(file_path)

        if not sync_state:
            console.print(f"[yellow]Note not found in sync database: {file_path}[/yellow]")
            console.print("[dim]The note may not have been synced yet, or the path might be incorrect.[/dim]")
            sys.exit(1)

        # Display what we found
        console.print(f"[cyan]Found note:[/cyan] {sync_state.title}")
        console.print(f"[dim]Document ID: {sync_state.doc_id[:8]}[/dim]")
        console.print(f"[dim]File path: {sync_state.file_path}[/dim]")
        console.print(f"[dim]Last synced: {sync_state.synced_at}[/dim]")

        # Remove from database
        sync_db.remove_synced_document(sync_state.doc_id)
        console.print("[green]✓ Removed from sync database[/green]")

        # Delete file if requested
        if delete_file:
            stored_path = Path(sync_state.file_path)
            if stored_path.is_absolute():
                file_to_delete = stored_path.resolve()
            else:
                file_to_delete = (output_root / stored_path).resolve()

            # Safety guard: never delete outside the configured output directory.
            try:
                file_to_delete.relative_to(output_root)
            except ValueError:
                raise ValueError(
                    f"Refusing to delete file outside output directory: {file_to_delete}"
                )

            if file_to_delete.exists():
                file_to_delete.unlink()
                console.print(f"[green]✓ Deleted file: {file_to_delete}[/green]")
            else:
                console.print(f"[yellow]Warning: File not found at {file_to_delete}[/yellow]")

        console.print("\n[blue]This note will be re-synced on the next 'notesync sync' run.[/blue]")
        sys.exit(0)

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
        sys.exit(130)
    except Exception as e:
        err_console.print(f"\n[bold red]Error: {e}[/bold red]")
        sys.exit(1)


@cli.command()
@click.option(
    "--since",
    type=int,
    default=30,
    help="Only check meetings from the last N days (default: 30)",
)
@click.option(
    "--json",
    "output_json",
    is_flag=True,
    help="Output as JSON (for scripting)",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Show detailed information",
)
@click.option(
    "--account",
    type=str,
    default=None,
    help="Only check this account (default: every Granola account).",
)
def pending(since: int, output_json: bool, verbose: bool, account: Optional[str]):
    """
    List meetings that ended but have no generated notes.

    Shows meetings with transcripts where the "Generate notes" button
    was never clicked in Granola. With multiple accounts on disk, checks
    each account in turn. Includes the owning account in JSON output.

    Examples:

      \b
      # Check all accounts (last 30 days)
      notesync pending

      \b
      # Check last 7 days only
      notesync pending --since 7

      \b
      # Check one account
      notesync pending --account user@example.com
    """
    import time as _time
    from datetime import datetime, timedelta, timezone

    try:
        accounts = _resolve_accounts(account)

        all_meetings: list = []  # for JSON aggregation across accounts
        any_pending_text = False  # to suppress the "all generated!" green line when only one account had nothing

        for idx, acc in enumerate(accounts):
            if not output_json and len(accounts) > 1:
                if idx > 0:
                    console.print()
                console.print(f"[bold blue]── Account: {acc.email} ──[/bold blue]")

            api = GranolaAPI(access_token=acc.access_token)

            if not output_json:
                console.print("[blue]Fetching documents...[/blue]")
            response = api.get_documents()

            cutoff = datetime.now(timezone.utc) - timedelta(days=since)

            candidates = []
            for doc in response.docs:
                if doc.is_likely_in_progress():
                    continue
                try:
                    updated = datetime.fromisoformat(doc.updated_at.replace("Z", "+00:00"))
                    if updated < cutoff:
                        continue
                except (ValueError, TypeError):
                    pass
                candidates.append(doc)

            if not candidates:
                if not output_json:
                    console.print(
                        f"[green]No meetings found in the last {since} days.[/green]"
                    )
                continue

            if not output_json:
                console.print(
                    f"[blue]Checking {len(candidates)} meetings for missing notes...[/blue]"
                )

            pending_docs = []
            for doc in candidates:
                panels = api.get_document_panels(doc.id)
                has_content = any(p.content for p in panels.values())
                if not has_content:
                    transcript = api.get_transcript(doc.id)
                    if transcript:
                        pending_docs.append((doc, len(transcript)))
                _time.sleep(0.1)

            if not pending_docs:
                if not output_json:
                    console.print(
                        f"[green]All meetings in the last {since} days have generated notes![/green]"
                    )
                continue

            any_pending_text = True
            sorted_docs = sorted(pending_docs, key=lambda x: x[0].created_at, reverse=True)

            if output_json:
                for doc, seg_count in sorted_docs:
                    all_meetings.append(
                        {
                            "account": acc.email,
                            "date": doc.created_at[:10] if doc.created_at else None,
                            "title": doc.title or "Untitled",
                            "segments": seg_count,
                            "document_id": doc.id,
                        }
                    )
            else:
                table = Table(title=f"Meetings needing notes ({len(pending_docs)})")
                table.add_column("Date", style="green")
                table.add_column("Title", style="cyan", no_wrap=False, max_width=60)
                table.add_column("Segments", justify="right", style="yellow")

                for doc, seg_count in sorted_docs:
                    created = doc.created_at[:10] if doc.created_at else "N/A"
                    table.add_row(
                        created,
                        doc.title or "Untitled",
                        str(seg_count),
                    )

                console.print(table)
                console.print(
                    "\n[dim]Open these meetings in Granola and click \"Generate notes\" to create summaries.[/dim]"
                )

        if output_json:
            import json
            click.echo(json.dumps({"count": len(all_meetings), "meetings": all_meetings}))
        elif not any_pending_text and len(accounts) > 1:
            # All accounts iterated, none had pending notes. The per-account
            # "All meetings… have generated notes!" lines already printed; no
            # final aggregate line needed.
            pass

        sys.exit(0)

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
        sys.exit(130)
    except Exception as e:
        err_console.print(f"\n[bold red]Error: {e}[/bold red]")
        if verbose:
            import traceback
            console.print(f"[dim]{traceback.format_exc()}[/dim]")
        sys.exit(1)


if __name__ == "__main__":
    cli()
