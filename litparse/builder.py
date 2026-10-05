"""Turn a Project Gutenberg text into categorized sections. The source file is only read, never changed."""
import os
import re
from pathlib import Path

from litparse.anchors import restore_markers
from litparse.gutenberg import boilerplate_ranges, find_volumes
from litparse.headings import Heading, scan
from litparse.model import Footnote, ParsedBook, Section

FOOTNOTE_START_RE = re.compile(r"^\s*\[Footnote (\w+):")
COMMENTARY_LABELS = {"EXPLANATION", "THE ARGUMENT", "ARGUMENT", "NOTES", "FOOTNOTES", "ENDNOTES"}
APPENDIX_LABELS = {"APPENDIX", "INDEX", "INDEX TO NOTES"}
BODY_LABELS = {"PROLOGUE", "EPILOGUE"}
# A numbered heading before the main text counts as body (e.g. Frankenstein's opening letters) when it
# has at least this many lines under it and does not repeat a heading of the main text; anything else
# there is a table-of-contents entry.
MIN_FRONT_BODY_LINES = 40
# Verse translations without chapter headings mark each passage with its line range, "(ll. 116-138) Verily at the
# first...". Such passages are grouped into sections of about this many lines of verse.
LINE_MARKER_RE = re.compile(r"^\(ll?\.\s*(\d+)(?:\s*[-–]\s*(\d+))?\)")
VERSE_LINES_PER_SECTION = 100
MIN_LINE_MARKERS = 20
FRAGMENT_RE = re.compile(r"^Fragment #(\d+[A-Za-z]?)")
FRAGMENT_LINES = 90                 # lines of the file in one section of a collection of fragments
SINGLE_SECTION_LINES = 150          # a work no longer than this is one section
NOTE_LIST_TITLES = {"endnotes", "footnotes", "notes"}
# A section of the story longer than this (an hour's reading on one page: the four books of the Argonautica) is cut
# into parts of about PART_WORDS words, at paragraph breaks.
MAX_SECTION_WORDS = 12000
PART_WORDS = 4000


def extract_footnotes(lines: list[str]) -> tuple[list[str], list[Footnote]]:
    """Pull `[Footnote N: ...]` blocks out of a block of lines; return (remaining lines, footnotes)."""
    body, notes, i = [], [], 0
    while i < len(lines):
        m = FOOTNOTE_START_RE.match(lines[i])
        if not m:
            body.append(lines[i])
            i += 1
            continue
        block, depth = [], 0
        while i < len(lines):
            block.append(lines[i].strip())
            depth += lines[i].count("[") - lines[i].count("]")
            i += 1
            if depth <= 0:
                break
        text = " ".join(block).split(":", 1)[1].strip()
        notes.append(Footnote(m.group(1), text[:-1].strip() if text.endswith("]") else text))
    return body, notes


def _clean(lines: list[str]) -> str:
    return re.sub(r"\n{3,}", "\n\n", "\n".join(l.rstrip() for l in lines)).strip()


def _name(h: Heading) -> str:
    """Readable name for a numbered heading: 'Book 2', 'Fables 4+5+6', '1. A SCANDAL IN BOHEMIA'."""
    if h.kind == "titled":
        return f"{h.numbers[0]}. {h.title}"
    if h.kind == "number":
        return f"Part {h.numbers[0]}"
    plural = "s" if len(h.numbers) > 1 else ""
    return f"{h.kind.title()}{plural} {'+'.join(map(str, h.numbers))}"


SMALL_WORDS = {"a", "an", "and", "at", "by", "for", "in", "of", "on", "or", "the", "to"}


