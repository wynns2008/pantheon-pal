"""Turn a Project Gutenberg text into categorized sections. The source file is only read, never changed."""
import os
import re
from collections import Counter
from pathlib import Path

from litparse.anchors import restore_markers
from litparse.gutenberg import boilerplate_ranges, find_volumes
from litparse.headings import Heading, find_candidates, scan
from litparse.model import Footnote, ParsedBook, Section

FOOTNOTE_START_RE = re.compile(r"^\s*\[Footnote (\w+):")
GREEK_RE = re.compile(r"\[Greek:\s*([^\]]*)\]")
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
# A Greek play is cut into scenes where the chorus sings an ode: a speech of the chorus at least ODE_WORDS long. A scene
# is at least SCENE_WORDS long, so a few lines sung between two speeches do not make a scene of their own.
SPEAKER_RE = re.compile(r"^\s*(?P<name>[A-Z][A-Za-z’' ]{1,30}?)\.?\s*(?:\[.*)?$")      # "OEDIPUS." / "Chorus"
ODE_WORDS = 100
SCENE_WORDS = 1000


NOTE_LINE_RE = re.compile(r"^\[(\d+)\]\s+\S")                      # "[1] The note's text..." under a chapter
INLINE_MARKER_RE = re.compile(r"[\w.,;:!?”’)]\[\d+\]")          # "...the sea-shore.[1] After the death..."


