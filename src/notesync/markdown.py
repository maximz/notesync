"""
Markdown conversion utilities for NoteSync.
Converts ProseMirror JSON structures and transcripts to markdown format.
Implements behavior compatible with Granola note/panel content formats.
"""

from datetime import datetime
from typing import Dict, List, Optional

from .models import (
    ContentNode,
    Document,
    DocumentStructure,
    PanelContent,
    TranscriptSegment,
)


def convert_node_to_markdown(node: Optional[ContentNode], depth: int = 0) -> str:
    """
    Convert a single ProseMirror content node to markdown.

    Args:
        node: The content node to convert
        depth: Current nesting depth for indentation

    Returns:
        Markdown string
    """
    if not node:
        return ""

    # Handle different node types
    if node.type == "paragraph":
        if node.content:
            text = "".join(convert_node_to_markdown(child, depth) for child in node.content)
            # Only add newline if not inside a list item
            return text + ("\n\n" if depth == 0 else "\n")
        return "\n" if depth == 0 else ""

    elif node.type == "heading":
        level = node.attrs.level if node.attrs and node.attrs.level else 1
        heading_prefix = "#" * level
        if node.content:
            text = "".join(convert_node_to_markdown(child, depth) for child in node.content)
            # Add blank line before heading (will be at doc level)
            return f"\n{heading_prefix} {text}\n\n"
        return f"\n{heading_prefix}\n\n"

    elif node.type == "bulletList":
        if node.content:
            items = []
            for child in node.content:
                item_md = convert_node_to_markdown(child, depth)
                if item_md:
                    items.append(item_md)
            # Don't add extra newline between list items
            return "".join(items)
        return ""

    elif node.type == "orderedList":
        if node.content:
            items = []
            for i, child in enumerate(node.content, 1):
                # Pass the number to the list item
                item_md = convert_list_item_to_markdown(child, depth, str(i) + ".")
                if item_md:
                    items.append(item_md)
            return "".join(items)
        return ""

    elif node.type == "listItem":
        # Get the content
        if node.content:
            indent = "  " * depth  # 2 spaces per depth level

            # Separate paragraph content from nested lists
            paragraph_parts = []
            nested_lists = []

            for child in node.content:
                if child.type in ("bulletList", "orderedList"):
                    # Nested list - will be added on new lines
                    nested = convert_node_to_markdown(child, depth + 1)
                    if nested:
                        nested_lists.append(nested)
                elif child.type == "paragraph":
                    # Paragraph in list item - goes on same line as bullet
                    text = "".join(convert_node_to_markdown(c, depth) for c in (child.content or []))
                    if text.strip():
                        paragraph_parts.append(text)
                else:
                    other = convert_node_to_markdown(child, depth)
                    if other.strip():
                        paragraph_parts.append(other)

            # Build the list item
            # Paragraph content goes on same line as bullet
            paragraph_text = " ".join(paragraph_parts) if paragraph_parts else ""

            if paragraph_text:
                # Start with bullet and paragraph text
                result = f"{indent}- {paragraph_text}\n"
                # Nested lists go on following lines (already have proper indentation)
                if nested_lists:
                    result += "".join(nested_lists)
                return result
            elif nested_lists:
                # Edge case: list item with only nested lists
                return "".join(nested_lists)

        return ""

    elif node.type == "text":
        return node.text or ""

    elif node.type == "horizontalRule":
        return "---\n\n"

    elif node.type == "doc":
        if node.content:
            return "".join(convert_node_to_markdown(child, depth) for child in node.content)
        return ""

    # Unknown node type - return empty string
    return ""


