"""Auto-detect a book's heading structure from its plain text (one Gutenberg volume at a time)."""
import re
from dataclasses import dataclass, field
from statistics import median

KINDS = ("BOOK", "CHAPTER", "FABLE", "PART", "CANTO", "ACT", "SCENE", "LETTER", "VOLUME", "SECTION", "STAVE")
LABELS = ("INTRODUCTION", "PREFACE", "CONTENTS", "EXPLANATION", "THE ARGUMENT", "ARGUMENT", "NOTES",
          "FOOTNOTES", "ENDNOTES", "APPENDIX", "INDEX", "INDEX TO NOTES", "THE END", "FINIS", "EPILOGUE", "PROLOGUE")
# Labels that mean the main text is over once they appear after the last body heading.
TERMINATORS = ("NOTES", "FOOTNOTES", "ENDNOTES", "APPENDIX", "INDEX", "INDEX TO NOTES", "THE END", "FINIS")

ORDINALS = ("FIRST SECOND THIRD FOURTH FIFTH SIXTH SEVENTH EIGHTH NINTH TENTH ELEVENTH TWELFTH "
            "THIRTEENTH FOURTEENTH FIFTEENTH SIXTEENTH SEVENTEENTH EIGHTEENTH NINETEENTH TWENTIETH").split()
CARDINALS = ("ONE TWO THREE FOUR FIVE SIX SEVEN EIGHT NINE TEN ELEVEN TWELVE THIRTEEN FOURTEEN FIFTEEN "
             "SIXTEEN SEVENTEEN EIGHTEEN NINETEEN TWENTY").split()
# Roman numerals must be uppercase so title words like "Civil" or "Mix" are not read as numbers.
_NUM = r"(?:(?-i:[IVXLC]+)|\d+|" + "|".join(ORDINALS + CARDINALS) + r")(?![A-Za-z])"
HEADING_RE = re.compile(
    rf"^(?P<kind>{'|'.join(KINDS)})S?\.?\s+(?:THE\s+)?(?P<nums>{_NUM}(?:[.,]?\s+(?:AND\s+)?{_NUM})*)[.:]?"
    rf"\s*(?:\[(?P<ref>[^\]]*)\])?(?:(?:(?<=\.)\s+|\s*[:\-—]\s*)(?P<title>.+))?\s*$", re.I)
# Bare numerals ("IV." / "12." / "I. A SCANDAL IN BOHEMIA") with no CHAPTER/BOOK word. Only used as a
# fallback, because lone numbers are ambiguous (page numbers, verse lines). A title needs a period after
# the numeral, so prose that starts with "I rose to go..." is not a heading.
BARE_RE = re.compile(r"^(?P<num>(?-i:[IVXLC]+)|\d+)(?:\.(?:\s+(?P<title>[A-Za-z\"'\[(].*))?)?$")
_KIND_WORD_RE = re.compile(rf"\b(?:{'|'.join(KINDS)})S?\.?\s+(?:THE\s+)?{_NUM}", re.I)  # "CHAPTER I", not "part of"
LABEL_RE = re.compile(rf"^(?:(?:FIRST|SECOND|THIRD) )?(?P<label>{'|'.join(sorted(LABELS, key=len, reverse=True))})\.?"
                      rf":?\s*(?:\[(?P<ref>[^\]]*)\])?(?:\s*[:\-—]\s*.+)?$")   # "FOOTNOTES:" too


@dataclass
class Heading:
    line: int                    # 0-indexed line in the volume
    kind: str                    # "book", "fable", ... or "label"
    numbers: list[int]           # [5] or [4, 5, 6] for "FABLES IV. V. AND VI."; [] for labels
    text: str                    # the heading line, stripped
    ref: str | None = None       # bracketed reference such as "I.5-31"
    title: str | None = None
    region: str = "body"         # "front", "body" or "after", set by scan()


@dataclass
class Scan:
    headings: list[Heading]
    body_kind: str | None        # top-level kind of the main text, e.g. "book"
    body_start: int              # line of the first body heading
    body_end: int                # line where the main text ends
    levels: list[str]            # hierarchy inside the body, e.g. ["book", "fable"]
    report: dict = field(default_factory=dict)


def _to_int(tok: str) -> int | None:
    tok = tok.strip(".,").upper()
    if tok.isdigit():
        return int(tok)
    if tok in ORDINALS:
        return ORDINALS.index(tok) + 1
    if tok in CARDINALS:
        return CARDINALS.index(tok) + 1
    if re.fullmatch(r"[IVXLC]+", tok):
        vals = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
        total = 0
        for a, b in zip(tok, tok[1:] + " "):
            total += -vals[a] if b != " " and vals[a] < vals[b] else vals[a]
        return total
    return None


