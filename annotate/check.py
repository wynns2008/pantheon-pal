"""Check a book for the problems that have turned up before. Free: nothing is sent anywhere.

    python -m annotate.check <slug>            everything that exists so far: the parse, then annotations, tree, ...
    python -m annotate.check <slug> --parse    only the parse (use it before paying for annotation)

Each check prints one line: "ok", "note" (worth knowing, usually fine), "WARN" (look at it before going on) or
"FAIL" (the reader will show something wrong). Every check here exists because one of the first four books had
that fault: works called "Work 3", a last section that swallowed the endnotes, note numbers left in the text,
notes that were never joined to their sentences, a god with no key moments, a Wikipedia article about
something else.
"""
import argparse
import json
import re
import statistics
import sys
from collections import Counter

from annotate.merge import ROOT, norm
from annotate.schema import SCHEMA_VERSION
from litparse.__main__ import find_source
from litparse.sentences import split_sentences

PLACEHOLDER = re.compile(r"^(Work \d+|Text|\(\(.*\)\)|.*[A-Za-z] ?\d{3,})$")
NOTE_ENTRY = re.compile(r"^(\[\w{1,4}\]|\d{1,5} \(return\)|\[Footnote )", re.M)
NOTE_HEADING = re.compile(r"^(ENDNOTES|FOOTNOTES|NOTES)[.:]?$", re.M)
MARKER = re.compile(r"\[(\d+|[A-Z])\]")
STUCK_NUMBER = re.compile(r"[A-Za-z][.,;:!?’”]*\d{1,4}(?![\d\]])")
# Markup the reader does not turn into anything: it would show as typed.
UNHANDLED = {
    "[Illustration]": r"\[Illustration",
    "[Sidenote] or [Greek:] or [Transcriber]": r"\[(Sidenote|Greek|Transcriber|Note)\b",
    "HTML tags": r"</?[a-z]{1,6}>",
    "^superscript marks": r"\^\{?\w",
    "+bold+ or #bold# marks": r"(?<!\w)[+#][A-Za-z][^+#\n]{0,40}[+#](?!\w)",
    "| table rules": r"^\s*\|.*\|\s*$",
}
NOTE_FIELDS = ("language_notes", "context_notes", "literary_notes")

results: list[tuple[str, str]] = []


def say(level: str, text: str, examples: list | None = None) -> None:
    results.append((level, text))
    shown = "; ".join(str(e) for e in (examples or [])[:5]) if level != "ok" else ""
    print(f"  {level:5s} {text}" + (f"  e.g. {shown}" if shown else ""))


