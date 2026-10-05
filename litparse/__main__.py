"""Command line for Pantheon Pal.

    python -m litparse add <name>             parse books/<name>/source.txt and print what was detected
    python -m litparse show <name>            list the parsed sections
    python -m litparse show <name> <id>       print one section (e.g. aeneid-body-3)
"""
import argparse
import json
import shutil
import sys
from dataclasses import asdict
from pathlib import Path

from litparse.builder import parse_book

ROOT = Path(__file__).resolve().parent.parent
BOOKS = ROOT / "books"
DATA = ROOT / "data"


def output_path(name: str) -> Path:
    return DATA / name / "output" / "sections.json"


def find_source(name: str) -> Path:
    """books/<name>/source.txt, or a loose books/<name>.txt which is moved into place."""
    source = BOOKS / name / "source.txt"
    loose = BOOKS / f"{name}.txt"
    if not source.exists() and loose.exists():
        source.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(loose), str(source))
        print(f"Moved {loose.name} to {source.relative_to(ROOT)}")
    if not source.exists():
        sys.exit(f"No book found. Put a Project Gutenberg .txt at {source.relative_to(ROOT)}")
    return source


def cmd_add(args) -> None:
    source = find_source(args.name)
    try:
        book = parse_book(source, args.name)
    except ValueError as e:
        sys.exit(f"{source.name}: {e}")
    out = output_path(args.name)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"title": book.title, "report": book.report,
                               "sections": [asdict(s) for s in book.sections]},
                              ensure_ascii=False, indent=1), encoding="utf-8")

    r = book.report
    print(f"{book.title or args.name}")
    for n, v in enumerate(r["volumes"], 1):
        if "warning" in v or "body_kind" not in v:
            print(f"  volume {n}: {'WARNING ' + v['warning'] if 'warning' in v else v.get('note', 'no headings')}")
        else:
            print(f"  volume {n}: main text is '{v['body_kind']}' headings, hierarchy {' > '.join(v['levels'])}"
                  f" ({v['body_headings']} {v['body_kind']} headings)")
    print("  sections:", ", ".join(f"{c} {n}" for c, n in r["by_category"].items()), f"(total {r['sections']})")
    print(f"  footnotes extracted: {r['footnotes']}")
    m = r.get("footnote_markers")
    if m:
        print(f"  (from the list at the end: {m['restored']} of {m['listed']} placed in the text"
              + (f"; not found: {', '.join(m['not_placed'])}" if m["not_placed"] else "") + ")")
    print(f"  saved to {out.relative_to(ROOT)}")
    print(f"  look at it with: python -m litparse show {args.name}")


def cmd_show(args) -> None:
    out = output_path(args.name)
    if not out.exists():
        sys.exit(f"Not parsed yet. Run: python -m litparse add {args.name}")
    sections = json.loads(out.read_text(encoding="utf-8"))["sections"]
    if args.section is None:
        for s in sections:
            if args.category in (None, s["category"]):
                print(f"{s['id']:<28} {s['category']:<13} {s['title']}")
        return
    match = next((s for s in sections if s["id"] == args.section), None)
    if match is None:
        sys.exit(f"No section '{args.section}'. List them with: python -m litparse show {args.name}")
    print(f"{match['title']}  [{match['category']}, lines {match['line_start']}-{match['line_end']}]")
    if match["reference"]:
        print(f"reference: {match['reference']}")
    print()
    print(match["text"])
    for f in match["footnotes"]:
        print(f"\n[{f['label']}] {f['text']}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="python -m litparse", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add", help="parse a book")
    add.add_argument("name", help="folder name under books/, e.g. aeneid")
    add.set_defaults(run=cmd_add)
    show = sub.add_parser("show", help="list sections, or print one")
    show.add_argument("name")
    show.add_argument("section", nargs="?", help="section id, e.g. aeneid-body-3")
    show.add_argument("--category", help="only list this category (e.g. body)")
    show.set_defaults(run=cmd_show)
    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