def convert_list_item_to_markdown(node: ContentNode, depth: int, prefix: str) -> str:
    """Helper for ordered list items"""
    if node.content:
        indent = "  " * depth

        # Separate paragraph content from nested lists
        paragraph_parts = []
        nested_lists = []

        for child in node.content:
            if child.type in ("bulletList", "orderedList"):
                # Nested list - will be added on new lines
                nested = convert_node_to_markdown(child, depth + 1)
                if nested:
                    nested_lists.append(nested)
            elif child.type == "paragraph":
                # Paragraph in list item - goes on same line as bullet
                text = "".join(convert_node_to_markdown(c, depth) for c in (child.content or []))
                if text.strip():
                    paragraph_parts.append(text)
            else:
                other = convert_node_to_markdown(child, depth)
                if other.strip():
                    paragraph_parts.append(other)

        # Build the list item
        # Paragraph content goes on same line as number
        paragraph_text = " ".join(paragraph_parts) if paragraph_parts else ""

        if paragraph_text:
            # Start with number and paragraph text
            result = f"{indent}{prefix} {paragraph_text}\n"
            # Nested lists go on following lines (already have proper indentation)
            if nested_lists:
                result += "".join(nested_lists)
            return result
        elif nested_lists:
            # Edge case: list item with only nested lists
            return "".join(nested_lists)

    return ""


def convert_document_structure_to_markdown(content: Optional[DocumentStructure]) -> str:
    """
    Convert a DocumentStructure to markdown.

    Args:
        content: The document structure to convert

    Returns:
        Markdown string
    """
    if not content:
        return ""

    # Handle the new document structure (type === "doc")
    if content.type == "doc" and content.content:
        # Convert the content as a doc node
        doc_node = ContentNode(type="doc", content=content.content)
        return convert_node_to_markdown(doc_node)

    # Fallback for old structure with attachments
    if content.attachments:
        result_parts = []
        for attachment in content.attachments:
            try:
                # Parse the attachment content as JSON
                import json

                parsed_content = json.loads(attachment.content)
                # Convert to ContentNode and then to markdown
                node = ContentNode(**parsed_content)
                result_parts.append(convert_node_to_markdown(node))
            except Exception:
                # Skip malformed attachments
                continue

        return " \n\n ".join(result_parts)

    return ""


def convert_panel_to_markdown(panel: PanelContent, debug: bool = False) -> str:
    """
    Convert a single AI-generated panel to markdown.
    Uses the first available content representation without adding extra headings.

    Args:
        panel: The panel content to convert

    Returns:
        Markdown string
    """
    import sys

    if debug:
        print(f"\n[DEBUG] convert_panel_to_markdown called with debug={debug}", file=sys.stderr)
        print(f"[DEBUG] panel.content type: {type(panel.content)}", file=sys.stderr)
        print(f"[DEBUG] panel.content is string: {isinstance(panel.content, str)}", file=sys.stderr)

    # Try to convert structured content if available
    markdown_content = ""
    if panel.content:
        if isinstance(panel.content, str):
            # Content is an HTML string - convert to markdown
            markdown_content = clean_html_to_markdown(panel.content, debug=debug)
        else:
            # Content is a DocumentStructure - convert using structured converter
            markdown_content = convert_document_structure_to_markdown(panel.content)

    # Fallback to original_content HTML if no structured content
    if not markdown_content and panel.original_content:
        # Basic HTML to markdown conversion
        markdown_content = clean_html_to_markdown(panel.original_content, debug=debug)

    return markdown_content


def clean_html_to_markdown(html: str, debug: bool = False) -> str:
    """
    Convert HTML to markdown with proper nested list handling.
    Uses markdownify library for accurate conversion.

    Args:
        html: HTML string

    Returns:
        Markdown string
    """
    from markdownify import markdownify as md
    import re
    import sys

    if debug:
        print(f"\n[DEBUG] clean_html_to_markdown CALLED with debug={debug}!", file=sys.stderr)

    if debug:
        print(f"\n[DEBUG HTML->MD] Input HTML ({len(html)} chars):", file=sys.stderr)
        print(f"  First 300 chars: {html[:300]}", file=sys.stderr)
        print(f"  Last 300 chars: {html[-300:]}", file=sys.stderr)

    # Convert HTML to markdown, preserving nested structure
    # heading_style="ATX" for # style headings
    # bullets='-' to use hyphens for unordered lists (consistent with ProseMirror output)
    result = md(html, heading_style="ATX", bullets="-")

    if debug:
        print(f"\n[DEBUG HTML->MD] After markdownify ({len(result)} chars):", file=sys.stderr)
        print(f"  First 300 chars: {repr(result[:300])}", file=sys.stderr)
        print(f"  Last 300 chars: {repr(result[-300:])}", file=sys.stderr)

    # Ensure blank line before all headings (proper markdown formatting)
    # Replace single newline before heading with double newline
    # This may create triple newlines in some cases, which we clean up next
    result = re.sub(r'\n(#{1,6} )', r'\n\n\1', result)

    if debug:
        print(f"\n[DEBUG HTML->MD] After heading spacing fix:", file=sys.stderr)
        print(f"  First 300 chars: {repr(result[:300])}", file=sys.stderr)
        print(f"  Last 300 chars: {repr(result[-300:])}", file=sys.stderr)

    # Clean up excessive blank lines (3+ newlines -> 2 newlines)
    result = re.sub(r'\n{3,}', '\n\n', result)

    if debug:
        print(f"\n[DEBUG HTML->MD] After cleanup:", file=sys.stderr)
        print(f"  First 300 chars: {repr(result[:300])}", file=sys.stderr)
        print(f"  Last 300 chars: {repr(result[-300:])}", file=sys.stderr)

    return result.strip()