def find_candidates(lines: list[str], bare: bool = False) -> list[Heading]:
    """Short, isolated lines (blank line before) that look like numbered headings or known labels.

    With bare=True, lines that are only a number ("IV." / "12.") also count, as kind "number", or "titled"
    when a title follows ("I. A SCANDAL IN BOHEMIA").
    """
    out = []
    for i, line in enumerate(lines):
        s = line.strip()
        if not s or len(s) > 80 or (i > 0 and lines[i - 1].strip()):
            continue
        if m := HEADING_RE.match(s):
            nums = [n for t in re.split(r"[\s.,]+", m.group("nums")) if t.upper() != "AND" and t
                    and (n := _to_int(t)) is not None]
            title = m.group("title")
            # A title that holds another heading word is prose, e.g. "BOOK I. (Folio), CHAPTER I. (Sperm Whale)". Not one
            # that only ends in "PART II.": "BOOK VI.--ELIS. PART II." is the second part of Elis, still Book 6.
            inner = _KIND_WORD_RE.search(title) if title else None
            if inner and not (inner.group(0).upper().startswith("PART") and not title[inner.end():].strip(" .")):
                continue
            # Several numbers are only valid in a list like "IV. V. AND VI."; otherwise it is a
            # table-of-contents line such as "Book I.    1" (page number).
            if nums and (len(nums) == 1 or " AND " in s.upper()):
                out.append(Heading(i, m.group("kind").lower(), nums, s, m.group("ref"), title))
        elif m := LABEL_RE.match(s):
            out.append(Heading(i, "label:" + m.group("label"), [], s, m.group("ref")))
        elif bare and (m := BARE_RE.match(s)) and (n := _to_int(m.group("num"))) is not None:
            kind = "titled" if m.group("title") else "number"  # "I. A SCANDAL IN BOHEMIA" vs bare "I."
            out.append(Heading(i, kind, [n], s, None, m.group("title")))
    return out


def _runs(headings: list[Heading]) -> list[list[Heading]]:
    """Group numbered headings of one kind into runs of consecutive numbering (1,2,3...)."""
    runs: dict[str, list[list[Heading]]] = {}
    for h in headings:
        if h.kind.startswith("label:"):
            continue
        kind_runs = runs.setdefault(h.kind, [])
        if kind_runs and h.numbers[0] == kind_runs[-1][-1].numbers[-1] + 1:
            kind_runs[-1].append(h)
        else:
            kind_runs.append([h])
    return [r for rs in runs.values() for r in rs]


def scan(lines: list[str]) -> Scan:
    """Detect the book's hierarchy, where the main text starts and ends, and where headings fall."""
    heads = find_candidates(lines)
    runs = _runs(heads)

    def gap(run: list[Heading]) -> float:
        # Median distance between consecutive headings of the run (not to the end of the text, which
        # would make a short table-of-contents run look as long as the whole book).
        pos = [h.line for h in run]
        return median(b - a for a, b in zip(pos, pos[1:]))

    candidates = [r for r in runs if len(r) >= 2]
    if not candidates:
        # No CHAPTER/BOOK-style headings: try bare numerals ("I.", "II."), but only for a run that is
        # long enough and widely spaced, so page numbers and stray numbers don't count.
        heads = find_candidates(lines, bare=True)
        candidates = [r for r in _runs(heads) if len(r) >= 3 and gap(r) >= 60]
    if not candidates:
        return Scan(heads, None, 0, len(lines), [], {"note": "no numbered headings found"})
    body_run = max(candidates, key=gap)
    body_kind = body_run[0].kind
    body_start = body_run[0].line
    last = body_run[-1].line
    terminators = [h.line for h in heads if h.kind.startswith("label:") and h.line > last
                   and h.kind[6:] in TERMINATORS]
    body_end = min(terminators) if terminators else len(lines)

    # Inner levels: kinds whose headings fall inside the body, outermost (largest gap) first.
    inner: dict[str, list[Heading]] = {}
    for h in heads:
        if body_start <= h.line < body_end and not h.kind.startswith("label:") and h.kind != body_kind:
            inner.setdefault(h.kind, []).append(h)
    levels = [body_kind] + sorted(inner, key=lambda k: -median(
        b.line - a.line for a, b in zip(inner[k], inner[k][1:] + [Heading(body_end, k, [0], "")])))

    for h in heads:
        h.region = "front" if h.line < body_start else "after" if h.line >= body_end else "body"

    report = {
        "body_kind": body_kind,
        "levels": levels,
        "body_headings": sum(1 for h in heads if h.region == "body" and h.kind == body_kind),
        "front_headings": sum(1 for h in heads if h.region == "front" and not h.kind.startswith("label:")),
        "after_headings": sum(1 for h in heads if h.region == "after" and not h.kind.startswith("label:")),
        "labels": sorted({h.kind[6:] for h in heads if h.kind.startswith("label:")}),
    }
    return Scan(heads, body_kind, body_start, body_end, levels, report)
