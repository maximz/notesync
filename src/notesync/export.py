"""
Export engine for NoteSync.
Orchestrates the export of notes and transcripts to markdown files.
"""

import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

from .api import GranolaAPI
from .markdown import create_full_note_markdown
from .models import Document, Folder
from .sync import SYNC_DB_FILENAME, SyncDatabase


console = Console()


class ExportEngine:
    """
    Main export engine for syncing Granola notes to disk.
    """

    def __init__(self, api: Optional[GranolaAPI] = None):
        """
        Initialize the export engine.

        Args:
            api: Optional GranolaAPI instance. If not provided, will create one.
        """
        self.api = api or GranolaAPI()

    def sanitize_title(self, title: str) -> str:
        """
        Sanitize a title for use in a filename.
        Keeps dashes, parentheses, and underscores for readability.
        Replaces spaces with underscores and removes problematic characters.

        Args:
            title: The title to sanitize

        Returns:
            Sanitized title safe for filenames
        """
        if not title or not title.strip():
            return "Untitled"

        # Replace problematic filesystem characters with underscores
        # Keep: letters, numbers, spaces, dashes, underscores, parentheses
        # Remove: < > : " / \ | ? * and other special chars
        sanitized = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title)

        # Replace multiple spaces with single space
        sanitized = re.sub(r"\s+", " ", sanitized)

        # Replace spaces with underscores
        sanitized = sanitized.replace(" ", "_")

        # Remove leading/trailing underscores and dashes
        sanitized = sanitized.strip("_-")

        # Collapse multiple underscores to single
        sanitized = re.sub(r"_+", "_", sanitized)

        # Limit length to 80 characters for readability
        sanitized = sanitized[:80]

        # Remove trailing underscores/dashes again after truncation
        sanitized = sanitized.rstrip("_-")

        return sanitized or "Untitled"

    def generate_filename(self, document: Document) -> str:
        """
        Generate a filename for a document with timestamp prefix.
        Format: YYYYMMDD_HHMM.Title.abc12345.md

        Example: 20250909_1400.Meeting_Title.0119670b.md

        Args:
            document: The document to generate filename for

        Returns:
            Filename string (not a path)
        """
        # Parse created_at timestamp
        try:
            dt = document.get_created_datetime()
        except Exception:
            # Fallback to updated_at if created_at is invalid
            try:
                dt = document.get_updated_datetime()
            except Exception:
                # Last resort: use current time
                dt = datetime.utcnow()

        # Format timestamp: YYYYMMDD_HHMM
        timestamp = dt.strftime("%Y%m%d_%H%M")

        # Sanitize title (keeps dashes, parentheses, converts spaces to underscores)
        title = self.sanitize_title(document.title)

        # Get first 8 characters of document ID for uniqueness
        unique_id = document.id[:8]

        # Use period separators between timestamp, title, and ID
        return f"{timestamp}.{title}.{unique_id}.md"

    def get_folder_structure(
        self, folders: Dict[str, Folder], documents: List[Document]
    ) -> Dict[str, str]:
        """
        Map documents to their folder names.

        Args:
            folders: Dictionary of folder_id -> Folder
            documents: List of documents

        Returns:
            Dictionary mapping doc_id -> folder_name
            Documents not in any folder map to "Uncategorized"
        """
        doc_to_folder = {}

        # Build reverse mapping: doc_id -> list of folder names
        doc_folders: Dict[str, List[str]] = {}
        for folder in folders.values():
            if folder.document_ids:
                for doc_id in folder.document_ids:
                    if doc_id not in doc_folders:
                        doc_folders[doc_id] = []
                    doc_folders[doc_id].append(folder.title)

        # Assign each document to a folder (use first folder if in multiple)
        for doc in documents:
            if doc.id in doc_folders and doc_folders[doc.id]:
                # Use first folder alphabetically for consistency
                folder_name = sorted(doc_folders[doc.id])[0]
                doc_to_folder[doc.id] = self.sanitize_title(folder_name)
            else:
                doc_to_folder[doc.id] = "Uncategorized"

        return doc_to_folder

    def ensure_folder_exists(self, output_dir: str, folder_name: str) -> str:
        """
        Ensure a folder exists within the output directory.

        Args:
            output_dir: Base output directory
            folder_name: Folder name to create

        Returns:
            Full path to the folder
        """
        folder_path = os.path.join(output_dir, folder_name)
        Path(folder_path).mkdir(parents=True, exist_ok=True)
        return folder_path

    def export_single_note(
        self,
        document: Document,
        output_dir: str,
        folder_name: str,
        verbose: bool = False,
        debug: bool = False,
    ) -> str:
        """
        Export a single note to a markdown file.

        Args:
            document: The document to export
            output_dir: Base output directory
            folder_name: Folder name to organize the file in
            verbose: If True, print detailed debug information

        Returns:
            Full path to the exported file

        Raises:
            Exception: If export fails
        """
        # Ensure folder exists
        folder_path = self.ensure_folder_exists(output_dir, folder_name)

        # Generate filename
        filename = self.generate_filename(document)
        file_path = os.path.join(folder_path, filename)

        # Fetch transcript
        try:
            transcript_segments = self.api.get_transcript(document.id)
            if verbose:
                console.print(f"[dim]  Transcript: {len(transcript_segments)} segments[/dim]")
        except Exception as e:
            console.print(f"[yellow]Warning: Could not fetch transcript for {document.title or 'Untitled'}: {e}[/yellow]")
            transcript_segments = []

        # Get panels from cache
        try:
            panels = self.api.get_document_panels(document.id, verbose=verbose)
            if verbose:
                console.print(f"[dim]  Panels: {len(panels)} found[/dim]")
        except Exception as e:
            console.print(f"[yellow]Warning: Could not read panels for {document.title or 'Untitled'}: {e}[/yellow]")
            panels = {}

        # Convert to markdown
        markdown_content = create_full_note_markdown(document, panels, transcript_segments, debug=debug)

        # Write to file
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(markdown_content)

        return file_path

    def sync_all_notes(
        self,
        output_dir: str,
        force: bool = False,
        dry_run: bool = False,
        verbose: bool = False,
        debug: bool = False,
        since: Optional[Union[datetime, int]] = None,
    ) -> Dict[str, Any]:
        """
        Sync all notes from Granola to disk.
        Main orchestration method for incremental sync.

        Args:
            output_dir: Directory to export notes to
            force: If True, re-export all notes regardless of sync state
            dry_run: If True, don't actually write files
            verbose: If True, show detailed logging
            since: If provided, force re-export notes updated since this datetime or N days ago (int)

        Returns:
            Dictionary with sync statistics:
            - new: Number of new notes exported
            - updated: Number of updated notes re-exported
            - skipped: Number of unchanged notes skipped
            - failed: Number of notes that failed to export
            - total: Total number of notes processed
        """
        # Ensure output directory exists
        output_dir_path = Path(output_dir)
        output_dir_path.mkdir(parents=True, exist_ok=True)

        # Initialize sync database
        db_path = output_dir_path / SYNC_DB_FILENAME
        sync_db = SyncDatabase(str(db_path))

        stats = {
            "new": 0,
            "updated": 0,
            "skipped": 0,
            "failed": 0,
            "total": 0,
        }

        console.print("[bold blue]Fetching documents from Granola...[/bold blue]")

        # Fetch all documents and folders
        try:
            response = self.api.get_documents()
            documents = response.docs
            stats["total"] = len(documents)

            folders_response = self.api.get_folders()
            folders = folders_response.lists

            console.print(f"[green]Found {len(documents)} documents and {len(folders)} folders[/green]")

        except Exception as e:
            console.print(f"[bold red]Error fetching data from Granola API: {e}[/bold red]")
            raise

        # Get folder structure
        doc_to_folder = self.get_folder_structure(folders, documents)

        # Handle --since filter: convert int (days) to datetime
        since_cutoff = None
        if since is not None:
            if isinstance(since, int):
                since_cutoff = datetime.now(timezone.utc) - timedelta(days=since)
            else:
                since_cutoff = since
                # Ensure timezone-aware
                if since_cutoff.tzinfo is None:
                    since_cutoff = since_cutoff.replace(tzinfo=timezone.utc)

        # Filter documents that need syncing
        if since_cutoff is not None:
            # Filter to docs updated since cutoff, then force re-export those
            docs_to_sync = []
            for doc in documents:
                try:
                    updated = datetime.fromisoformat(doc.updated_at.replace("Z", "+00:00"))
                    if updated >= since_cutoff:
                        docs_to_sync.append(doc)
                except (ValueError, TypeError):
                    # If we can't parse the date, include it
                    docs_to_sync.append(doc)

            console.print(
                f"[blue]Force re-exporting {len(docs_to_sync)} documents updated since "
                f"{since_cutoff.strftime('%Y-%m-%d %H:%M')} UTC[/blue]"
            )
        elif force:
            docs_to_sync = documents
            console.print(f"[blue]Force sync: re-exporting all {len(docs_to_sync)} documents[/blue]")
        else:
            docs_to_sync = [doc for doc in documents if sync_db.should_sync(doc, force=False)]
            console.print(
                f"[blue]{len(docs_to_sync)} documents need syncing "
                f"({len(documents) - len(docs_to_sync)} already up to date)[/blue]"
            )

        if not docs_to_sync:
            console.print("[green]All documents are already up to date![/green]")
            return stats

        if dry_run:
            console.print(f"[yellow]Dry run: would export {len(docs_to_sync)} documents[/yellow]")
            for doc in docs_to_sync:
                folder = doc_to_folder.get(doc.id, "Uncategorized")
                filename = self.generate_filename(doc)
                console.print(f"  {folder}/{filename}")
            return stats

        # Export documents with progress bar
        console.print(f"[bold blue]Exporting {len(docs_to_sync)} documents...[/bold blue]")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("[cyan]Exporting notes...", total=len(docs_to_sync))

            sync_records = []

            for doc in docs_to_sync:
                folder_name = doc_to_folder.get(doc.id, "Uncategorized")
                title_preview = (doc.title or "Untitled")[:50]
                progress.update(task, description=f"[cyan]Exporting: {title_preview}...")

                try:
                    # Check if this is new or updated
                    sync_state = sync_db.get_sync_state(doc.id)
                    is_new = sync_state is None

                    # Export the note
                    file_path = self.export_single_note(doc, output_dir, folder_name, verbose=verbose, debug=debug)

                    # Store paths relative to output_dir for portability across machines.
                    try:
                        stored_file_path = os.path.relpath(file_path, output_dir)
                    except ValueError:
                        stored_file_path = file_path

                    # Track sync record
                    sync_records.append((
                        doc.id,
                        doc.title or "Untitled",
                        doc.created_at,
                        doc.updated_at,
                        stored_file_path,
                    ))

                    # Update stats
                    if is_new:
                        stats["new"] += 1
                    else:
                        stats["updated"] += 1

                    if verbose:
                        status = "new" if is_new else "updated"
                        console.print(f"  [green]✓[/green] {folder_name}/{self.generate_filename(doc)} ({status})")

                    # Small delay to avoid overwhelming the API
                    time.sleep(0.1)

                except Exception as e:
                    stats["failed"] += 1
                    console.print(f"  [red]✗[/red] Failed to export {doc.title or 'Untitled'} (ID: {doc.id[:8]}): {e}")
                    import traceback
                    console.print(f"[dim]{traceback.format_exc()}[/dim]")

                progress.advance(task)

            # Batch update sync database
            if sync_records:
                sync_db.mark_many_synced(sync_records)

        # Print summary
        console.print("\n[bold green]Export complete![/bold green]")
        console.print(f"  New: {stats['new']}")
        console.print(f"  Updated: {stats['updated']}")
        console.print(f"  Skipped: {len(documents) - len(docs_to_sync)}")
        console.print(f"  Failed: {stats['failed']}")
        console.print(f"  Total: {stats['total']}")
        console.print(f"\n[blue]Notes exported to: {output_dir}[/blue]")

        return stats
