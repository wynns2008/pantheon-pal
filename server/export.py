"""Write the reader as a static website: every answer the reader asks the server for, saved as a file, plus the
reader itself, in site/. Upload site/ to any static host (Cloudflare Pages, GitHub Pages).

    python -m server.export          rebuild site/ from data/ (free; nothing is asked of Claude)

The answers come from the server's own route functions, so the website shows exactly what `uvicorn server.app:app`
does. Three things depend on what the reader types or where they are, so the browser works them out from files
written here: the search (search-index.json), "Also in:" (elsewhere.json) and who is in the section being read
(tree-current.json, beside the book's one tree.json).
"""
import json
import shutil
from pathlib import Path

from fastapi import HTTPException

from server import app as server

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"


def write(path: str, data) -> None:
    file = SITE / "api" / f"{path}.json"
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def export_book(slug: str) -> int:
    """Every answer for one book; returns how many files were written."""
    count = 0

    def put(path, data):
        nonlocal count
        write(f"books/{slug}/{path}", data)
        count += 1
    put("toc", server.table_of_contents(slug))
    put("boilerplate", server.list_sections(slug, "boilerplate", True))
    put("about", server.get_about(slug))
    put("places", server.get_places(slug))
    put("items", server.get_items(slug))
    put("journey", server.get_journey(slug))
    put("book-starts", server.get_book_starts(slug))
    for s in server.load(slug)["sections"]:
        put(f"sections/{s['id']}", server.get_section(slug, s["id"]))
        put(f"annotations/{s['id']}", server.get_annotations(slug, s["id"]))
    try:
        tree = server.load_tree(slug)
    except HTTPException:
        return count                       # no family tree yet: no profiles either
    put("tree", server.get_tree(slug))
    put("tree-current", {sid: server.get_tree(slug, upto=sid)["current"] for sid in tree["sections"]})
    for p in tree["people"]:
        put(f"people/{p['id']}", server.get_references(slug, p["id"]))
    put("elsewhere", {f"{e['type']}:{e['id']}": rows for e in server.entries() if e["book"] == slug
                      if (rows := server.get_elsewhere(slug, e["type"], e["id"]))})
    return count


def main() -> None:
    if SITE.exists():
        shutil.rmtree(SITE)
    books = server.list_books()
    write("books", books)
    files = 1 + sum(export_book(b["slug"]) for b in books)
    # The search index: every character, place and thing, grouped by the being, place or thing they are (see server.entries).
    groups: dict[int, list] = {}
    for e in server.entries():
        groups.setdefault(e["key"], []).append({k: e[k] for k in ("type", "book", "book_title", "id", "name", "kind",
                                                                  "names", "weight", "about")})
    write("search-index", list(groups.values()))
    # The reader, told to read these files instead of asking a server.
    shutil.copytree(server.WEB, SITE / "static")
    page = (server.WEB / "index.html").read_text(encoding="utf-8")
    (SITE / "index.html").write_text(page.replace('<script src="/static/app.js">',
                                                  '<script>window.STATIC_SITE = true;</script>\n  <script src="/static/app.js">'),
                                     encoding="utf-8")
    # The welcome page at /welcome/ (a folder's index.html works on every host).
    (SITE / "welcome").mkdir()
    shutil.copy(server.WEB / "welcome.html", SITE / "welcome" / "index.html")
    size = sum(f.stat().st_size for f in SITE.rglob("*") if f.is_file()) / 1e6
    print(f"Wrote site/: {len(books)} books, {files + 1} data files, {size:.1f} MB. Upload site/ to a static host.")


if __name__ == "__main__":
    main()
