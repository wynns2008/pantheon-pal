"""Footnotes that are listed at the end of a book and marked in the text by a bare number.

Some Project Gutenberg plain texts print a footnote marker as a number stuck to the word before it
("the other East.1 He had gone") and list the notes at the end ("[1] [ Black races are ...]"). Here each such
number becomes a proper marker ("East.[1] He had gone") and the note is attached to the section it is in, so the
reader can show it beside its sentence.

A number counts as a marker only if it is stuck to the text before it and is the next footnote in order, which
keeps real numbers ("12 ships") out of it.

Other texts number their notes by chapter ("1501 (return) [ Or perhaps 'a Scythian'.]") and print the marker after
a space ("Hesiod calls him Scythes 1501."). There a number is a marker if it is one of the listed numbers and
comes after the last marker found.

A third kind already prints its markers in brackets ("Arms and the man I sing,[1] who...") and lists the notes
under a "Notes" heading, book by book ("[1] 1:1. =Arms and the man I sing.= Compare..."), with lettered footnotes
("[B]") in a list of their own. There the text is left as it is and each note is attached to the section that
cites it. Such a list may also number its notes with a bare number at the start of a paragraph ("5 _i.e._ God
of embarcation."); that is trusted only when the numbers run 1, 2, 3 without a break.
"""
import re

from litparse.model import Footnote, Section

_ENTRY_RE = re.compile(r"^\[(\d+|[A-Z])\][ \t]*", re.M)
_PAGE_LINE_RE = re.compile(r"^[0-9]+:[0-9]+[.]? *")
_CITED_RE = re.compile(r"\[(\d+|[A-Z])\]")
_PLAIN_RE = re.compile(r"(?:\A|(?<=\n\n))(\d{1,3})[ \t]+(?=\S)")
_RETURN_RE = re.compile(r"^(\d+) \(return\)[ \t]*", re.M)
# "Scythes 1501." or "EOIAE1701"; after another number only with a space between ("Papyri, 421 1704:")
_LISTED_RE = re.compile(r"(?:(?<=[^\s\d\[(])([ \n]?)|(?<=\d)( ))(\d{3,5})(?![\d\]])")
# Digits right after a word or punctuation mark ("East.1 He"), sometimes with a space or line break between
# ("with them. 44 Now").
_BARE_RE = re.compile(r"(?<=[^\s\d\[(])([ \n]?)(\d{1,3})(?![\d\]])")
LIST_TITLES = {"footnotes", "notes", "endnotes"}
MAY_SKIP = 2          # a marker may be missing from the text; accept the next few numbers too


def listed_footnotes(text: str) -> dict[str, str]:
    """The entries of a footnote list: {"1": "Black races are ..."}."""
    found = list(_ENTRY_RE.finditer(text)) or list(_RETURN_RE.finditer(text))
    if not found:       # "5 text": only if the numbers run on from 1 without a break
        plain = list(_PLAIN_RE.finditer(text))
        if len(plain) >= 3 and [m.group(1) for m in plain] == [str(n) for n in range(1, len(plain) + 1)]:
            found = plain
    notes = {}
    for m, following in zip(found, found[1:] + [None]):
        body = " ".join(text[m.end():following.start() if following else len(text)].split())
        if body.startswith("[") and body.endswith("]"):       # Butler wraps every note in brackets
            body = body[1:-1].strip()
        notes.setdefault(m.group(1), body)
    return notes


def restore_markers(sections: list[Section], title_notes: dict[str, str] | None = None) -> dict | None:
    """Turn bare footnote numbers in the body into [n] markers and attach the listed notes to their sections.
    `title_notes` gives, by section id, the number of a note that was attached to the section's title.

    Does nothing (returns None) unless the book has a footnote list and no footnotes of the usual kind.
    """
    notes = {}
    for s in sections:
        if s.category == "commentary" and (s.path[-1].lower() in LIST_TITLES or s.path[0].lower() in LIST_TITLES):
            notes.update(listed_footnotes(s.text))
    if not notes or any(s.footnotes for s in sections):
        return None
    body = [s for s in sections if s.category == "body"]
    cited = {label for s in body for label in _CITED_RE.findall(s.text)}
    if len(cited & set(notes)) * 2 >= len(notes):       # the markers are already in brackets: only attach the notes
        placed = set()
        for s in body:
            here = list(dict.fromkeys(label for label in _CITED_RE.findall(s.text) if label in notes))
            # A note opens with the page and line it belongs to ("51:7."), which means nothing here.
            s.footnotes = [Footnote(label, _PAGE_LINE_RE.sub("", notes[label])) for label in here]
            placed.update(here)
        return {"listed": len(notes), "restored": len(placed), "not_placed": sorted(set(notes) - placed)}
    by_chapter = "1" not in notes          # numbered by chapter (1501, 1502, 1601...), not 1, 2, 3
    expected, placed = 1, set()
    for s in sections:
        if s.category != "body":
            continue
        here = []

        def mark(m: re.Match) -> str:
            nonlocal expected
            gap, n = m.group(1), int(m.group(2))
            # A number standing apart from the text is a marker only if it is exactly the next footnote.
            if str(n) not in notes or not expected <= n <= expected + (0 if gap else MAY_SKIP):
                return m.group(0)
            expected = n + 1
            here.append(str(n))
            return f"[{n}]" + ("\n" if gap == "\n" else "")      # the marker joins the word before it

        def listed(m: re.Match) -> str:
            nonlocal expected
            gap, n = m.group(1) or m.group(2) or "", int(m.group(3))
            # The next note of this chapter (a few may be missing), or the first of a later chapter. This keeps out
            # a real number that happens to be a note's number ("Hom. 1746.7").
            ahead = n - expected if n // 100 == (expected - 1) // 100 or n // 100 == expected // 100 else                 (n % 100 - 1 if n > expected else -1)
            if str(n) not in notes or not 0 <= ahead <= MAY_SKIP:
                return m.group(0)
            expected = n + 1
            here.append(str(n))
            return f"[{n}]" + ("\n" if gap == "\n" else "")

        s.text = (_LISTED_RE if by_chapter else _BARE_RE).sub(listed if by_chapter else mark, s.text)
        titled = (title_notes or {}).get(s.id)
        if titled in notes:                 # the note on the work's title comes first
            here.insert(0, titled)
            expected = max(expected, int(titled) + 1)
        s.footnotes = [Footnote(label, notes[label]) for label in here]
        placed.update(here)
    return {"listed": len(notes), "restored": len(placed), "not_placed": sorted(set(notes) - placed, key=int)}
