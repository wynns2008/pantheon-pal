"""A text from English Wikisource, in the layout the parser reads.

    python -m litparse.wikisource <name> --title Thebaid --author Statius \\
        --translator "John Henry Mozley (Loeb Classical Library, 1928)" \\
        --source "https://en.wikisource.org/wiki/Statius_(Mozley_1928)_v1" \\
        --licence "Public domain (published 1928); transcription from English Wikisource (CC BY-SA 4.0)" \\
        --section "Book 1=Statius (Mozley 1928) v1/Thebaid/Book 1" --section "Book 2=Statius (Mozley 1928) v1/Thebaid/Book 2"

Wikisource holds proofread transcriptions of scanned books, among them many Loeb Classical Library volumes from before
1931 (public domain in the United States). Each --section is a heading for the book (LABEL) and the title of the
Wikisource page that holds its text. A page is fetched rendered, with the scan pages it transcludes: only the English of
the translation is kept (Loeb prints the original on the facing pages, which the page leaves out), with speaker names
and stage directions, and Wikisource's footnotes become `[Footnote N: ...]`. The result is books/<name>/source.txt with
a header (title, author, translator, source, licence), a start marker and one BOOK heading per section. After that:

    python -m annotate.book <name> --parse-only

Add only a translation that is itself in the public domain; Wikisource's own contributions are CC BY-SA 4.0, which the
README's Licence section and the reader's footer credit.
"""
import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
API = "https://en.wikisource.org/w/api.php"
USER_AGENT = "PantheonPal/0.1 (personal reading companion; Python urllib)"
PAUSE = 3.0          # seconds between requests (Wikisource answers 429 to a faster pace)
HEADING = re.compile(r"^(?:THE\s+)?[A-Z][A-Z' ,.-]{2,40}$|^(?:BOOK|CHAPTER|ACT)\s+[IVXLC0-9]+\W*$", re.I)
DROP = ["style", "script", ".wst-header", ".ws-noexport", ".noprint", ".mw-editsection", ".pagenum", ".ws-pagenum",
        ".mw-empty-elt", ".prp-pages-output > .mw-collapsible", "#toc", ".toc"]


def render(page: str) -> str:
    """The HTML of a Wikisource page, with the scan pages it transcludes."""
    url = API + "?" + urllib.parse.urlencode({"action": "parse", "page": page, "prop": "text", "format": "json",
                                              "disablelimitreport": "1", "redirects": "1"})
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(6):
        try:
            data = json.loads(urllib.request.urlopen(request, timeout=90).read().decode("utf-8"))
            break
        except urllib.error.HTTPError as e:       # "429 Too Many Requests": wait as long as asked, or a little longer each time
            if e.code not in (429, 503) or attempt == 5:
                raise
            wait = int(e.headers.get("Retry-After") or 0) or 20 * (attempt + 1)
            print(f"  Wikisource asks for a pause: waiting {wait} s")
            time.sleep(wait)
    if "error" in data:
        sys.exit(f"Wikisource: {page}: {data['error'].get('info')}")
    time.sleep(PAUSE)
    return data["parse"]["text"]["*"]


def squash(text: str) -> str:
    return re.sub(r"[ \t​]+", " ", re.sub(r"\s*\n\s*", "\n", text)).strip()


