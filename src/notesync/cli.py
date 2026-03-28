"""
Command-line interface for NoteSync.
"""

import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from . import __version__
from .api import GranolaAPI
from .auth import GranolaAuth
from .export import ExportEngine
from .sync import SYNC_DB_FILENAME, SyncDatabase


console = Console()


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
    "--generate",
    is_flag=True,
    help="Auto-generate AI notes for meetings that have a transcript but no panels",
)
@click.option(
    "--debug",
    is_flag=True,
    help="Show debug output for markdown conversion",
)
def sync(output_dir: Path, force: bool, since: int, dry_run: bool, generate: bool, verbose: bool, debug: bool):
    """
    Sync Granola notes to a local directory.

    OUTPUT_DIR: Directory to export notes to. Will be created if it doesn't exist.

    Examples:

      \b
      # Initial sync - exports all notes
      notesync sync ~/Documents/notesync-notes

      \b
      # Incremental sync - only exports new/updated notes
      notesync sync ~/Documents/notesync-notes

      \b
      # Force re-export all notes
      notesync sync ~/Documents/notesync-notes --force

      \b
      # Re-export notes from the last 7 days
      notesync sync ~/Documents/notesync-notes --since 7

      \b
      # Preview what would be synced
      notesync sync ~/Documents/notesync-notes --dry-run

      \b
      # Auto-generate notes for meetings missing AI summaries
      notesync sync ~/Documents/notesync-notes --generate

    The sync command:
    - Organizes notes by Granola folder structure
    - Uses timestamp-prefixed filenames (YYYYMMDD_HHMM_Title_abc12345.md)
    - Includes user notes, AI-generated panels, and transcripts
    - Tracks sync state to avoid re-exporting unchanged notes
    """
    try:
        # Verify authentication
        try:
            GranolaAuth.get_access_token()
        except FileNotFoundError as e:
            console.print(f"[bold red]Error: {e}[/bold red]")
            console.print("\n[yellow]Make sure Granola desktop app is installed and you're logged in.[/yellow]")
            sys.exit(1)
        except ValueError as e:
            console.print(f"[bold red]Error: {e}[/bold red]")
            sys.exit(1)

        # Initialize export engine and run sync
        engine = ExportEngine()
        stats = engine.sync_all_notes(
            output_dir=str(output_dir),
            force=force,
            dry_run=dry_run,
            verbose=verbose,
            debug=debug,
            since=since,
            generate=generate,
        )

        # Exit with success
        sys.exit(0)

    except KeyboardInterrupt:
        console.print("\n[yellow]Sync interrupted by user[/yellow]")
        sys.exit(130)
    except Exception as e:
        console.print(f"\n[bold red]Error: {e}[/bold red]")
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
def list_folders(verbose: bool):
    """
    List all Granola folders.

    Shows your Granola folders with document counts and metadata.
    """
    try:
        # Verify authentication
        try:
            GranolaAuth.get_access_token()
        except (FileNotFoundError, ValueError) as e:
            console.print(f"[bold red]Error: {e}[/bold red]")
            sys.exit(1)

        # Fetch folders
        api = GranolaAPI()
        console.print("[blue]Fetching folders from Granola...[/blue]")
        folders_response = api.get_folders()
        folders = list(folders_response.lists.values())

        if not folders:
            console.print("[yellow]No folders found[/yellow]")
            sys.exit(0)

        # Sort by title
        folders.sort(key=lambda f: f.title)

        # Create table
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
        console.print(f"\n[bold red]Error: {e}[/bold red]")
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
def list_notes(folder: str, limit: int, verbose: bool):
    """
    List Granola notes.

    Shows your Granola notes with metadata. Optionally filter by folder.

    Examples:

      \b
      # List recent notes
      notesync list-notes

      \b
      # List notes in a specific folder
      notesync list-notes --folder "Team Meetings"

      \b
      # List more notes
      notesync list-notes --limit 100
    """
    try:
        # Verify authentication
        try:
            GranolaAuth.get_access_token()
        except (FileNotFoundError, ValueError) as e:
            console.print(f"[bold red]Error: {e}[/bold red]")
            sys.exit(1)

        # Fetch documents
        api = GranolaAPI()
        console.print("[blue]Fetching notes from Granola...[/blue]")
        response = api.get_documents()
        documents = response.docs

        # Fetch folders if filtering
        folder_filter = folder.lower() if folder else None
        if folder_filter:
            folders_response = api.get_folders()
            folders = folders_response.lists

            # Find matching folder IDs
            matching_folder_ids = set()
            for folder_obj in folders.values():
                if folder_filter in folder_obj.title.lower():
                    matching_folder_ids.update(folder_obj.document_ids)

            # Filter documents
            documents = [doc for doc in documents if doc.id in matching_folder_ids]

            if not documents:
                console.print(f"[yellow]No notes found in folders matching '{folder}'[/yellow]")
                sys.exit(0)

        # Sort by updated_at (most recent first)
        documents.sort(key=lambda d: d.updated_at, reverse=True)

        # Limit results
        documents = documents[:limit]

        # Create table
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

        if len(response.docs) > limit:
            console.print(f"\n[dim]Showing {limit} of {len(response.docs)} total notes. Use --limit to see more.[/dim]")

        sys.exit(0)

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
        sys.exit(130)
    except Exception as e:
        console.print(f"\n[bold red]Error: {e}[/bold red]")
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

    Examples:

      \b
      # Forget a note (keeps file, removes from sync state)
      notesync forget "Uncategorized/20240101_2100.Meeting_Title.7ab123dd.md" --output-dir ~/Documents/notesync-notes

      \b
      # Forget a note and delete the file
      notesync forget "Uncategorized/20240101_2100.Meeting_Title.7ab123dd.md" --output-dir ~/Documents/notesync-notes --delete-file

      \b
      # Auto-detect output directory from current location
      cd ~/Documents/notesync-notes
      notesync forget "Uncategorized/20240101_2100.Meeting_Title.7ab123dd.md"
    """
    try:
        # Find the sync database
        db_path = None
        if output_dir:
            output_dir = output_dir.expanduser()
            db_path = output_dir / SYNC_DB_FILENAME
            if not db_path.exists():
                console.print(f"[bold red]Error: Sync database not found at {db_path}[/bold red]")
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
                console.print(f"[bold red]Error: Could not find {SYNC_DB_FILENAME}[/bold red]")
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
        console.print(f"\n[bold red]Error: {e}[/bold red]")
        sys.exit(1)


@cli.command()
@click.option(
    "--since",
    type=int,
    default=30,
    help="Only check meetings from the last N days (default: 30)",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Show detailed information",
)
def pending(since: int, verbose: bool):
    """
    List meetings that ended but have no generated notes.

    Shows meetings with transcripts where the "Generate notes" button
    was never clicked in Granola. Includes links to open each meeting.

    Examples:

      \b
      # List meetings from last 30 days needing notes
      notesync pending

      \b
      # Check last 7 days only
      notesync pending --since 7
    """
    import time as _time
    from datetime import datetime, timedelta, timezone

    try:
        try:
            GranolaAuth.get_access_token()
        except (FileNotFoundError, ValueError) as e:
            console.print(f"[bold red]Error: {e}[/bold red]")
            sys.exit(1)

        api = GranolaAPI()

        console.print("[blue]Fetching documents...[/blue]")
        response = api.get_documents()

        cutoff = datetime.now(timezone.utc) - timedelta(days=since)

        # Filter to ended meetings within the time window
        candidates = []
        for doc in response.docs:
            if not doc.is_meeting_ended():
                continue
            try:
                updated = datetime.fromisoformat(doc.updated_at.replace("Z", "+00:00"))
                if updated < cutoff:
                    continue
            except (ValueError, TypeError):
                pass
            candidates.append(doc)

        if not candidates:
            console.print(f"[green]No ended meetings found in the last {since} days.[/green]")
            sys.exit(0)

        console.print(f"[blue]Checking {len(candidates)} ended meetings for missing notes...[/blue]")

        pending_docs = []
        for doc in candidates:
            panels = api.get_document_panels(doc.id)
            has_content = any(p.content for p in panels.values())
            if not has_content:
                # Check if there's a transcript (worth generating for)
                transcript = api.get_transcript(doc.id)
                if transcript:
                    pending_docs.append((doc, len(transcript)))
            _time.sleep(0.1)

        if not pending_docs:
            console.print(f"[green]All meetings in the last {since} days have generated notes![/green]")
            sys.exit(0)

        # Display results
        table = Table(title=f"Meetings needing notes ({len(pending_docs)})")
        table.add_column("Date", style="green")
        table.add_column("Title", style="cyan", no_wrap=False, max_width=60)
        table.add_column("Segments", justify="right", style="yellow")

        for doc, seg_count in sorted(pending_docs, key=lambda x: x[0].created_at, reverse=True):
            created = doc.created_at[:10] if doc.created_at else "N/A"
            table.add_row(
                created,
                doc.title or "Untitled",
                str(seg_count),
            )

        console.print(table)
        console.print(f"\n[dim]Open these meetings in Granola and click \"Generate notes\" to create summaries.[/dim]")
        sys.exit(0)

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
        sys.exit(130)
    except Exception as e:
        console.print(f"\n[bold red]Error: {e}[/bold red]")
        if verbose:
            import traceback
            console.print(f"[dim]{traceback.format_exc()}[/dim]")
        sys.exit(1)


@cli.command()
@click.argument("document_id", type=str)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Show detailed logging",
)
def generate(document_id: str, verbose: bool):
    """
    Generate missing AI notes for a single document.

    DOCUMENT_ID: Full or partial (prefix) document ID.

    This triggers the same note generation that Granola performs when you
    click "Generate notes" in the desktop app: creates a panel, streams
    content from the LLM, and saves the result.

    Examples:

      \b
      # Generate notes for a specific document
      notesync generate d0c0463a --verbose
    """
    try:
        try:
            GranolaAuth.get_access_token()
        except (FileNotFoundError, ValueError) as e:
            console.print(f"[bold red]Error: {e}[/bold red]")
            sys.exit(1)

        api = GranolaAPI()

        # Resolve partial document ID
        console.print(f"[blue]Fetching documents to resolve ID {document_id}...[/blue]")
        response = api.get_documents()
        matches = [doc for doc in response.docs if doc.id.startswith(document_id)]

        if not matches:
            console.print(f"[bold red]No document found matching ID prefix '{document_id}'[/bold red]")
            sys.exit(1)
        if len(matches) > 1:
            console.print(f"[bold red]Multiple documents match '{document_id}':[/bold red]")
            for m in matches:
                console.print(f"  {m.id[:8]} - {m.title}")
            sys.exit(1)

        doc = matches[0]
        console.print(f"[cyan]Document:[/cyan] {doc.title} ({doc.id[:8]})")

        # Check if meeting has ended
        if not doc.is_meeting_ended():
            console.print("[bold red]Meeting has not ended yet (or is not a valid meeting).[/bold red]")
            console.print(f"[dim]meeting_end_count={doc.meeting_end_count}, valid_meeting={doc.valid_meeting}[/dim]")
            sys.exit(1)

        # Check if panels with content already exist
        existing_panels = api.get_document_panels(doc.id, verbose=verbose)
        panels_with_content = {pid: p for pid, p in existing_panels.items() if p.content}
        if panels_with_content:
            console.print(f"[yellow]Document already has {len(panels_with_content)} panel(s) with content. Skipping generation.[/yellow]")
            console.print("[dim]Use Granola to delete existing panels first if you want to regenerate.[/dim]")
            sys.exit(0)

        # Fetch transcript
        console.print("[blue]Fetching transcript...[/blue]")
        transcript = api.get_transcript(doc.id)
        if not transcript:
            console.print("[bold red]No transcript found. Cannot generate notes without a transcript.[/bold red]")
            sys.exit(1)
        console.print(f"[green]Transcript: {len(transcript)} segments[/green]")

        # Get user info for my_name
        try:
            user_info = GranolaAuth.get_user_info()
        except Exception:
            user_info = None

        # Generate
        console.print("[blue]Generating notes...[/blue]")
        panel_id = api.generate_notes_for_document(
            doc, transcript, user_info=user_info, verbose=verbose
        )

        console.print(f"[bold green]Notes generated successfully![/bold green]")
        console.print(f"[dim]Panel ID: {panel_id[:8]}[/dim]")

        # Verify
        panels = api.get_document_panels(doc.id, verbose=verbose)
        if panels:
            console.print(f"[green]Verified: document now has {len(panels)} panel(s)[/green]")
        else:
            console.print("[yellow]Warning: panel created but not visible via get_document_panels yet[/yellow]")

        sys.exit(0)

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
        sys.exit(130)
    except Exception as e:
        console.print(f"\n[bold red]Error: {e}[/bold red]")
        if verbose:
            import traceback
            console.print(f"[dim]{traceback.format_exc()}[/dim]")
        sys.exit(1)


if __name__ == "__main__":
    cli()