def convert_panels_to_markdown(panels: Dict[str, PanelContent], debug: bool = False) -> str:
    """
    Convert panels for a document to markdown.
    Uses the first panel and converts its content.

    Args:
        panels: Dictionary of panel_id -> PanelContent

    Returns:
        Markdown string with panel content
    """
    if not panels:
        return ""

    # Get the first panel entry.
    first_panel_id = next(iter(panels.keys()))
    first_panel = panels[first_panel_id]

    return convert_panel_to_markdown(first_panel, debug=debug)


def convert_transcript_to_markdown(segments: List[TranscriptSegment]) -> str:
    """
    Convert transcript segments to markdown format.

    Args:
        segments: List of transcript segments

    Returns:
        Markdown formatted transcript with speaker labels
    """
    if not segments:
        return "Transcript not available for this note."

    # Sort segments by start timestamp
    sorted_segments = sorted(segments, key=lambda s: s.get_start_time())

    result_parts = []
    for segment in sorted_segments:
        if segment.source == "microphone":
            result_parts.append(f"**Me:** {segment.text}")
        elif segment.source == "system":
            result_parts.append(f"**Them:** {segment.text}")
        else:
            # Unknown source - just include the text
            result_parts.append(segment.text)

    return "\n\n".join(result_parts)


def create_full_note_markdown(
    document: Document,
    panels: Dict[str, PanelContent],
    transcript_segments: List[TranscriptSegment],
    debug: bool = False,
) -> str:
    """
    Create a complete markdown document combining notes, panels, and transcript.

    Format:
    ```
    # {Title}

    **Created:** {created_at}
    **Updated:** {updated_at}
    **Source:** {creation_source}

    ---

    ## My Notes

    {notes_markdown}

    ---

    ## Enhanced Notes

    {AI-generated panels}

    ---

    ## Transcript

    {transcript with speaker labels}

    ---

    *Exported from Granola on {export_timestamp}*
    ```

    Args:
        document: The document to export
        panels: AI-generated panels for the document
        transcript_segments: Transcript segments for the document

    Returns:
        Complete markdown document
    """
    parts = []

    # Header section
    parts.append(f"# {document.title or 'Untitled'}\n")
    parts.append(f"**Created:** {document.created_at}")
    parts.append(f"**Updated:** {document.updated_at}")
    parts.append(f"**Source:** {document.creation_source or 'unknown'}\n")
    parts.append("---\n")

    # My Notes section (user's notes)
    parts.append("## My Notes\n")
    if document.notes_markdown:
        parts.append(document.notes_markdown)
    else:
        parts.append("*No notes available*")
    parts.append("\n---\n")

    # Enhanced Notes section (AI-generated panels)
    panels_markdown = convert_panels_to_markdown(panels, debug=debug)
    if panels_markdown:
        parts.append("## Enhanced Notes\n")
        parts.append(panels_markdown)
        parts.append("---\n")

    # Transcript section
    transcript_markdown = convert_transcript_to_markdown(transcript_segments)
    if transcript_markdown and transcript_markdown != "Transcript not available for this note.":
        parts.append("## Transcript\n")
        parts.append(transcript_markdown)
        parts.append("\n---\n")

    # Footer
    export_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    parts.append(f"\n*Exported from Granola on {export_time}*\n")

    return "\n".join(parts)
