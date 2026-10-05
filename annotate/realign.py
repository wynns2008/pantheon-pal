"""Parse a book again without losing its annotations.

    python -m annotate.realign <slug>          show what would change; writes nothing
    python -m annotate.realign <slug> --yes    parse again and move every note to its sentence's new number

Annotations point at sentences by number. When the parser improves (say it learns to read a book's footnote
markers) the same text can split into sentences differently, and the numbers shift. This parses the book again,
works out where each old sentence went by comparing the letters of the text, renumbers the saved annotations to
match, and then saves the new sections.json. A section whose wording has changed cannot be matched; it is
reported and left alone (annotate it again). Nothing is sent to Claude.
"""
import argparse
import json
import re
import sys
from dataclasses import asdict

from annotate.run import ROOT, annotations_dir
from litparse.__main__ import find_source
from litparse.builder import parse_book
from litparse.sentences import split_sentences

NOTE_FIELDS = ("language_notes", "context_notes", "literary_notes", "notable_quotes")


def letters(text: str) -> str:
    return re.sub(r"[^A-Za-z]", "", text)


def sentence_map(old_text: str, new_text: str) -> list[tuple[int, int]] | None:
    """For each old sentence, the first and last new sentence that hold its words. None if the wording differs."""
    old, new = split_sentences(old_text), split_sentences(new_text)
    if letters("".join(s.text for s in old)) != letters("".join(s.text for s in new)):
        return None
    ends, total = [], 0                # the letter count at which each new sentence ends
    for s in new:
        total += len(letters(s.text))
        ends.append(total)
    out, position, j = [], 0, 0
    for s in old:
        size = len(letters(s.text))
        while j < len(new) - 1 and ends[j] <= position:
            j += 1
        first, last = j, j
        while last < len(new) - 1 and ends[last] < position + size:
            last += 1
        out.append((first, last))
        position += size
    return out


def renumber(result: dict, where: list[tuple[int, int]]) -> None:
    for field in NOTE_FIELDS:
        for note in result.get(field, []):
            note["sentence"] = where[note["sentence"]][0]
    for r in result.get("relationships", []):
        r["evidence_sentence"] = where[r["evidence_sentence"]][0]
    for scene in result.get("scenes", []):
        scene["start_sentence"], scene["end_sentence"] = where[scene["start_sentence"]][0], where[scene["end_sentence"]][1]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug")
    ap.add_argument("--yes", action="store_true", help="save the new sections and the renumbered annotations")
    args = ap.parse_args()

    sections_file = ROOT / "data" / args.slug / "output" / "sections.json"
    old = {s["id"]: s for s in json.loads(sections_file.read_text(encoding="utf-8"))["sections"]}
    book = parse_book(find_source(args.slug), args.slug)
    moved, same, lost, updates = 0, 0, [], {}
    for s in book.sections:
        file = annotations_dir(args.slug) / f"{s.id}.json"
        if not file.exists() or s.id not in old or old[s.id]["text"] == s.text:
            same += 1
            continue
        where = sentence_map(old[s.id]["text"], s.text)
        if where is None:
            lost.append(s.id)
            continue
        saved = json.loads(file.read_text(encoding="utf-8"))
        if any(a != (i, i) for i, a in enumerate(where)):
            renumber(saved["result"], where)
            moved += 1
        updates[file] = saved
    print(f"{book.title}: {len(book.sections)} sections; {len(updates)} annotated sections changed, "
          f"{moved} of them need renumbering; {book.report['footnotes']} footnotes.")
    for sid in lost:
        print(f"  {sid}: the wording changed, so its notes cannot be moved. Delete its annotation file and annotate it again.")
    if not args.yes:
        return print(f"Nothing written. To apply: python -m annotate.realign {args.slug} --yes")
    for file, saved in updates.items():
        file.write_text(json.dumps(saved, ensure_ascii=False, indent=1), encoding="utf-8")
    sections_file.write_text(json.dumps({"title": book.title, "report": book.report,
                                         "sections": [asdict(s) for s in book.sections]}, ensure_ascii=False, indent=1),
                             encoding="utf-8")
    print(f"Saved. Now rebuild the tree: python -m annotate.merge {args.slug}")


if __name__ == "__main__":
    main()