def _title_case(s: str) -> str:
    """"HESIOD'S WORKS AND DAYS" -> "Hesiod's Works and Days" (str.title() would give "Hesiod'S"); Roman numerals
    stay, and small words are lower case unless they open the title ("II. To Demeter")."""
    first = True

    def word(m: re.Match) -> str:
        nonlocal first
        w = m.group()
        if re.fullmatch(r"[IVXLC]+\.", w) or (re.fullmatch(r"[IVXLC]+", w) and len(w) > 1):
            return w
        opening, first = first, False
        return w.lower() if w.lower() in SMALL_WORDS and not opening else w.capitalize()

    return re.sub(r"[A-Za-z]+(?:['’][A-Za-z]+)*\.?", word, s)


def _is_title(M: list[str], j: int) -> bool:
    """An isolated line in capitals: "THE THEOGONY", "II. TO DEMETER". Not "((LACUNA))" or a line marker."""
    s = M[j].strip()
    return (len(s) >= 6 and s.upper() == s and bool(re.search(r"[A-Z]{3}", s)) and s[0] not in "(*["
            and not LINE_MARKER_RE.match(s) and (j == 0 or not M[j - 1].strip())
            and (j + 1 >= len(M) or not M[j + 1].strip()))


def _title_text(s: str) -> str:
    """The title without a footnote number stuck to it ("EOIAE1701", "TO DIONYSUS 2501") or a trailing dash."""
    return _title_case(re.sub(r"\s*\d{3,5}$", "", s.strip()).rstrip(" \u2014\u2013-"))


def _groups(M: list[str], start: int, end: int) -> list[tuple[int, str | None]]:
    """Where one work is cut into sections: [(first line, name)], the first entry at `start`. A poem with running
    line numbers is cut about every VERSE_LINES_PER_SECTION lines of verse ("Lines 1-105"). A collection of
    fragments, or a text with no line markers, is cut about every FRAGMENT_LINES lines of the file, at the start of
    a fragment, a passage or a paragraph ("Fragments 1-18", "Part 2"). A short work is one section (name None)."""
    marks = [(i, int(m.group(1)), int(m.group(2) or m.group(1))) for i in range(start, end) if (m := LINE_MARKER_RE.match(M[i]))]
    fragments = [i for i in range(start, end) if FRAGMENT_RE.match(M[i])]
    running = all(x[1] > y[1] for x, y in zip(marks[1:], marks))
    if marks and running and not fragments:
        groups, cur = [], [marks[0]]
        for mk in marks[1:]:
            if mk[1] - cur[0][1] >= VERSE_LINES_PER_SECTION:
                groups.append(cur)
                cur = []
            cur.append(mk)
        groups.append(cur)
        if len(groups) == 1 and end - start <= SINGLE_SECTION_LINES:
            return [(start, None)]
        return [(grp[0][0] if g else start, f"Lines {grp[0][1]}-{grp[-1][2]}") for g, grp in enumerate(groups)]
    if end - start <= SINGLE_SECTION_LINES:
        return [(start, None)]
    stops = sorted({m[0] for m in marks} | set(fragments)) or \
        [i for i in range(start + 1, end) if M[i].strip() and not M[i - 1].strip()]
    cuts = [start]
    for i in stops:
        if i - cuts[-1] >= FRAGMENT_LINES and end - i >= FRAGMENT_LINES // 2:
            cuts.append(i)
    named = []
    for k, (c, nxt) in enumerate(zip(cuts, cuts[1:] + [end])):
        numbers = [m.group(1) for i in range(c, nxt) if (m := FRAGMENT_RE.match(M[i]))]
        name = (f"Fragments {numbers[0]}-{numbers[-1]}" if len(numbers) > 1 else f"Fragment {numbers[0]}") if numbers else f"Part {k + 1}"
        named.append((c, name if name not in [n for _, n in named] else f"Part {k + 1}"))
    return named if len(named) > 1 else [(start, None)]


