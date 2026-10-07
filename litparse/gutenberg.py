"""Locate Project Gutenberg boilerplate and book text. Never modifies the source file."""
import re
from dataclasses import dataclass

# Matches "*** START OF THE PROJECT GUTENBERG EBOOK X ***" and the older "THIS" variant, and for a text from another
# open source (python -m litparse.perseus) "*** START OF THIS TEXT ***".
START_RE = re.compile(r"^\*\*\* ?START OF (?:THE|THIS) (?:PROJECT GUTENBERG EBOOK|TEXT)", re.I)
END_RE = re.compile(r"^\*\*\* ?END OF (?:THE|THIS) (?:PROJECT GUTENBERG EBOOK|TEXT)", re.I)
TITLE_RE = re.compile(r"^Title:\s*(.+?)\s*$")


@dataclass
class Volume:
    start: int         # 0-indexed first line of book text (the line after the START marker)
    end: int           # 0-indexed line after the last book-text line (the END marker's index)
    title: str | None  # from the "Title:" header line, if present


def find_volumes(lines: list[str]) -> list[Volume]:
    """Return one Volume per START/END pair. A file may hold several (e.g. Metamorphoses has two).

    Raises ValueError if no START/END pair is found: only Project Gutenberg texts are supported.
    """
    volumes, start, title, pending_title = [], None, None, None
    for i, line in enumerate(lines):
        if start is None and (m := TITLE_RE.match(line)):
            pending_title = m.group(1)
        if START_RE.match(line):
            start, title = i + 1, pending_title
        elif END_RE.match(line) and start is not None:
            volumes.append(Volume(start, i, title))
            start = None
    if not volumes:
        raise ValueError("No START/END markers found; only Project Gutenberg texts (or texts made by litparse.perseus) are supported.")
    return volumes


def boilerplate_ranges(lines: list[str], volumes: list[Volume]) -> list[tuple[int, int]]:
    """Line ranges (0-indexed start, end) outside every volume: the headers and licence text."""
    ranges, cursor = [], 0
    for v in volumes:
        if v.start > cursor:
            ranges.append((cursor, v.start))
        cursor = v.end
    if cursor < len(lines):
        ranges.append((cursor, len(lines)))
    return ranges
