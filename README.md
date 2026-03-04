# NoteSync

Export and sync your [Granola](https://www.granola.ai/) meeting notes and transcripts to local markdown files.

A Python CLI tool inspired by the Granola extension for Raycast, designed for reliable automated backups via cron.

## Disclaimer

- This is an unofficial, independent project and is not affiliated with, endorsed by, or maintained by Granola.
- You are responsible for ensuring your use complies with Granola's Terms of Service, API policies, and applicable laws.
- This project is also not affiliated with Raycast. The Granola extension in the Raycast extensions repo is a separate project.

## Features

- **Incremental Sync**: Only exports new or updated notes (configurable with `--force` or `--since`)
- **Organized Structure**: Preserves Granola's folder hierarchy on disk
- **Timestamp Filenames**: Uses `YYYYMMDD_HHMM.Title.abc12345.md` format for chronological sorting
- **Complete Export**: Includes user notes, AI-generated panels (summaries, action items), and transcripts
- **Attendee Information**: Exports meeting attendees with names, emails, titles, companies, and LinkedIn profiles
- **Smart Re-sync**: Automatically re-exports recently ended meetings to capture complete transcripts
- **Efficient**: Tracks sync state in SQLite to avoid unnecessary re-exports
- **Progress Tracking**: Rich terminal progress output with status updates

## Installation

### Prerequisites

- Python 3.11 or higher
- [Granola desktop app](https://www.granola.ai/) installed and logged in
- [uv](https://docs.astral.sh/uv/) package manager

### Install uv (if not already installed)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Install NoteSync

```bash
# Clone or navigate to the notesync directory
cd notesync

# Sync dependencies and install
uv sync

# Verify installation
uv run notesync --version
```

## Quick Start

```bash
# Export all notes to a directory
mkdir -p ~/Dropbox/notesync_notes
uv run notesync sync ~/Dropbox/notesync_notes

# Subsequent runs will only sync new/updated notes
uv run notesync sync ~/Dropbox/notesync_notes

# Force re-export all notes
uv run notesync sync ~/Dropbox/notesync_notes --force

# Preview what would be synced (dry run)
uv run notesync sync ~/Dropbox/notesync_notes --dry-run
```

## Installation Options

### Option 1: Run from Repository (Development)

The quick start above uses `uv run`, which runs the tool directly from the repository. This is useful if you're actively developing or modifying the tool.

### Option 2: Install Globally (Recommended for Daily Use)

For cleaner usage and cron jobs, install notesync globally:

```bash
# Install in editable mode from the repository
cd /path/to/notesync
uv tool install -e .

# Verify installation
which notesync
notesync --version

# Now use it anywhere without 'uv run'
notesync sync ~/Dropbox/notesync_notes
```

**Why `-e` (editable)?** Changes to the code are immediately reflected without reinstalling. If you just want to use the tool as-is, you can omit `-e`.
Use the output of `which notesync` in your cron config so cron does not depend on shell PATH setup.

## Usage

### Sync Command

Export Granola notes to a local directory:

```bash
# Development mode (run from repository)
uv run notesync sync [OPTIONS] OUTPUT_DIR

# Installed tool mode (recommended for cron/automation)
notesync sync [OPTIONS] OUTPUT_DIR
```

**Options:**
- `--force`: Re-export all notes, ignoring sync state (overwrites existing files)
- `--since N`: Force re-export notes updated in the last N days (e.g., `--since 7` for last week)
- `--dry-run`: Show what would be synced without actually writing files
- `--verbose`, `-v`: Show detailed logging for each note

**Examples:**

```bash
# Initial sync - exports all notes
uv run notesync sync ~/Dropbox/notesync_notes

# Incremental sync - only new/updated notes
uv run notesync sync ~/Dropbox/notesync_notes

# Force full re-sync
uv run notesync sync ~/Dropbox/notesync_notes --force

# Re-export notes from the last 7 days
uv run notesync sync ~/Dropbox/notesync_notes --since 7

# Preview changes
uv run notesync sync ~/Dropbox/notesync_notes --dry-run

# Verbose output
uv run notesync sync ~/Dropbox/notesync_notes --verbose
```

### List Folders

View your Granola folders:

```bash
uv run notesync list-folders [OPTIONS]
```

**Options:**
- `--verbose`, `-v`: Show detailed information about each folder

### List Notes

View your Granola notes:

```bash
uv run notesync list-notes [OPTIONS]
```

**Options:**
- `--folder TEXT`: Filter by folder name (case-insensitive partial match)
- `--limit INTEGER`: Maximum number of notes to display (default: 50)
- `--verbose`, `-v`: Show detailed information about each note

**Examples:**

```bash
# List recent notes
uv run notesync list-notes

# List notes in a specific folder
uv run notesync list-notes --folder "Team Meetings"

# List more notes
uv run notesync list-notes --limit 100

# Detailed view
uv run notesync list-notes --verbose
```

### Forget (Remove from Sync State)

Remove a note from the sync database to allow re-syncing:

```bash
uv run notesync forget FILE_PATH [OPTIONS]
```

**Options:**
- `--output-dir PATH`: Output directory containing .notesync-sync.db (auto-detects if not specified)
- `--delete-file`: Also delete the markdown file from disk

**Use Cases:**
- Testing re-sync of a specific note
- Regenerating a note after fixing conversion issues
- Removing a note that was synced incorrectly

**Examples:**

```bash
# Forget a note (keeps file, removes from sync state)
uv run notesync forget "Uncategorized/20240101_2100.Meeting_Title.7ab123dd.md" --output-dir ~/Dropbox/notesync_notes

# Forget and delete the file
uv run notesync forget "Uncategorized/20240101_2100.Meeting_Title.7ab123dd.md" --output-dir ~/Dropbox/notesync_notes --delete-file

# Auto-detect output directory (when run from notes directory)
cd ~/Dropbox/notesync_notes
uv run notesync forget "Uncategorized/20240101_2100.Meeting_Title.7ab123dd.md"
```

After forgetting a note, it will be re-synced on the next `notesync sync` run.

## Recommended: Wrapper Script + Git Integration

This is the recommended setup for most users. It syncs notes, then optionally commits and pushes changes to a remote repository (GitHub, GitLab, etc.) for version history and off-site backup.

### Why Git Integration?

- **Version history**: See how your notes evolved over time
- **Off-site backup**: Automatic cloud backup on every sync
- **Multi-device access**: Access notes from any machine
- **Searchable on GitHub**: Use GitHub's search to find content across all notes

### Setup Instructions

#### 1. Initialize Git Repository

```bash
# Navigate to your notes directory
cd ~/Dropbox/notesync_notes

# Initialize git repo
git init

# Create .gitignore to exclude sync database
cat << 'EOF' > .gitignore
# NoteSync sync state database (machine-specific)
.notesync-sync.db

# macOS
.DS_Store

# Editor files
*.swp
*.swo
*~
EOF

# Make initial commit
git add .gitignore
git commit -m "Initial commit: Setup NoteSync notes repository"
```

**Why exclude `.notesync-sync.db`?**
- It's binary and machine-specific state
- Not useful in version control (no meaningful diffs)
- Prevents conflicts if syncing from multiple machines
- Can be rebuilt with `--force` flag if needed

#### 2. Create Remote Repository

Create a repository on your preferred Git hosting service:

**GitHub:**
```bash
# Create repo on github.com, then:
git remote add origin git@github.com:yourusername/notesync-notes.git
git branch -M main
git push -u origin main
```

**GitLab:**
```bash
git remote add origin git@gitlab.com:yourusername/notesync-notes.git
git branch -M main
git push -u origin main
```

**Private repo recommended** - your notes may contain sensitive information!

#### 3. Set Up SSH Authentication (for passwordless push)

For cron to push automatically, you need SSH key authentication:

```bash
# Generate SSH key (if you don't have one)
ssh-keygen -t ed25519 -C "your_email@example.com"

# Add to ssh-agent
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/id_ed25519

# Copy public key to clipboard (macOS)
pbcopy < ~/.ssh/id_ed25519.pub

# Or display it to copy manually
cat ~/.ssh/id_ed25519.pub
```

Then add the public key to your Git hosting service:
- **GitHub**: Settings -> SSH and GPG keys -> New SSH key
- **GitLab**: Preferences -> SSH Keys -> Add new key

Test the connection:
```bash
ssh -T git@github.com  # or git@gitlab.com
```

#### 4. Create Sync Script with Git Commit

**Prerequisites:** Install notesync globally first (see Installation Options above):
```bash
cd /path/to/notesync
uv tool install -e .
```

Create a script that syncs notes and commits changes:

```bash
# Create sync script
cat << 'SCRIPT' > ~/bin/sync_notesync.sh
#!/bin/bash

# Configuration
OUTPUT_DIR="$HOME/Dropbox/notesync_notes"
LOG_FILE="$HOME/notesync-sync.log"
NOTESYNC_BIN="$HOME/.local/bin/notesync"  # Use full path for cron

# Timestamp for logging
timestamp() {
    date "+%Y-%m-%d %H:%M:%S"
}

log() {
    echo "[$(timestamp)] $1" | tee -a "$LOG_FILE"
}

log "Starting notesync sync..."

# Run notesync sync with full path
if "$NOTESYNC_BIN" sync "$OUTPUT_DIR" >> "$LOG_FILE" 2>&1; then
    log "notesync sync completed successfully"
else
    log "ERROR: notesync sync failed"
    exit 1
fi

# Navigate to notes directory for git operations
cd "$OUTPUT_DIR" || {
    log "ERROR: Cannot access $OUTPUT_DIR"
    exit 1
}

# Check if there are changes to commit
if [[ -n $(git status --porcelain) ]]; then
    log "Changes detected, committing to git..."

    # Stage all changes
    git add -A

    # Create commit with timestamp
    git commit -m "Auto-sync: $(date '+%Y-%m-%d %H:%M')" >> "$LOG_FILE" 2>&1

    # Push to remote
    if git push >> "$LOG_FILE" 2>&1; then
        log "Successfully pushed to remote repository"
    else
        log "ERROR: Failed to push to remote repository"
        exit 1
    fi
else
    log "No changes to commit"
fi

log "Sync and backup complete"
SCRIPT

# Make script executable
chmod +x ~/bin/sync_notesync.sh
```

#### 5. Set Up Cron Job

Add the sync script to cron:

```bash
# Edit crontab
crontab -e

# Add one of these lines:

# Every 4 hours
0 */4 * * * $HOME/bin/sync_notesync.sh >>$HOME/cron.out 2>>$HOME/cron.err

# Every day at 2 AM
0 2 * * * $HOME/bin/sync_notesync.sh >>$HOME/cron.out 2>>$HOME/cron.err

# Every hour during work hours (9 AM - 6 PM, Monday-Friday)
0 9-18 * * 1-5 $HOME/bin/sync_notesync.sh >>$HOME/cron.out 2>>$HOME/cron.err

# Every 2 hours (recommended)
0 */2 * * * $HOME/bin/sync_notesync.sh >>$HOME/cron.out 2>>$HOME/cron.err
```

#### 6. Test the Setup

Run the script manually to verify everything works:

```bash
~/bin/sync_notesync.sh
```

Check the log:
```bash
tail -f ~/notesync-sync.log
```

Verify commits on GitHub/GitLab.

### Advanced: Smarter Commit Messages

For more descriptive commit messages, you can use this enhanced script.
Note: this is an optional advanced example; test it manually before adding it to cron.

```bash
cat << 'SCRIPT' > ~/bin/sync_notesync_smart.sh
#!/bin/bash
set -euo pipefail

NOTES_DIR="$HOME/Dropbox/notesync_notes"
LOG_FILE="$HOME/notesync-sync.log"
NOTESYNC_BIN="$HOME/.local/bin/notesync"  # Use full path for cron

timestamp() { date "+%Y-%m-%d %H:%M:%S"; }
log() { echo "[$(timestamp)] $1" | tee -a "$LOG_FILE"; }

cd "$NOTES_DIR" || { log "ERROR: Cannot access $NOTES_DIR"; exit 1; }

log "Starting notesync sync..."
"$NOTESYNC_BIN" sync "$NOTES_DIR" >> "$LOG_FILE" 2>&1 || {
    log "ERROR: notesync sync failed"
    exit 1
}
log "notesync sync completed"

if [[ -n $(git status --porcelain) ]]; then
    # Count changes
    NEW=$(git status --porcelain | grep -c '^??' || true)
    MODIFIED=$(git status --porcelain | grep -c '^ M' || true)

    # Build commit message
    MSG="Auto-sync: $(date '+%Y-%m-%d %H:%M')"
    [[ $NEW -gt 0 ]] && MSG="$MSG - $NEW new"
    [[ $MODIFIED -gt 0 ]] && MSG="$MSG - $MODIFIED updated"

    log "Committing: $MSG"
    git add -A
    git commit -m "$MSG" >> "$LOG_FILE" 2>&1
    # Adjust branch name if your default branch is not main.
    if git push origin main >> "$LOG_FILE" 2>&1; then
        log "Pushed to remote"
    else
        log "ERROR: Push failed"
        exit 1
    fi
else
    log "No changes to commit"
fi
SCRIPT

chmod +x ~/bin/sync_notesync_smart.sh
```

### Troubleshooting Git Integration

#### Cron can't find git or notesync

Add PATH to your cron job:
```bash
0 */4 * * * PATH=/usr/local/bin:/usr/bin:/bin:$HOME/.cargo/bin $HOME/bin/sync_notesync.sh
```

#### SSH key not found in cron

Ensure ssh-agent is configured in the script:
```bash
# Add to beginning of script
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/id_ed25519 2>/dev/null
```

#### Push authentication fails

- Verify SSH key is added to GitHub/GitLab
- Test: `ssh -T git@github.com`
- Use SSH URLs, not HTTPS: `git@github.com:user/repo.git`

#### Large repository size

Notes with lots of history can grow large. To reduce size:
```bash
# Shallow clone on new machines
git clone --depth 1 git@github.com:user/notesync-notes.git

# Occasional cleanup
git gc --aggressive
```

### Repository Structure

With Git integration, your repository will look like:

```
notesync-notes/
|-- .git/                        # Git metadata
|-- .gitignore                   # Excludes .notesync-sync.db
|-- .notesync-sync.db            # NOT in git (machine-specific)
|-- Team Meetings/
|   |-- 20251025_1430.Weekly_Team_Sync.a1b2c3d4.md
|   \-- 20251023_0900.Sprint_Planning.e5f6g7h8.md
|-- 1-on-1s/
|   \-- 20251024_1500_Check_in_Jane_i9j0k1l2.md
\-- Uncategorized/
    \-- 20251020_1000.Random_Ideas.m3n4o5p6.md
```

The `.notesync-sync.db` file exists locally but is ignored by Git, allowing each machine to maintain its own sync state while the actual notes are version controlled.

## Alternative: Direct Cron Sync (No Git Automation)

Use this if you only want periodic local exports and do not want automatic git commit/push.

```bash
# Find the absolute binary path once
command -v notesync

# Edit your crontab
crontab -e

# Every 2 hours (recommended)
0 */2 * * * /absolute/path/to/notesync sync "$HOME/Dropbox/notesync_notes" >> "$HOME/notesync-sync.log" 2>&1

# Sync every hour during work hours (9 AM - 6 PM, Monday-Friday)
0 9-18 * * 1-5 /absolute/path/to/notesync sync "$HOME/Dropbox/notesync_notes" >> "$HOME/notesync-sync.log" 2>&1
```

**Tips:**
- Replace `/absolute/path/to/notesync` with the output of `which notesync`
- The tool automatically handles incremental sync, so frequent runs are efficient
- Check `~/notesync-sync.log` for any errors
- Cron jobs do not run while your machine is asleep; missed runs resume at the next scheduled time.

## Output Structure

Exported notes are organized by folder with timestamp-prefixed filenames:

```
~/Dropbox/notesync_notes/
 .notesync-sync.db                    # Sync state database
 Team Meetings/                       # Folder from Granola
    20251025_1430.Weekly_Team_Sync.a1b2c3d4.md
    20251023_0900.Sprint_Planning.e5f6g7h8.md
 1-on-1s/
    20251024_1500.Check_in_with_Jane.i9j0k1l2.md
 Uncategorized/                       # Notes without folders
     20251020_1000.Random_Ideas.m3n4o5p6.md
```

### Markdown File Format

Each exported note contains:

```markdown
# Meeting Title

- **Meeting:** Oct 25, 2025 2:30 PM - 3:45 PM (America/New_York)
- **Created:** 2025-10-25T14:30:45.123Z
- **Updated:** 2025-10-25T15:45:12.456Z
- **Source:** macOS

## Attendees

- **Alice Smith** <alice@example.com> - Engineering Manager, Acme Corp [LinkedIn](https://linkedin.com/in/alicesmith) *(organizer)*
- **Bob Jones** <bob@example.com> - Product Lead, Acme Corp
- **Carol Lee** <carol@partner.com> - Director, Partner Inc *(tentative)*

---

## My Notes

[Your notes in markdown format]

---

## Enhanced Notes

### Summary
[AI-generated summary]

### Action Items
[AI-generated action items]

[Other AI-generated panels...]

---

## Transcript

**Me:** First thing I wanted to discuss today...

**Them:** [Audio from presentation]

**Me:** So that covers the main points.

---

*Exported from Granola on 2025-11-23 10:30:00*
```

Attendee information includes:
- Names and email addresses
- Job titles and companies (when available)
- LinkedIn profiles (when available)
- Status annotations: *(organizer)*, *(optional)*, *(tentative)*, *(declined)*

## How It Works

### Authentication

NoteSync reads authentication credentials from the Granola desktop app's local configuration:

- **macOS**: `~/Library/Application Support/Granola/supabase.json`
- **Windows**: `%APPDATA%\Granola\supabase.json`

No separate login required - as long as you're logged into the Granola desktop app, the CLI will work.

### Incremental Sync

The CLI tracks which documents have been synced in a local SQLite database (`.notesync-sync.db` in the output directory). On each run:

1. Fetches all documents from Granola API
2. Compares `updated_at` timestamps with local sync state
3. Re-syncs meetings that ended recently (to capture complete transcripts)
4. Only exports documents that are new, updated, or recently ended
5. Updates sync state after successful export

This makes frequent syncs very efficient while ensuring transcripts are complete.

### API Compatibility

The CLI is behaviorally compatible with the same Granola API endpoints used by the Granola extension for Raycast:

- `GET /v2/get-documents` - Fetch all notes
- `POST /v1/get-document-transcript` - Fetch transcript for a note
- `POST /v1/get-document-lists-metadata` - Fetch folder metadata

It also reads AI-generated panel content from the local cache file (`cache-v3.json`).

## Troubleshooting

### "Granola configuration file not found"

**Cause**: The Granola desktop app is not installed or you're not logged in.

**Solution**:
1. Install the [Granola desktop app](https://www.granola.ai/)
2. Launch Granola and log in
3. Try running the CLI again

### "Access token not found"

**Cause**: The authentication file exists but doesn't contain a valid token.

**Solution**:
1. Make sure you're logged into Granola
2. Try logging out and logging back in
3. Check that Granola is running

### "Failed to fetch documents"

**Cause**: Network error or API issue.

**Solution**:
1. Check your internet connection
2. Verify Granola's API is accessible
3. Try again with `--verbose` for more details

### Notes not syncing

**Cause**: Sync state database might be out of date.

**Solution**:
- Use `--force` to re-export all notes
- Or delete `.notesync-sync.db` in your output directory

## Development

### Project Structure

```
notesync/
 pyproject.toml              # Project configuration and dependencies
 README.md                   # This file
 src/notesync/
     __init__.py            # Package initialization
     cli.py                 # Click CLI interface
     auth.py                # Authentication with Granola
     api.py                 # Granola API client
     models.py              # Pydantic data models
     export.py              # Export orchestration
     sync.py                # SQLite sync state management
     markdown.py            # Markdown conversion utilities
```

### Running from Source

```bash
# Install dependencies
uv sync --group dev

# Run CLI
uv run notesync [command]

# Or use Python directly
uv run python -m notesync.cli [command]
```

### Testing

```bash
# Run unit tests (pytest)
uv run --group dev pytest -q

# Test authentication
uv run python -c "from notesync.auth import GranolaAuth; print(GranolaAuth.get_user_info().email)"

# Test API connection
uv run python -c "from notesync.api import GranolaAPI; print(len(GranolaAPI().get_documents().docs), 'documents')"

# Dry run sync
uv run notesync sync /tmp/test-export --dry-run
```

## License

MIT. See [LICENSE](LICENSE).

## Credits

Built as an independent tool inspired by the [Granola extension in the Raycast extensions repo](https://github.com/raycast/extensions/tree/main/extensions/granola).

Implementation is independent; compatibility decisions were guided by observed behavior and public endpoints.