def _line_marker_sections(M: list[str], report: dict) -> list[dict] | None:
    """Sections for a verse anthology whose passages start with "(ll. a-b)" (Hesiod and the Homeric Hymns): one
    work for each title in capitals, from the first work with line markers on; the list of notes at the end
    ("ENDNOTES") is kept apart as commentary."""
    marks = [i for i, l in enumerate(M) if LINE_MARKER_RE.match(l)]
    if len(marks) < MIN_LINE_MARKERS:
        return None
    titles = [j for j in range(len(M)) if _is_title(M, j)]
    before = [j for j in titles if j < marks[0]]
    first = before[-1] if before else marks[0]
    titles = [j for j in titles if j >= first] or [first]
    tail = next((j for j in titles if M[j].strip().lower() in NOTE_LIST_TITLES), len(M))
    titles = [j for j in titles if j < tail]
    drafts = [dict(category="front_matter", path=["Preface"], start=0, end=first, ref=None)]
    works = 0
    for j, nxt in zip(titles, titles[1:] + [tail]):
        name = _title_text(M[j]) if _is_title(M, j) else "Text"
        note = re.search(r"(\d{3,5})$", M[j].strip()) if _is_title(M, j) else None
        body = j + 1 if _is_title(M, j) else j
        if not any(l.strip() for l in M[body:nxt]):      # a heading over a group of works ("THE HOMERIC HYMNS")
            continue
        works += 1
        groups = _groups(M, body, nxt)
        for (g_start, g_name), g_end in zip(groups, [g[0] for g in groups[1:]] + [nxt]):
            drafts.append(dict(category="body", path=[name] + ([g_name] if g_name else []), start=g_start, end=g_end, ref=None,
                               note=note.group(1) if note and g_start == body else None))
    if tail < len(M):
        drafts.append(dict(category="commentary", path=[_title_case(M[tail].strip())], start=tail + 1, end=len(M), ref=None))
    report["note"] = f"verse text with line markers: {works} works, {len(marks)} passages"
    return drafts


def _volume_sections(M: list[str], offset: int, slug: str, report: dict) -> list[dict]:
    """Section drafts (without ids) for one volume. `offset` maps volume lines to file lines."""
    sc = scan(M)
    report.update(sc.report)
    heads = sorted(sc.headings, key=lambda h: h.line)
    if not heads or sc.body_kind is None:
        by_lines = _line_marker_sections(M, report)
        if by_lines:
            return by_lines
        report["warning"] = "no headings detected; the whole volume is one body section"
        return [dict(category="body", path=["Text"], start=0, end=len(M), ref=None)]

    bounds = [h.line for h in heads] + [len(M)]
    in_body = {(h.kind, tuple(h.numbers)) for h in heads if h.region == "body"}
    drafts = []
    if heads[0].line > 0:
        drafts.append(dict(category="front_matter", path=["Preface"], start=0, end=heads[0].line, ref=None))
    stack, tail = [], None      # tail = the label that opened the after-body part (NOTES, INDEX, ...)
    for h, end in zip(heads, bounds[1:]):
        start = h.line + 1
        if h.kind.startswith("label:"):
            label = h.kind[6:]
            if label in COMMENTARY_LABELS and h.region == "body":
                cat, path = "commentary", stack + [label.title()]
            elif h.region == "after":
                tail = label.title()
                cat = ("commentary" if label in COMMENTARY_LABELS else
                       "appendix" if label in APPENDIX_LABELS else "back_matter")
                path = [tail]
            elif label in BODY_LABELS:
                cat, path = "body", [label.title()]
            else:   # INTRODUCTION, PREFACE, CONTENTS, ...
                cat, path = "front_matter", [label.title()]
        else:
            name = _name(h)
            if h.region == "after":
                cat, path = "commentary", [tail or "Notes", name]
            elif h.region == "body" or (end - start >= MIN_FRONT_BODY_LINES
                                        and (h.kind, tuple(h.numbers)) not in in_body):
                level = sc.levels.index(h.kind) if h.kind in sc.levels else 0
                stack = stack[:level] + [name]
                cat, path = "body", list(stack)
            else:
                cat, path = "front_matter", ["Contents", name]
        drafts.append(dict(category=cat, path=path, start=start, end=end, ref=h.ref))
    return drafts