def extract_footnotes(lines: list[str], chapter: bool = False) -> tuple[list[str], list[Footnote]]:
    """Pull `[Footnote N: ...]` blocks out of a block of lines; return (remaining lines, footnotes). Also notes set as
    paragraphs that begin `[N] ` (Pausanias): each runs to the next blank line, if the text has `[N]` markers. In a
    chapter of the story (`chapter`), two such paragraphs also count without a marker: the notes of the chapter before
    it, printed after the heading (see _move_late_notes). A list of notes at the end of a book never counts here."""
    body, notes, i = [], [], 0
    note_lines = sum(bool(NOTE_LINE_RE.match(l)) for l in lines)
    marked = note_lines >= 1 and (any(INLINE_MARKER_RE.search(l) for l in lines) or (chapter and note_lines >= 2))
    while i < len(lines):
        m = FOOTNOTE_START_RE.match(lines[i])
        if not m and marked and (n := NOTE_LINE_RE.match(lines[i])):
            block = []
            while i < len(lines) and lines[i].strip():
                block.append(lines[i].strip())
                i += 1
            notes.append(Footnote(n.group(1), " ".join(block).split("]", 1)[1].strip()))
            continue
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
    text = "\n".join(l.rstrip() for l in lines)
    # Markup of Project Gutenberg e-texts the reader cannot show: a margin note with the verse numbers, a picture, and
    # a transliterated Greek word ("[Greek: Dios]" is shown as "Dios").
    text = re.sub(r"^[ \t]*\[(?:Sidenote|Illustration)[^\]\n]*\][ \t]*\n?", "", text, flags=re.M)
    text = GREEK_RE.sub(r"\1", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _name(h: Heading) -> str:
    """Readable name for a numbered heading: 'Book 2', 'Fables 4+5+6', '1. A SCANDAL IN BOHEMIA'."""
    if h.kind == "titled":
        title = _title_case(h.title) if h.title == h.title.upper() else h.title        # "1. AGAMEMNON" -> "1. Agamemnon"
        return f"{h.numbers[0]}. {title}"
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
    total = len(named)
    named = [(c, f"{n} of {total}" if n.startswith("Part ") else n) for c, n in named]      # "Part 2 of 5"
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


def _speakers(M: list[str], start: int, end: int) -> list[tuple[int, str]]:
    """The lines of a play that say who speaks ("OEDIPUS.", "Chorus"): a short name alone after a blank line, used
    at least three times (so a line of the cast list or a lone title does not count)."""
    found = [(i, m.group("name").strip().upper()) for i in range(start, end)
             if (m := SPEAKER_RE.match(M[i])) and len(M[i].strip()) <= 40 and (i == 0 or not M[i - 1].strip())]
    counts = Counter(name for _, name in found)
    return [(i, name) for i, name in found if counts[name] >= 3]


def _scene_cuts(M: list[str], start: int, end: int) -> list[int] | None:
    """Where a Greek play is cut into scenes: before each choral ode, with the stage direction over it (the chorus
    entering). None if the text is not a play: at least 30 speeches, some of them by the chorus."""
    speakers = _speakers(M, start, end)
    if len(speakers) < 30 or not any(name == "CHORUS" for _, name in speakers):
        return None
    words = lambda a, b: sum(len(line.split()) for line in M[a:b])
    cuts = [start]
    for (i, name), nxt in zip(speakers, [j for j, _ in speakers[1:]] + [end]):
        if name != "CHORUS" or words(i, nxt) < ODE_WORDS:
            continue
        above = i - 1
        while above > start and not M[above].strip():
            above -= 1
        top = above
        while top > start and M[top - 1].strip():
            top -= 1
        cut = top if M[top].strip().startswith(("[", "_")) else i
        if words(cuts[-1], cut) >= SCENE_WORDS and words(cut, end) >= SCENE_WORDS // 2:
            cuts.append(cut)
    return cuts


def _play_sections(M: list[str]) -> list[dict] | None:
    """Sections for a volume that is one play and has no numbered headings (Murray's Sophocles and Euripides): the
    preface and cast list before it, the play under its own title, then the notes after it. None if it is no play."""
    if _scene_cuts(M, 0, len(M)) is None:
        return None
    speakers = _speakers(M, 0, len(M))
    # The play begins at its first speech, with the stage directions over it ("SCENE.--Before the Palace", "_The
    # background represents..._"); the title in capitals just above them is the play's name. A name on the title page
    # ("OEDIPUS") is no speech: the first speech is followed by the next within a few pages.
    top = next(i for (i, _), (j, _) in zip(speakers, speakers[1:]) if j - i <= 200)
    while True:
        above = top - 1
        while above > 0 and not M[above].strip():
            above -= 1
        block = above
        while block > 0 and M[block - 1].strip():
            block -= 1
        if above <= 0 or not M[block].strip().startswith(("[", "_", "SCENE", "Scene")):
            break
        top = block
    title = M[above].strip() if above == block and M[above].strip().upper() == M[above].strip() else None
    front_end = block if title else top
    last = speakers[-1][0]
    tail = [i for i in range(last, len(M)) if re.match(r"^\s*(NOTES\b|TRANSCRIBER['’]S NOTE)", M[i])]
    end = tail[0] if tail else len(M)
    drafts = [dict(category="front_matter", path=["Preface"], start=0, end=front_end, ref=None)] if front_end else []
    drafts.append(dict(category="body", path=[_title_case(title) if title else "Text"], start=top, end=end, ref=None))
    for i, nxt in zip(tail, tail[1:] + [len(M)]):
        notes = M[i].strip().startswith("NOTES")
        drafts.append(dict(category="commentary" if notes else "back_matter", path=["Notes" if notes else "Transcriber's Note"],
                           start=i + 1, end=nxt, ref=None))
    return drafts


def _caps_line(s: str) -> bool:
    s = s.strip()
    return bool(s) and s.upper() == s and bool(re.search(r"[A-Z]{3}", s)) and len(s) <= 80


def _grouped_sections(M: list[str]) -> list[dict] | None:
    """Sections for a collection whose poems are numbered again from I under each group title in capitals ("OLYMPIAN
    ODES." I. II. ... "THE PYTHIAN ODES." I. II. ...), each poem with its own title under the number ("FOR HIERON OF
    SYRACUSE,"): "Olympian Odes > I. For Hieron of Syracuse". A heading in capitals after a group's last poem, after a
    wide gap ("FRAGMENTS."), opens a section of its own up to the next group or THE END. None unless there are two
    such groups."""
    numbers = [h for h in find_candidates(M, bare=True) if h.kind in ("number", "titled") and h.numbers[0] <= 200]
    groups = []                                     # (title line, the numbered headings under it)
    for h in numbers:
        above = h.line - 1
        while above > 0 and not M[above].strip():
            above -= 1
        if h.numbers[0] == 1 and _caps_line(M[above]) and not M[above - 1].strip():
            groups.append((above, [h]))
        elif groups and h.numbers[0] == groups[-1][1][-1].numbers[0] + 1:
            groups[-1][1].append(h)
    if len(groups) < 2:
        return None
    finis = next((i for i in range(groups[-1][1][-1].line, len(M)) if re.match(r"^\s*(THE END|FINIS)\.?\s*$", M[i])), len(M))
    drafts = [dict(category="front_matter", path=["Preface"], start=0, end=groups[0][0], ref=None)]
    for g, (title_line, heads) in enumerate(groups):
        group = _title_case(M[title_line].strip().rstrip(".:"))
        next_group = groups[g + 1][0] if g + 1 < len(groups) else finis
        tail = next((i for i in range(heads[-1].line + 1, next_group) if _caps_line(M[i]) and len(M[i].strip()) <= 40
                     and not any(l.strip() for l in M[i - 3:i])), None)
        group_end = tail or next_group
        for h, nxt in zip(heads, [x.line for x in heads[1:]] + [group_end]):
            start, titles = h.line + 1, []
            while start < nxt and (not M[start].strip() or _caps_line(M[start]) or not M[start].strip(" *")):
                if M[start].strip(" *"):
                    titles.append(M[start].strip())
                start += 1
            roman = h.text.strip().split()[0].rstrip(".")
            name = f"{roman}. {_title_case(titles[0].rstrip(',.;:'))}" if titles else f"{roman}."
            drafts.append(dict(category="body", path=[group, name], start=start, end=nxt, ref=None))
        if tail:
            drafts.append(dict(category="body", path=[_title_case(M[tail].strip().rstrip(".:"))], start=tail + 1,
                               end=next_group, ref=None))
    return drafts


def _volume_sections(M: list[str], offset: int, slug: str, report: dict) -> list[dict]:
    """Section drafts (without ids) for one volume. `offset` maps volume lines to file lines."""
    sc = scan(M)
    report.update(sc.report)
    heads = sorted(sc.headings, key=lambda h: h.line)
    # Bare numbers that start again from I under each group title (Pindar's four books of odes).
    if sc.body_kind in (None, "number", "titled") and not _line_marker_sections(M, {}) and (grouped := _grouped_sections(M)):
        report["note"] = "numbered poems in groups: " + ", ".join(dict.fromkeys(d["path"][0] for d in grouped if d["category"] == "body"))
        return grouped
    if not heads or sc.body_kind is None:
        by_lines = _line_marker_sections(M, report)
        if by_lines:
            return by_lines
        if play := _play_sections(M):
            report["note"] = "a play without headings: cut into scenes at the choral odes"
            return play
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
    """Cut each over-long body section into parts of about PART_WORDS words: "Book 1" becomes "Book 1 > Part 1 of 3".
    A play, whatever its length, is cut into scenes at its choral odes instead: "1. Agamemnon > Scene 1"."""
    out = []
    for d in drafts:
        scenes = _scene_cuts(M, d["start"], d["end"]) if d["category"] == "body" else None
        if scenes and len(scenes) > 1:
            for n, (a, b) in enumerate(zip(scenes, scenes[1:] + [d["end"]]), 1):
                out.append({**d, "path": d["path"] + [f"Scene {n}"], "start": a, "end": b})
            continue
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
            if k and count and not M[i - 1].strip() and not FOOTNOTE_START_RE.match(M[i]):    # nor in front of a footnote block
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
            out.append({**d, "path": d["path"] + [f"Part {n} of {len(cuts)}"], "start": a, "end": b})
    return out


# ---------- Front matter: named by its own headings ----------

FRONT_LABEL = re.compile(r"^(?:(?:preparer|transcriber|translator|editor)['’]s (?:note|preface)|preface(?: to (?:the )?\w+ edition)?"
                         r"|introduction|bibliography|foreword|prefatory note|characters in the play|argument)\.?$", re.I)
BRACKET_NOTE = re.compile(r"^\[\s*(transcriber['’]s note|preparer['’]s note)\b", re.I)
ATTRIBUTION = re.compile(r"^\[\s*((?:by|from)\s[^\]]+?)\.?\s*\]$", re.I)
CREDITS = re.compile(r"\s*(produced by|\[illustration|e-?text prepared|transcribed by)", re.I)


def _prose(line: str) -> bool:
    """A line of running text, not a heading or an entry in a list."""
    s = line.strip()
    return len(s) > 60 or (len(s) > 25 and s[:1].islower()) or bool(re.search(r"[a-z][.;:,]\s+\w", s))


def _is_contents(lines: list[str]) -> bool:
    """A table of contents: several short lines, almost none of them running text."""
    text = [l for l in lines if l.strip()]
    return len(text) >= 3 and sum(not _prose(l) for l in text) >= 0.8 * len(text)


def _is_title_page(lines: list[str]) -> bool:
    """Credits and a title page (producer lines, an illustration, title and author, perhaps a short reading list), with
    no real paragraph: never eight lines of running text in a row."""
    text = [l for l in lines if l.strip()]
    run = longest = 0
    for l in text:
        run = run + 1 if _prose(l) else 0
        longest = max(longest, run)
    return any(CREDITS.match(l) for l in text) and longest < 8


def _drop_running_title(M: list[str], start: int, end: int) -> int:
    """Where a front-matter part really ends: without the book's title set in capitals after its last paragraph ("THE
    ODYSSEY", "VIRGIL'S ÆNEID" over the first page of the text). A signature with initials ("S. BUTLER.") stays."""
    while end > start:
        s = M[end - 1].strip()
        if not s:
            end -= 1
        elif (s.upper() == s and re.search(r"[A-Z]{3}", s) and len(s) < 60 and not _prose(s)
              and not re.search(r"\b[A-Z]\.\s", s) and s[0] not in "(["):
            end -= 1
        else:
            break
    return end


def _front_name(name: str, first: str) -> str:
    """A part's name from its first line when that says what it is: "[Transcriber's Note: ..." is the transcriber's
    note; "[From Bell edition.]" and "[By Edward Brooks, Jr., from McKay edition.]" say whose introduction it is."""
    if BRACKET_NOTE.match(first):
        return _title_case(BRACKET_NOTE.match(first).group(1).replace("’", "'")).replace("'S ", "'s ")
    if ATTRIBUTION.match(first):
        who = ATTRIBUTION.match(first).group(1)
        who = who[0].lower() + who[1:]
        if who.startswith("by "):
            by, _, source = who.partition(", from ")
            return f"{name} {by}" + (f" (from {source})" if source else "")
        return f"{name} ({who})"
    return name


def _tidy_front(M: list[str], drafts: list[dict], volume_label: str | None) -> list[dict]:
    """Front matter named by what it is. Everything before the first heading used to be one "Preface", and a heading
    such as INTRODUCTION opened a section whatever followed it. Now each front-matter section is cut at its own
    headings (a label such as PREFACE TO SECOND EDITION with running text in the next few lines; in a table of
    contents the same words are followed by more entries) and each part is named by its heading, or by its first line
    when that names it. Tables of contents become "Contents" and credits pages "Title page" (the reader lists neither),
    the front matter of a second volume is marked with that volume ("Books VIII-XV"), and no two parts keep one name."""
    out = []
    for d in drafts:
        if d["category"] != "front_matter" or d["path"][0] == "Contents":
            out.append(d)
            continue
        cuts = [(d["start"], d["path"][-1])]
        for i in range(d["start"] + 1, d["end"]):
            s = M[i].strip()
            after = [M[j] for j in range(i + 1, min(i + 12, d["end"])) if M[j].strip()][:3]
            if FRONT_LABEL.match(s) and s.upper() == s and any(_prose(l) for l in after):
                cuts.append((i, _title_case(s.rstrip("."))))
        for (a, name), (b, _) in zip(cuts, cuts[1:] + [(d["end"], None)]):
            body = M[a + (1 if a != d["start"] else 0):b]
            text = [l for l in body if l.strip()]
            if not text:
                continue
            name = _front_name(name, text[0].strip())
            if a == d["start"] and _is_title_page(body):      # not a part that opens with its own heading
                out.append({**d, "path": ["Title page"], "start": a, "end": b})
                continue
            if _is_contents(body):
                out.append({**d, "path": ["Contents", name], "start": a, "end": b})
                continue
            # A list of contents at the end of a part (after a bibliography, say) is set apart too.
            tail = len(body)
            while tail > 0 and (not body[tail - 1].strip() or not _prose(body[tail - 1])):
                tail -= 1
            start = a + (1 if a != d["start"] else 0)
            # The part starts after its own heading, which the reader shows as the title (not again as the first line).
            if sum(1 for l in body[tail:] if l.strip()) >= 5:
                out.append({**d, "path": [name], "start": start, "end": _drop_running_title(M, start, start + tail)})
                out.append({**d, "path": ["Contents", name], "start": start + tail, "end": b})
            else:
                out.append({**d, "path": [name], "start": start, "end": _drop_running_title(M, start, b)})
    seen: dict[str, int] = {}
    for d in out:
        if d["category"] != "front_matter" or d["path"][0] in ("Contents", "Title page"):
            continue
        if volume_label:
            d["path"] = [volume_label] + d["path"]
        key = " > ".join(d["path"])
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            d["path"] = d["path"][:-1] + [f"{d['path'][-1]} ({seen[key]})"]
    return out


def _volume_labels(volumes) -> list[str | None]:
    """A label for each volume after the first of a book printed in several: the part of its title that differs from
    the others, with the word before it ("...Books I-VII" and "...Books VIII-XV" give "Books VIII-XV")."""
    if len(volumes) < 2:
        return [None] * len(volumes)
    titles = [v.title or "" for v in volumes]
    prefix = os.path.commonprefix(titles)
    prefix = prefix[: prefix.rfind(" ") + 1]
    last = prefix.rstrip().split(" ")[-1] if prefix.strip() else ""
    if last.isalpha():
        prefix = prefix[: prefix.rstrip().rfind(" ") + 1]
    return [None] + [(t[len(prefix):].strip() or f"Volume {k + 2}") for k, t in enumerate(titles[1:])]


def _move_late_notes(sections: list[Section]) -> None:
    """A note printed at the start of the chapter after the one that cites it (a page's notes that ran over a chapter
    heading) goes back: a note that its own section does not cite, whose number the section before cites with no note."""
    body = [s for s in sections if s.category == "body"]
    for before, here in zip(body, body[1:]):
        missing = set(re.findall(r"\[(\d+)\]", before.text)) - {f.label for f in before.footnotes}
        cited = set(re.findall(r"\[(\d+)\]", here.text))
        late = [f for f in here.footnotes if f.label in missing and f.label not in cited]
        if late:
            before.footnotes += late
            here.footnotes = [f for f in here.footnotes if f not in late]


def parse_book(source: str | Path, slug: str) -> ParsedBook:
    lines = Path(source).read_text(encoding="utf-8-sig").split("\n")
    volumes = find_volumes(lines)
    sections, reports, counts, title_notes = [], [], {}, {}

    def add(category, path, start, end, ref=None, notes=True):
        chunk, footnotes = extract_footnotes(lines[start:end], category == "body") if notes else (lines[start:end], [])
        text = _clean(chunk)
        if not text and not footnotes:
            return
        counts[category] = counts.get(category, 0) + 1
        sections.append(Section(f"{slug}-{category}-{counts[category]}", slug, category, " > ".join(path),
                                path, start + 1, end, text, ref, footnotes))
        return sections[-1]

    labels = dict(zip((id(v) for v in volumes), _volume_labels(volumes)))
    spans = []                                           # the sections of each volume, for the footnotes of each
    # Assemble in file order: boilerplate ranges and volumes interleaved.
    pieces = [(s, e, None) for s, e in boilerplate_ranges(lines, volumes)] + [(v.start, v.end, v) for v in volumes]
    for start, end, vol in sorted(pieces, key=lambda p: p[0]):
        if vol is None:
            add("boilerplate", ["Gutenberg boilerplate"], start, end, notes=False)
            continue
        report: dict = {"volume_title": vol.title}
        reports.append(report)
        first = len(sections)
        volume = lines[vol.start:vol.end]
        drafts = _tidy_front(volume, _volume_sections(volume, vol.start, slug, report), labels[id(vol)])
        for d in _split_long(volume, drafts):
            added = add(d["category"], d["path"], vol.start + d["start"], vol.start + d["end"], d["ref"])
            if added and d.get("note"):       # a footnote number printed on the work's title
                title_notes[added.id] = d["note"]
        spans.append((first, len(sections)))

    # Footnotes listed at the end of a volume and marked by bare numbers. Each printed volume has its own list, and its
    # own numbering, so they are restored one volume at a time (one volume's notes of another style do not stop it).
    _move_late_notes(sections)
    found = [m for a, b in spans if (m := restore_markers(sections[a:b], title_notes))]
    markers = ({"listed": sum(m["listed"] for m in found), "restored": sum(m["restored"] for m in found),
                "not_placed": [n for m in found for n in m["not_placed"]]} if found else None)
    report = {"volumes": reports, "sections": len(sections), "by_category": counts,
              "footnotes": sum(len(s.footnotes) for s in sections)}
    if markers:
        report["footnote_markers"] = markers
    for s in sections:          # a transliterated Greek word in a note too: "[Greek: Dios]" is "Dios"
        for f in s.footnotes:
            f.text = GREEK_RE.sub(r"\1", f.text)
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
