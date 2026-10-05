"""Ask the server for everything the reader could ask for, in every book, and report what breaks. Free.

    python -m annotate.sweep            every book
    python -m annotate.sweep <slug>     one book

It calls the server's own route functions (no server needs to be running): the contents, every section in the
contents with its annotations, the map's journey, the Wikipedia background, the family tree as seen from every
section, and every character's profile references. Then it checks that the pieces point at each other properly:
every link from a profile, a key moment or the cross-book index must lead to a section and a scene that exist.
`annotate.check` looks at one book's data for known faults; this looks for anything the reader would fail to open.
"""
import json
import sys
import time

from fastapi import HTTPException

from annotate.merge import ROOT
from server import app as server

problems: list[str] = []


def call(what: str, function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except HTTPException as e:
        problems.append(f"{what}: {e.status_code} {e.detail}")
    except Exception as e:                      # a crash in a route is exactly what this is looking for
        problems.append(f"{what}: {type(e).__name__}: {e}")
    return None


def flatten(nodes: list[dict]) -> list[dict]:
    out = []
    for node in nodes:
        if "id" in node:
            out.append(node)
        out += flatten(node["children"])
    return out


def sweep(slug: str) -> None:
    started, before = time.time(), len(problems)
    book = server.load(slug)
    ids = {s["id"] for s in book["sections"]}
    built = (ROOT / "data" / slug / "output" / "tree.json").exists()      # a book only parsed so far has no tree or profiles
    contents = flatten(call(f"{slug} contents", server.table_of_contents, slug) or [])
    scenes: dict[str, int] = {}
    for node in contents:
        section = call(f"{slug} {node['id']}", server.get_section, slug, node["id"])
        notes = call(f"{slug} {node['id']} annotations", server.get_annotations, slug, node["id"])
        if not section or not notes:
            continue
        for end in ("prev", "next"):
            if section[end] and section[end]["id"] not in ids:
                problems.append(f"{slug} {node['id']}: its {end} section does not exist")
        if not section["text"].strip():
            problems.append(f"{slug} {node['id']}: the section is empty")
        if notes["annotated"]:
            scenes[node["id"]] = len(notes["annotations"].get("scenes", []))
            labels = {f["label"] for f in section["footnotes"]}
            for sentence in notes["sentences"]:
                if set(sentence["footnotes"]) - labels:
                    problems.append(f"{slug} {node['id']}: sentence {sentence['i']} cites a note the section does not have")
        if node["category"] == "body" and built:
            call(f"{slug} tree at {node['id']}", server.get_tree, slug, upto=node["id"])
    if not built:
        found = len(problems) - before
        return print(f"{server.short_title(slug)}: {len(contents)} sections opened; not annotated yet, so there is no tree "
                     f"or profile to follow; {found} problem{'s' if found != 1 else ''}")
    for stop in call(f"{slug} journey", server.get_journey, slug) or []:
        if stop["section"] not in ids:
            problems.append(f"{slug} map: a place belongs to the missing section {stop['section']}")
    about = call(f"{slug} background", server.get_about, slug) or {"works": {}, "sections": {}}
    for sid, work in about["sections"].items():
        if sid not in ids or work not in about["works"]:
            problems.append(f"{slug} background: section {sid} is tied to '{work}', which is missing")
    tree = call(f"{slug} whole tree", server.get_tree, slug) or {"people": [], "others": []}
    people = tree["people"] + tree["others"]
    links = 0
    for person in people:
        refs = call(f"{slug} profile of {person['name']}", server.get_references, slug, person["id"])
        for m in (refs or {}).get("here", []) + (refs or {}).get("scenes", []):
            links += 1
            if m["section"] not in ids:
                problems.append(f"{slug} profile of {person['name']}: a link leads to the missing section {m['section']}")
            elif m.get("scene") is not None and m["scene"] >= scenes.get(m["section"], 0):
                problems.append(f"{slug} profile of {person['name']}: a link leads to scene {m['scene']} of {m['section']}, which has fewer")
        for m in (refs or {}).get("elsewhere", []):
            links += 1
            other = {s["id"] for s in server.load(m["book"])["sections"]}
            if m["section"] not in other:
                problems.append(f"{slug} profile of {person['name']}: a link into {m['book']} leads to the missing section {m['section']}")
    found = len(problems) - before
    print(f"{server.short_title(slug)}: {len(contents)} sections, {len(people)} profiles, {links} links followed in "
          f"{time.time() - started:.0f} s; {found} problem{'s' if found != 1 else ''}")


def library() -> None:
    file = ROOT / "data" / "library.json"
    if not file.exists():
        return print("No cross-book index (python -m annotate.library).")
    saved = json.loads(file.read_text(encoding="utf-8"))
    before = len(problems)
    for being in saved["beings"]:
        for member in being["members"]:
            tree = server.load_tree(member["book"])
            if not any(p["id"] == member["id"] for p in tree["people"]):
                problems.append(f"cross-book index: {being['label']} names {member['id']}, who is not in {member['book']}")
        for moment in being["moments"]:
            if moment["section"] not in {s["id"] for s in server.load(moment["book"])["sections"]}:
                problems.append(f"cross-book index: {being['label']} has a moment in the missing section {moment['section']}")
    found = len(problems) - before
    print(f"Cross-book index: {len(saved['beings'])} shared characters across {len(saved['books'])} books; "
          f"{found} problem{'s' if found != 1 else ''}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    slugs = sys.argv[1:] or [b["slug"] for b in server.list_books()]
    for slug in slugs:
        sweep(slug)
    library()
    for line in problems[:60]:
        print("  " + line)
    if len(problems) > 60:
        print(f"  ... and {len(problems) - 60} more")
    print("Everything opens." if not problems else f"{len(problems)} problems.")


if __name__ == "__main__":
    main()
