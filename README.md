# NoteSync

Export and sync your [Granola](https://www.granola.ai/) meeting notes and transcripts to local markdown files.

A Python CLI tool inspired by the Granola extension for Raycast, designed for reliable automated backups via cron or launchd.

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
- **Independent Authentication**: Browser-approved CLI session that never borrows or rotates Granola Desktop's token
- **Conservative API Use**: Sequential requests paced at 2 requests/second with automatic slowdown on rate limits

## Installation

### Prerequisites

- Python 3.11 or higher
- A Granola account you can approve in a browser
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

### First run: authorize NoteSync's own session

Current Granola builds keep the desktop credential store behind an app-specific macOS entitlement. NoteSync therefore uses a separate browser-approved device session instead of borrowing Granola Desktop's token:

```bash
uv run notesync auth login
uv run notesync auth status
```

Approve the displayed Granola page once. The resulting session is stored under `~/.local/share/notesync/sessions/` in a mode-`0700` directory with mode-`0600` files. Scheduled runs refresh only this CLI-owned token family. See [Authentication](#authentication) for details.

## Quick Start

```bash
# Create the independent CLI session once
uv run notesync auth login

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

**After changing dependencies in `pyproject.toml`**, re-run `uv tool install -e . --force` to update the tool environment. Code changes are picked up automatically via editable mode, but dependency changes are not.

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
- `--output-dir PATH`: Per-account directory containing `.notesync-sync.db` (auto-detects if not specified). With the multi-account layout, this is `OUTPUT_DIR/<account-email>/`, not the base `OUTPUT_DIR`.
- `--delete-file`: Also delete the markdown file from disk

**Use Cases:**
- Testing re-sync of a specific note
- Regenerating a note after fixing conversion issues
- Removing a note that was synced incorrectly

**Examples:**

```bash
# Forget a note (keeps file, removes from sync state)
uv run notesync forget "Uncategorized/20240101_2100.Meeting_Title.7ab123dd.md" \
    --output-dir ~/Dropbox/notesync_notes/alice_example_com

# Forget and delete the file
uv run notesync forget "Uncategorized/20240101_2100.Meeting_Title.7ab123dd.md" \
    --output-dir ~/Dropbox/notesync_notes/alice_example_com --delete-file

# Auto-detect output directory (when run from the per-account notes directory)
cd ~/Dropbox/notesync_notes/alice_example_com
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

#### 5. Schedule the Sync

The browser-approved CLI session is stored in ordinary owner-only files, so the job no longer depends on access to Granola's protected Keychain item. A launchd LaunchAgent remains the recommended macOS scheduler because it has a predictable user environment.
>
> ```xml
> <!-- ~/Library/LaunchAgents/com.example.notesync.plist -->
> <?xml version="1.0" encoding="UTF-8"?>
> <plist version="1.0"><dict>
>   <key>Label</key><string>com.example.notesync</string>
>   <key>ProgramArguments</key><array><string>/Users/you/bin/sync_notesync.sh</string></array>
>   <key>StartCalendarInterval</key><dict><key>Minute</key><integer>0</integer></dict>
>   <key>StandardErrorPath</key><string>/tmp/notesync.err</string>
>   <key>StandardOutPath</key><string>/tmp/notesync.out</string>
> </dict></plist>
> ```
>
> Load it with `launchctl load ~/Library/LaunchAgents/com.example.notesync.plist`. Once `notesync auth login` has created the CLI session, the plain-cron setup below can also authenticate; launchd is still preferred on macOS for its predictable user environment.

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
\-- alice_example_com/           # One subdirectory per Granola account
    |-- .notesync-sync.db        # NOT in git (machine-specific)
    |-- Team Meetings/
    |   |-- 20251025_1430.Weekly_Team_Sync.a1b2c3d4.md
    |   \-- 20251023_0900.Sprint_Planning.e5f6g7h8.md
    |-- 1-on-1s/
    |   \-- 20251024_1500_Check_in_Jane_i9j0k1l2.md
    \-- Uncategorized/
        \-- 20251020_1000.Random_Ideas.m3n4o5p6.md
```

The per-account `.notesync-sync.db` files exist locally but are ignored by Git, allowing each machine to maintain its own sync state while the actual notes are version controlled.

## Alternative: Direct Cron Sync (No Git Automation)

Use this if you only want periodic local exports and do not want automatic git commit/push.

Run `notesync auth login` interactively once before enabling an unattended schedule. The crontab below can then use the CLI-owned session without reading Granola Desktop's Keychain data.

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

Exported notes are organized by account, then by folder, with timestamp-prefixed filenames:

```
~/Dropbox/notesync_notes/
 alice_example_com/                   # One subdir per Granola account
    .notesync-sync.db                 # Per-account sync state database
    Team Meetings/                    # Folder from Granola
       20251025_1430.Weekly_Team_Sync.a1b2c3d4.md
       20251023_0900.Sprint_Planning.e5f6g7h8.md
    1-on-1s/
       20251024_1500.Check_in_with_Jane.i9j0k1l2.md
    Uncategorized/                    # Notes without folders
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

The preferred authentication path is a separate, browser-approved device session:

```bash
notesync auth login           # open the approval page and save a session
notesync auth status          # inspect local session metadata; never refreshes
notesync auth logout          # remove every local CLI-owned session
notesync auth logout --account user@example.com
```

Each approval creates a token family owned by NoteSync. It does not read, copy, or rotate Granola Desktop's single-use refresh token. Token rotations are serialized per account and written atomically. DNS, connection-establishment, and connect-timeout failures happen before the refresh request is sent, so NoteSync clears its safety marker and retries on the next scheduled run. If a response is lost after the request may have been sent, or the rotated token cannot be saved, NoteSync records an interrupted-rotation marker and refuses to replay the possibly consumed token until `notesync auth login` creates a replacement session.

The session directory defaults to:

- **macOS/Linux**: `~/.local/share/notesync/sessions/`
- Tests and isolated deployments can override it with `NOTESYNC_SESSION_DIR`.

When one or more CLI-owned sessions exist, they are authoritative and NoteSync does not inspect Granola Desktop's credential files. On older Granola installations with no CLI-owned session, the legacy plaintext/`storage.dek` discovery path remains available as a compatibility fallback. Current entitlement-gated Granola builds require `notesync auth login`.

### Multiple accounts

Run `notesync auth login` once for each Granola account you want to sync. By default, `notesync sync OUTPUT_DIR` syncs every CLI-owned session into its own subdirectory, sanitized from the email address:

```
OUTPUT_DIR/
├── alice_example_com/
│   ├── .notesync-sync.db
│   └── ... notes ...
└── bob_work_io/
    ├── .notesync-sync.db
    └── ... notes ...
```

Each subdirectory has its own sync database, so accounts can be synced independently and don't collide on folder/file names.

Discover what's signed in with `notesync accounts` — it prints each account's email, the sanitized subdirectory `sync` will use for it, and where the credentials came from:

```bash
notesync accounts          # table
notesync accounts --json   # machine-readable
```

Pin to one account with `--account`:

```bash
notesync sync ~/Documents/notesync-notes --account alice@example.com
```

`list-folders`, `list-notes`, and `pending` accept the same `--account` flag and iterate every account by default. `forget` operates on one account's sync DB — point `--output-dir` at the per-account subdirectory (e.g. `~/Documents/notesync-notes/alice_example_com`).

Migrating from a pre-multi-account install: if `OUTPUT_DIR/.notesync-sync.db` exists at the root (left by an older NoteSync), the next `sync` will refuse to run and tell you exactly how to move your existing notes into the new per-account subdirectory before continuing.

### Incremental Sync

The CLI tracks which documents have been synced in a per-account SQLite database (`<OUTPUT_DIR>/<account>/.notesync-sync.db`). On each run:

1. Fetches all documents from Granola API
2. Compares `updated_at` timestamps with local sync state
3. Re-syncs meetings that ended recently (to capture complete transcripts)
4. Only exports documents that are new, updated, or recently ended
5. Updates sync state after successful export

This makes frequent syncs very efficient while ensuring transcripts are complete.

### API Compatibility

The CLI uses Granola's internal application endpoints. Requests are sequential, paced conservatively at two requests per second, automatically slowed after HTTP 429, and retried with bounded backoff:

- `GET /v2/get-documents` - Fetch all notes
- `POST /v1/get-document-transcript` - Fetch transcript for a note
- `POST /v1/get-document-lists-metadata` - Fetch folder metadata
- `POST /v1/get-document-panels` - Fetch AI-generated panel content (summaries, action items, etc.)

## Troubleshooting

### "Granola configuration file not found"

**Cause**: No CLI-owned device session or usable legacy desktop credentials were found.

**Solution**:
1. Run `notesync auth login` interactively.
2. Approve the displayed Granola page.
3. Confirm with `notesync auth status`, then retry the sync.

### "Access token not found"

**Cause**: A legacy desktop authentication file exists but contains no usable token.

**Solution**:
1. Run `notesync auth login` to create an independent session.
2. Confirm it with `notesync auth status`.

### "could not read ...stored-accounts.json.enc" / repeated Keychain prompts

**Cause**: NoteSync fell back to legacy desktop credential discovery because no CLI-owned session exists. Current Granola builds protect the encryption key with an app-specific entitlement that third-party processes cannot use.

**Solution**:
1. Run `notesync auth login`.
2. Confirm with `notesync accounts`; its source column should say `device-auth`.
3. Retry the scheduled command. It will no longer read the protected desktop store.

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

# List discovered Granola accounts
uv run notesync accounts

# Test API connection for the first account
uv run python -c "from notesync.auth import GranolaAuth; from notesync.api import GranolaAPI; a = GranolaAuth.list_accounts()[0]; print(a.email, len(GranolaAPI(access_token=a.access_token).get_documents().docs), 'documents')"

# Dry run sync (all accounts, into per-account subdirs)
uv run notesync sync /tmp/test-export --dry-run
```

## License

MIT. See [LICENSE](LICENSE).

## Credits

Built as an independent tool inspired by the [Granola extension in the Raycast extensions repo](https://github.com/raycast/extensions/tree/main/extensions/granola).

Implementation is independent; compatibility decisions were guided by observed behavior and public endpoints.