def _split_long(M: list[str], drafts: list[dict]) -> list[dict]:
    """Cut each over-long body section into parts of about PART_WORDS words: "Book 1" becomes "Book 1 > Part 1"..."""
    out = []
    for d in drafts:
        words = [len(line.split()) for line in M[d["start"]:d["end"]]]
        total = sum(words)
        if d["category"] != "body" or total <= MAX_SECTION_WORDS:
            out.append(d)
            continue
        parts = round(total / PART_WORDS)
        # Paragraph starts, with the number of words before each.
        starts, seen = [], 0
        for k, count in enumerate(words):
            i = d["start"] + k
            if k and count and not M[i - 1].strip():
                before = next((M[j].strip() for j in range(i - 1, d["start"], -1) if M[j].strip()), "")
                if not before.endswith((":", ",", ";", "—")):       # never between "he said:" and the speech
                    starts.append((i, seen))
            seen += count
        cuts = [d["start"]]
        for n in range(1, parts):
            line = min(starts, key=lambda s: abs(s[1] - n * total / parts))[0]
            if line > cuts[-1]:
                cuts.append(line)
        for n, (a, b) in enumerate(zip(cuts, cuts[1:] + [d["end"]]), 1):
            out.append({**d, "path": d["path"] + [f"Part {n}"], "start": a, "end": b})
    return out


def parse_book(source: str | Path, slug: str) -> ParsedBook:
    lines = Path(source).read_text(encoding="utf-8-sig").split("\n")
    volumes = find_volumes(lines)
    sections, reports, counts, title_notes = [], [], {}, {}

    def add(category, path, start, end, ref=None, notes=True):
        chunk, footnotes = extract_footnotes(lines[start:end]) if notes else (lines[start:end], [])
        text = _clean(chunk)
        if not text and not footnotes:
            return
        counts[category] = counts.get(category, 0) + 1
        sections.append(Section(f"{slug}-{category}-{counts[category]}", slug, category, " > ".join(path),
                                path, start + 1, end, text, ref, footnotes))
        return sections[-1]

    # Assemble in file order: boilerplate ranges and volumes interleaved.
    pieces = [(s, e, None) for s, e in boilerplate_ranges(lines, volumes)] + [(v.start, v.end, v) for v in volumes]
    for start, end, vol in sorted(pieces, key=lambda p: p[0]):
        if vol is None:
            add("boilerplate", ["Gutenberg boilerplate"], start, end, notes=False)
            continue
        report: dict = {"volume_title": vol.title}
        reports.append(report)
        volume = lines[vol.start:vol.end]
        for d in _split_long(volume, _volume_sections(volume, vol.start, slug, report)):
            added = add(d["category"], d["path"], vol.start + d["start"], vol.start + d["end"], d["ref"])
            if added and d.get("note"):       # a footnote number printed on the work's title
                title_notes[added.id] = d["note"]

    markers = restore_markers(sections, title_notes)       # footnotes listed at the end and marked by bare numbers
    report = {"volumes": reports, "sections": len(sections), "by_category": counts,
              "footnotes": sum(len(s.footnotes) for s in sections)}
    if markers:
        report["footnote_markers"] = markers
    return ParsedBook(slug, _book_title([v.title for v in volumes]), sections, report)


def _book_title(titles: list[str | None]) -> str | None:
    """One title for the book. A multi-volume file gives each volume its own "Title:" line, for example "...Books I-VII"
    and "...Books VIII-XV"; they are joined on their shared start: "...Books I-VII + VIII-XV"."""
    titles = list(dict.fromkeys(t for t in titles if t))
    if len(titles) < 2:
        return titles[0] if titles else None
    prefix = os.path.commonprefix(titles)
    prefix = prefix[: prefix.rfind(" ") + 1] if prefix not in titles else prefix
    if len(prefix) < 4 or any(not t[len(prefix):] for t in titles):
        return " + ".join(titles)
    return prefix + " + ".join(t[len(prefix):] for t in titles)
