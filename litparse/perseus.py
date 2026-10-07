"""A text from the Perseus Digital Library (Tufts University), in the layout the parser reads.

    python -m litparse.perseus <name> --repo canonical-greekLit --path tlg0548/tlg001/tlg0548.tlg001.perseus-eng2.xml \\
        --title "The Library" --author Apollodorus --translator "Sir James George Frazer (Loeb Classical Library, 1921)"

Perseus publishes its texts as TEI XML on GitHub (github.com/PerseusDL), under Creative Commons Attribution-ShareAlike
4.0. This downloads one translation and writes books/<name>/source.txt: a header (title, author, translator, where
it came from and under what licence), a start marker, then BOOK, CHAPTER and numbered sections, with the translator's
footnotes as `[Footnote N: ...]`. After that it is a book like any other:

    python -m annotate.book <name> --parse-only

Only a translation that is itself in the public domain should be added (a CC licence on a file does not free a modern
translation): Frazer's Apollodorus is from 1921. The footer of the reader credits Perseus for such a book.
"""
import argparse
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = "https://raw.githubusercontent.com/PerseusDL/{repo}/master/data/{path}"
USER_AGENT = "PantheonPal/0.1 (personal reading companion; Python urllib)"
LICENCE = "Creative Commons Attribution-ShareAlike 4.0 (Perseus Digital Library); the translation itself is in the public domain"


def fetch(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(request, timeout=120).read().decode("utf-8")


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


class Converter:
    def __init__(self):
        self.notes: list[str] = []

    def inline(self, el: ET.Element) -> str:
        """The text of an element: a note becomes a marker "[N]" and joins self.notes; italics become _x_."""
        out = [el.text or ""]
        for child in el:
            name = local(child.tag)
            if name == "note":
                self.notes.append(squash("".join(child.itertext())).replace("[", "(").replace("]", ")"))
                out.append(f"[{len(self.notes)}]")
            elif name in ("pb", "milestone", "lb"):
                pass
            elif name in ("hi", "emph") and child.get("rend") in ("italic", "i", None):
                inner = self.inline(child).strip()
                out.append(f"_{inner}_" if inner else "")
            else:
                out.append(self.inline(child))
            out.append(child.tail or "")
        return "".join(out)

    def section(self, div: ET.Element) -> tuple[str, list[str]]:
        """A section's paragraphs, and the notes they cite (in order)."""
        before = len(self.notes)
        paragraphs = [squash(self.inline(p)) for p in div.iter() if local(p.tag) == "p"]
        return "\n\n".join(p for p in paragraphs if p), self.notes[before:]


def convert(xml_text: str, title: str, author: str, translator: str, source: str) -> str:
    xml_text = re.sub(r'\sxmlns="[^"]+"', "", xml_text, count=1)
    root = ET.fromstring(xml_text)
    body = next(e for e in root.iter() if local(e.tag) == "body")
    c = Converter()
    out = [f"Title: {title}", "", f"Author: {author}", "", f"Translator: {translator}", "", f"Source: {source}", "",
           f"Licence: {LICENCE}", "", "", "*** START OF THIS TEXT ***", "", "",
           title.upper(), "", f"by {author}", "", f"Translated by {translator}", "", ""]
    for book in (d for d in body.iter() if local(d.tag) == "div" and d.get("subtype") == "book"):
        out += [f"BOOK {book.get('n')}", ""]
        for chapter in (d for d in book if local(d.tag) == "div" and d.get("subtype") == "chapter"):
            out += [f"CHAPTER {chapter.get('n')}", ""]
            for section in (d for d in chapter if local(d.tag) == "div" and d.get("subtype") == "section"):
                text, notes = c.section(section)
                if not text:
                    continue
                out += [f"({book.get('n')}.{chapter.get('n')}.{section.get('n')}) {text}", ""]
                first = len(c.notes) - len(notes) + 1
                out += [line for k, note in enumerate(notes, first) for line in (f"[Footnote {k}: {note}]", "")]
    out += ["", "*** END OF THIS TEXT ***", ""]
    return "\n".join(out)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", help="the book's folder name under books/")
    ap.add_argument("--repo", required=True, help="PerseusDL repository, e.g. canonical-greekLit or canonical-latinLit")
    ap.add_argument("--path", required=True, help="the file's path under data/, e.g. tlg0548/tlg001/tlg0548.tlg001.perseus-eng2.xml")
    ap.add_argument("--title", required=True)
    ap.add_argument("--author", required=True)
    ap.add_argument("--translator", required=True)
    ap.add_argument("--yes", action="store_true", help="overwrite books/<name>/source.txt if it exists")
    args = ap.parse_args()
    target = ROOT / "books" / args.name / "source.txt"
    if target.exists() and not args.yes:
        sys.exit(f"{target.relative_to(ROOT)} exists. Add --yes to replace it.")
    url = RAW.format(repo=args.repo, path=args.path)
    text = convert(fetch(url), args.title, args.author, args.translator,
                   f"Perseus Digital Library, Tufts University: https://github.com/PerseusDL/{args.repo} ({args.path})")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    print(f"Saved {target.relative_to(ROOT)}: {len(text):,} characters, {text.count('[Footnote ')} footnotes, from {url}")
    print(f"Next: python -m annotate.book {args.name} --parse-only")


if __name__ == "__main__":
    main()