def read(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def check_parse(slug: str, book: dict) -> list[dict]:
    print("PARSE")
    sections = book["sections"]
    body = [s for s in sections if s["category"] == "body"]
    if not body:
        say("FAIL", "no section was recognised as the story itself")
        return body
    kinds = Counter(s["category"] for s in sections)
    say("ok", f"{len(body)} sections of story; also " + ", ".join(f"{n} {k}" for k, n in kinds.items() if k != "body"))

    odd = [" > ".join(s["path"]) for s in body if any(PLACEHOLDER.match(part) for part in s["path"])]
    say("WARN" if odd else "ok", f"{len(odd)} sections have a placeholder for a title" if odd else "section titles look like titles", odd)
    doubles = [t for t, n in Counter(" > ".join(s["path"]) for s in body).items() if n > 1]
    say("WARN" if doubles else "ok", f"{len(doubles)} titles are used by more than one section" if doubles else "no two sections share a title", doubles)

    sizes = [len(s["text"]) for s in body]
    middle = statistics.median(sizes)
    huge = [f"{' > '.join(s['path'])} ({len(s['text']):,} characters)" for s in body if len(s["text"]) > max(8 * middle, 15000)]
    say("WARN" if huge else "ok", f"{len(huge)} sections are far longer than the rest (median {int(middle):,} characters): "
        "something may have been swallowed" if huge else f"section sizes are even (median {int(middle):,} characters, longest {max(sizes):,})", huge)
    tiny = sum(1 for s in body if len(s["text"].split()) < 100)
    if tiny:
        say("note", f"{tiny} sections are under 100 words")

    lists = [" > ".join(s["path"]) for s in body if len(NOTE_ENTRY.findall(s["text"])) >= 5 or NOTE_HEADING.search(s["text"])]
    say("FAIL" if lists else "ok", f"{len(lists)} story sections contain a list of notes" if lists else "no list of notes inside the story", lists)

    try:
        lines = find_source(slug).read_text(encoding="utf-8-sig").split("\n")
        covered = set()
        for s in sections:
            covered.update(range(s["line_start"] - 1, s["line_end"]))
        first, last = min(covered), max(covered)
        lost = [i for i in range(first, last) if i not in covered and lines[i].strip() and lines[i].strip().upper() != lines[i].strip()]
        say("WARN" if lost else "ok", f"{len(lost)} lines of the file are in no section" if lost else "every line of the file is in a section (headings apart)",
            [f"line {i + 1}: {lines[i].strip()[:50]}" for i in lost])
    except (OSError, SystemExit):
        say("note", "the source file was not found, so coverage of the file was not checked")

    print("FOOTNOTES")
    notes = sum(len(s["footnotes"]) for s in body)
    orphans = [(s["id"], m) for s in body for m in MARKER.findall(s["text"]) if m not in {f["label"] for f in s["footnotes"]} and m.isdigit()]
    say("FAIL" if len(orphans) > 3 else "ok", f"{len(orphans)} markers in the text have no note to show" if len(orphans) > 3
        else f"{notes} notes are joined to their sentences" if notes else "the book has no footnotes (or none were found)",
        [f"[{m}] in {sid}" for sid, m in orphans])
    unplaced = (book["report"].get("footnote_markers") or {}).get("not_placed", [])
    if unplaced:
        say("note" if len(unplaced) < max(notes, 1) / 4 else "WARN", f"{len(unplaced)} listed notes were not placed (fine if they belong to the introduction)", unplaced)
    listed = sum(len(NOTE_ENTRY.findall(s["text"])) for s in sections if s["category"] != "body")
    if listed >= 10 and not notes:
        say("WARN", f"the book lists about {listed} notes outside the story, but none is joined to a sentence")
    stuck = [m.group() for s in body for m in STUCK_NUMBER.finditer(s["text"])]
    say("WARN" if len(stuck) > 10 else "ok", f"{len(stuck)} numbers are stuck to words: perhaps note numbers that were not recognised" if len(stuck) > 10
        else "no note numbers left in the sentences", stuck)

    print("FORMATTING")
    left = Counter()
    for s in body:
        for name, pattern in UNHANDLED.items():
            left[name] += len(re.findall(pattern, s["text"], flags=re.M))
    left = {k: v for k, v in left.items() if v}
    say("WARN" if left else "ok", "markup the reader shows as typed: " + ", ".join(f"{v} {k}" for k, v in left.items()) if left
        else "no markup that the reader cannot show")
    return body


def check_annotations(slug: str, out, body: list[dict], tree: dict | None) -> dict[str, int]:
    """Returns how many scenes each person of the tree is present in (for the key-moments check)."""
    print("ANNOTATIONS")
    known, owner = set(), {}
    for p in (tree or {}).get("people", []):
        for name in [p["name"], *p["aliases"]]:
            known.add(norm(name))
            owner[norm(name)] = p["name"]
    missing, old, problems, empty, strangers, appearances = [], 0, Counter(), 0, Counter(), Counter()
    scenes_total = unplaced = 0
    for s in body:
        saved = read(out / "annotations" / f"{s['id']}.json")
        if saved is None:
            missing.append(" > ".join(s["path"]))
            continue
        old += saved.get("schema") != SCHEMA_VERSION
        result, count = saved["result"], len(split_sentences(s["text"]))
        scenes = result.get("scenes", [])
        problems["sections without scenes"] += not scenes
        for a, b in zip(scenes, scenes[1:]):
            problems["scenes that overlap"] += b["start_sentence"] <= a["end_sentence"]
            problems["gaps between scenes"] += b["start_sentence"] > a["end_sentence"] + 1
        for sc in scenes:
            scenes_total += 1
            unplaced += sc["place"].get("lat") is None
            problems["scenes past the end of their section"] += not 0 <= sc["start_sentence"] <= sc["end_sentence"] < count
            lat, lon = sc["place"].get("lat"), sc["place"].get("lon")
            problems["map positions that are not on the globe"] += lat is not None and not (-90 <= lat <= 90 and -180 <= lon <= 180)
            for name in sc["present"]:
                if norm(name) in known:
                    appearances[owner[norm(name)]] += 1
                elif tree:
                    strangers[name] += 1
        cited = [n["sentence"] for field in (*NOTE_FIELDS, "notable_quotes") for n in result.get(field, [])]
        problems["notes that point past the end of their section"] += sum(not 0 <= i < count for i in cited)
        empty += not any(result.get(field) for field in NOTE_FIELDS)
    if len(missing) == len(body):
        say("note", "not annotated yet")
        return appearances
    say("FAIL" if missing else "ok", f"{len(missing)} sections are not annotated" if missing else f"all {len(body)} sections are annotated", missing)
    if old:
        say("WARN", f"{old} sections were annotated with an older prompt and lack what the reader now shows")
    bad = {k: v for k, v in problems.items() if v}
    serious = {"scenes that overlap", "scenes past the end of their section", "notes that point past the end of their section"}
    many = bad.get("sections without scenes", 0) or bad.get("gaps between scenes", 0) > scenes_total / 20
    say("FAIL" if serious & set(bad) else "WARN" if many else "note" if bad else "ok",
        ", ".join(f"{v} {k}" for k, v in bad.items()) if bad else f"{scenes_total} scenes and their notes sit inside their sections")
    if empty:
        say("WARN", f"{empty} sections have no notes on any sentence")
    if strangers:
        say("WARN" if len(strangers) > 10 else "note", f"{len(strangers)} names in scenes have no profile", list(strangers))
    if scenes_total:
        say("note", f"{unplaced} of {scenes_total} scenes have no place on the map ({100 * unplaced // scenes_total}%)")
    return appearances


def check_tree(out, tree: dict | None, appearances: dict[str, int]) -> None:
    print("FAMILY TREE, KEY MOMENTS, WIKIPEDIA")
    if tree is None:
        return say("note", "no family tree yet")
    name = {p["id"]: p["name"] for p in tree["people"]}
    parents: dict[str, list] = {}
    for e in tree["relationships"]:
        if e["relation"] == "parent_of":
            parents.setdefault(e["b"], []).append(e)
    three = {name[c]: [name[e["a"]] for e in es] for c, es in parents.items() if len(es) > 2}
    from_outside = [c for c, es in parents.items() if len(es) > 2 and any(e["source"] == "outside" for e in es)]
    if three:
        say("WARN" if from_outside else "note", f"{len(three)} people have three or more parents"
            + ("" if from_outside else " (each one stated by the book itself)"), [f"{c}: {', '.join(ps)}" for c, ps in three.items()])
    else:
        say("ok", f"{len(tree['people'])} people; nobody has more than two parents")
    blank = [p["name"] for p in tree["people"] if not (p["about"] or p["descriptions"]) and p["kind"] not in ("form", "place")]
    if blank:
        say("note", f"{len(blank)} people have no description", blank)
    said_file = ROOT / "data" / "pronunciations.json"
    said = json.loads(said_file.read_text(encoding="utf-8")) if said_file.exists() else {}
    unsaid = [p["name"] for p in tree["people"] if p["kind"] not in ("form", "place") and norm(p["name"]) not in said]
    if unsaid:
        say("WARN" if len(unsaid) > len(tree["people"]) / 10 else "note",
            f"{len(unsaid)} names have no pronunciation yet (python -m annotate.pronounce)", unsaid)
    if not (out / "wikidata.json").exists():
        say("note", "no Wikidata check: the family links are unverified and the book takes no part in cross-book links (--wikidata)")

    moments = read(out / "moments.json")
    if moments is None:
        say("note", "no key moments yet")
    else:
        have = {norm(n) for n in moments["people"]}
        people = {p["name"]: {norm(n) for n in [p["name"], *p["aliases"]]} for p in tree["people"]}
        missed = sorted(((n, k) for n, k in appearances.items() if k >= 5 and not people[n] & have), key=lambda x: -x[1])
        say("WARN" if missed and missed[0][1] >= 8 else "note" if missed else "ok",
            f"{len(missed)} characters are present in five or more scenes but have no key moments (a warning from eight scenes)" if missed
            else f"{len(moments['people'])} characters have key moments; nobody who is in five scenes or more was left out",
            [f"{n} ({k} scenes)" for n, k in missed])

    about = read(out / "about.json")
    if about is None:
        return say("note", "no Wikipedia background yet")
    sections = read(out / "sections.json")["sections"]
    body = [s for s in sections if s["category"] == "body"]
    without = sorted({s["path"][0] for s in body if s["id"] not in about["sections"]}) if len({s["path"][0] for s in body}) > 1 \
        and not all(re.match(r"(Book|Chapter|Part|Canto|Act)\b", s["path"][0]) for s in body) else \
        ([] if about["works"] else ["the whole book"])
    covered = sum(1 for s in body if s["id"] in about["sections"])
    say("FAIL" if not about["works"] else "WARN" if covered < len(body) / 2 else "ok",
        f"Wikipedia: {len(about['works'])} articles cover {covered} of {len(body)} sections"
        + (f"; {len(without)} works have none" if without else ""), without)
    stubs = [item["heading"] for w in about["works"].values() for kind in ("summaries", "themes", "history", "primary", "secondary", "tertiary")
             for item in w[kind] if re.match(r"(Source|Main article|See also|Further information)s?:", item["text"])]
    if stubs:
        say("WARN", f"{len(stubs)} pieces of Wikipedia text open with a leftover label such as \"Source:\"", stubs)
    say("note", "read the About page: an article can be about something else of the same name ("
        + ", ".join(sorted(about["works"])[:8]) + (", ..." if len(about["works"]) > 8 else "") + ")")


def check(slug: str, parse_only: bool = False) -> tuple[int, int]:
    """Print the checks for one book; returns (warnings, failures)."""
    results.clear()
    out = ROOT / "data" / slug / "output"
    book = read(out / "sections.json")
    if book is None:
        sys.exit(f"No parsed book '{slug}'. Parse it first: python -m litparse add {slug}")
    print(f"Checking {book.get('title') or slug}")
    body = check_parse(slug, book)
    if not parse_only and body:
        tree = read(out / "tree.json")
        appearances = check_annotations(slug, out, body, tree)
        check_tree(out, tree, appearances)
    warnings, failures = (sum(1 for level, _ in results if level == kind) for kind in ("WARN", "FAIL"))
    print(f"{failures} failed, {warnings} to look at." if warnings or failures else "Nothing to fix.")
    return warnings, failures


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug")
    ap.add_argument("--parse", action="store_true", help="only check the parse")
    args = ap.parse_args()
    check(args.slug, args.parse)


if __name__ == "__main__":
    main()