def clean(html: str) -> tuple[str, list[str]]:
    """The page's text with `[N]` footnote markers, and its footnotes."""
    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one(".mw-parser-output") or soup
    for selector in DROP:
        for el in root.select(selector):
            el.decompose()
    notes = []
    for ol in root.select("ol.references"):
        for li in ol.select("li"):
            for back in li.select(".mw-cite-backlink"):
                back.decompose()
            notes.append(squash(li.get_text(" ")).replace("[", "(").replace("]", ")"))
        ol.decompose()
    for sup in root.select("sup.reference"):
        m = re.search(r"(\d+)", sup.get_text())
        sup.replace_with(f"[{m.group(1)}]" if m else "")
    paragraphs = []
    for p in root.find_all(["p", "div", "li"], recursive=True):
        if p.name == "div" and p.find(["p", "div"]):
            continue                                   # a container: its paragraphs are taken on their own
        for br in p.select("br"):
            br.replace_with("\n")
        text = squash(p.get_text(""))
        if text:
            paragraphs.append(text)
    # Leading lines that only repeat the work's or the book's title are dropped (their footnote markers move to the text).
    paragraphs = [p for p in paragraphs if not re.fullmatch(r"Layout \d", p)]
    # A short line printed in capitals ("ARGUMENT", "DRAMATIS PERSONAE") would be read as a heading, and "Argument" as the
    # start of commentary: it is set in normal case.
    paragraphs = [p.title() if len(p) <= 40 and p == p.upper() and re.search(r"[A-Z]{3}", p) else p for p in paragraphs]
    markers = ""
    while paragraphs and len(paragraphs[0]) < 60 and HEADING.match(re.sub(r"\[\d+\]", "", paragraphs[0]).strip()):
        markers += "".join(re.findall(r"\[\d+\]", paragraphs.pop(0)))
    if paragraphs:
        paragraphs[0] = markers + paragraphs[0]
    return "\n\n".join(paragraphs), notes


def convert(title: str, author: str, translator: str, source: str, licence: str, sections: list[tuple[str, str]],
            numbered: bool = False) -> str:
    out = [f"Title: {title}", "", f"Author: {author}", "", f"Translator: {translator}", "", f"Source: {source}", "",
           f"Licence: {licence}", "", "", "*** START OF THIS TEXT ***", "", "",
           title.upper(), "", f"by {author}", "", f"Translated by {translator}", "", ""]
    for n, (label, page) in enumerate(sections, 1):
        text, notes = clean(render(page))
        # Books are headed BOOK 1; a work with titles of its own (the plays of a dramatist) is headed "1. AGAMEMNON", which
        # the parser reads as a numbered title.
        out += [f"{n}. {label.upper()}" if numbered else label.upper(), ""]
        # Each footnote follows the paragraph that cites it (a long book is cut into parts, and a note must stay with its text).
        placed = set()
        for paragraph in text.split("\n\n"):
            out += [paragraph, ""]
            for k in (int(m) for m in re.findall(r"\[(\d+)\]", paragraph)):
                if 0 < k <= len(notes) and k not in placed:
                    placed.add(k)
                    out += [f"[Footnote {k}: {notes[k - 1]}]", ""]
        out += [line for k, note in enumerate(notes, 1) if k not in placed for line in (f"[Footnote {k}: {note}]", "")]
        print(f"  {label}: {len(text.split()):,} words, {len(notes)} notes")
    out += ["", "*** END OF THIS TEXT ***", ""]
    return "\n".join(out)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", help="the book's folder name under books/")
    ap.add_argument("--title", required=True)
    ap.add_argument("--author", required=True)
    ap.add_argument("--translator", required=True)
    ap.add_argument("--source", required=True, help="the work's Wikisource address, credited in the footer")
    ap.add_argument("--licence", required=True)
    ap.add_argument("--section", action="append", required=True, metavar="LABEL=PAGE", help="one per book; repeat")
    ap.add_argument("--numbered", action="store_true", help='head each section "1. LABEL" (works whose parts have titles, such as plays)')
    ap.add_argument("--yes", action="store_true", help="overwrite books/<name>/source.txt if it exists")
    args = ap.parse_args()
    target = ROOT / "books" / args.name / "source.txt"
    if target.exists() and not args.yes:
        sys.exit(f"{target.relative_to(ROOT)} exists. Add --yes to replace it.")
    sections = [tuple(s.split("=", 1)) for s in args.section]
    text = convert(args.title, args.author, args.translator, f"English Wikisource: {args.source}", args.licence, sections, args.numbered)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    print(f"Saved {target.relative_to(ROOT)}: {len(text):,} characters, {text.count('[Footnote ')} footnotes")
    print(f"Next: python -m annotate.book {args.name} --parse-only")


if __name__ == "__main__":
    main()
