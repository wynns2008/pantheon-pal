"""Backend API: serves parsed books to the web reader. Run: uvicorn server.app:app --reload

Every route is scoped by book slug (the folder name under books/, e.g. "aeneid"). Data comes from
data/<slug>/output/sections.json, written by `python -m litparse add <slug>`.
"""
import json
import re
import urllib.parse
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from annotate.merge import apply_namesakes, norm
from litparse.sentences import split_sentences

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
WEB = ROOT / "web"
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
SUMMARY = ("id", "category", "title", "path", "line_start", "line_end", "reference")

app = FastAPI(title="Pantheon Pal")
app.mount("/static", StaticFiles(directory=WEB), name="static")
_cache: dict[str, tuple[float, dict]] = {}
_tree_cache: dict[str, tuple[float, dict]] = {}


def load(slug: str) -> dict:
    """The parsed book, re-read only when its file changes."""
    path = DATA / slug / "output" / "sections.json"
    if not SLUG_RE.match(slug) or not path.exists():
        raise HTTPException(404, f"Unknown book '{slug}'. Parse it first: python -m litparse add {slug}")
    mtime = path.stat().st_mtime
    if slug not in _cache or _cache[slug][0] != mtime:
        _cache[slug] = (mtime, json.loads(path.read_text(encoding="utf-8")))
    return _cache[slug][1]


_said: tuple[float, dict] = (0.0, {})


def pronunciations() -> dict:
    """How each name is said (python -m annotate.pronounce), re-read when the file changes."""
    global _said
    file = DATA / "pronunciations.json"
    if not file.exists():
        return {}
    if _said[0] != file.stat().st_mtime:
        _said = (file.stat().st_mtime, json.loads(file.read_text(encoding="utf-8")))
    return _said[1]


def load_tree(slug: str) -> dict:
    """The merged family tree, re-read only when its file changes."""
    path = DATA / slug / "output" / "tree.json"
    if not SLUG_RE.match(slug) or not path.exists():
        raise HTTPException(404, f"No family tree for '{slug}'. Build it with: python -m annotate.merge {slug}")
    mtime = path.stat().st_mtime
    if slug not in _tree_cache or _tree_cache[slug][0] != mtime:
        _tree_cache[slug] = (mtime, json.loads(path.read_text(encoding="utf-8")))
    return _tree_cache[slug][1]


def short_title(slug: str) -> str:
    """The book's title without the catalogue's additions: "The Æneid of Virgil translated into English prose" is
    "The Æneid", "The Metamorphoses of Ovid, Books I-VII + VIII-XV" is "The Metamorphoses"."""
    book = load(slug)
    title = book["title"] or slug
    title = re.split(r";|\s+translated\s|,?\s+(?:books?|vol(?:ume)?s?\.?|parts?)\s+[ivxlc\d]", title, flags=re.I)[0]
    header = book["sections"][0]["text"] if book["sections"] else ""
    author = re.search(r"^Author:\s*(.+)$", header, re.M)
    if author:       # "of Ovid" only when Ovid is the author: "The Count of Monte Cristo" keeps its ending
        ending = re.search(r"\s+(?:of|by)\s+(.+)$", title)
        # ...and not when what is left is only "The Extant Odes" or "The Plays": then the author is part of the name.
        generic = re.search(r"\b(?:odes|poems|works|hymns|plays|tragedies|fragments)$", title[:ending.start()], re.I) if ending else None
        if ending and not generic and ending.group(1).strip().lower() in author.group(1).lower():
            title = title[:ending.start()]
    return title.strip(" ,.") or slug


def summary(section: dict) -> dict:
    return {k: section[k] for k in SUMMARY}


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(WEB / "index.html")


@app.get("/welcome", include_in_schema=False)
@app.get("/welcome/", include_in_schema=False)
def welcome():
    """The welcome page: what Pantheon Pal is, why it exists and who it is for. A first visit to / starts here."""
    return FileResponse(WEB / "welcome.html")


@app.get("/api/books")
def list_books():
    """Every annotated book with its title and section counts. A book that is parsed but not annotated yet (no
    tree.json) is left out of the picker, the search and the published site until its annotations are done."""
    books = []
    for f in sorted(DATA.glob("*/output/sections.json")):
        slug = f.parent.parent.name
        if not (f.parent / "tree.json").exists():
            continue
        book = load(slug)
        books.append({"slug": slug, "title": short_title(slug), "sections": book["report"]["sections"],
                      "by_category": book["report"]["by_category"]})
    return books


def cast_names(slug: str) -> set[str]:
    """Every name of every character of a book, spelling-insensitive."""
    try:
        people = load_tree(slug)["people"]
    except HTTPException:
        return set()
    return {k for p in people if p["kind"] not in ("place", "form") for n in [p["name"], *p["aliases"]] if len(k := norm(n)) > 2}


def place_aliases(place: dict, cast: set[str]) -> list[str]:
    """The other names of a place worth showing and searching: short ones that are names, not the note-taker's
    descriptions ("Ulysses' house, Ithaca") nor a part named after someone else ("Telemachus' room" is a room of
    Ulysses' house, and must not make it come up for Telemachus)."""
    main = f" {norm(place['name'])} "
    return [n for n in place["names"] if "," not in n and len(n.split()) <= 3
            and not any(f" {c} " in f" {norm(n)} " and c != norm(n) and f" {c} " not in main for c in cast)]


_entries: tuple[tuple, list] = ((), [])


def entries() -> list[dict]:
    """Every character, place and thing of every book, each with a `key` shared by all entries for one being, place
    or thing: entries matched to the same Wikipedia article, or joined by the library index (python -m
    annotate.library: Jove in the Iliad is Zeus in Hesiod), share a key; a name alone never joins two books.
    Characters join only characters, places only places and things only things, so Tartarus the god (Hesiod) and
    Tartarus the place stay apart, though they share an article. Re-read when any book changes."""
    global _entries
    files = [f for f in sorted(DATA.glob("*/output/*.json")) if f.name in ("tree.json", "about.json", "places.json", "items.json")]
    files += [DATA / "library.json"] if (DATA / "library.json").exists() else []
    stamp = tuple((str(f), f.stat().st_mtime) for f in files)
    if _entries[0] == stamp:
        return _entries[1]
    found, links = [], []          # links: (entry number, evidence), joined below
    # A place named exactly as the Wikipedia article that a place of that name links to elsewhere is that place:
    # Riley's Tartarus (his word for the whole underworld, linked to "Underworld") is still Tartarus.
    titled = {}
    for file in DATA.glob("*/output/places.json"):
        for p in json.loads(file.read_text(encoding="utf-8"))["places"]:
            url = (p["wikipedia"] or {}).get("url", "")
            if url and norm(urllib.parse.unquote(url.rsplit("/", 1)[1]).replace("_", " ")) == norm(p["name"]):
                titled[norm(p["name"])] = url
    beings = {}
    if (DATA / "library.json").exists():
        for n, being in enumerate(json.loads((DATA / "library.json").read_text(encoding="utf-8"))["beings"]):
            for m in being["members"]:
                beings[(m["book"], m["id"])] = f"being:{n}"
    for book in list_books():
        slug, title = book["slug"], book["title"]
        about = DATA / slug / "output" / "about.json"
        pages = json.loads(about.read_text(encoding="utf-8")).get("characters", {}) if about.exists() else {}
        try:
            people = load_tree(slug)["people"]
        except HTTPException:
            people = []
        for p in people:
            if p["first_index"] is None or p["kind"] in ("place", "form"):
                continue
            url = next((pages[q]["url"] for q in p.get("wikidata", []) if pages.get(q, {}).get("url")), "")
            links += [(len(found), ("person", e)) for e in (url, beings.get((slug, p["id"]))) if e]
            found.append({"type": "person", "book": slug, "book_title": title, "id": p["id"], "name": p["name"],
                          # epithets too, so "son of Latona" finds Apollo
                          "names": [p["name"], *p["aliases"], *p.get("epithets", [])], "kind": p["kind"], "weight": p["section_count"],
                          "about": p["about"] or (p["descriptions"][0]["text"] if p["descriptions"] else "")})
        file = DATA / slug / "output" / "places.json"
        cast = cast_names(slug)
        for p in json.loads(file.read_text(encoding="utf-8"))["places"] if file.exists() else []:
            url = titled.get(norm(p["name"])) or (p["wikipedia"] or {}).get("url")
            if url:
                links.append((len(found), ("place", url)))
            found.append({"type": "place", "book": slug, "book_title": title, "id": p["id"], "name": p["name"],
                          "names": [p["name"], *place_aliases(p, cast)], "kind": p["kind"],
                          "weight": len(p["scenes"]) + len(p["named"]), "about": p["description"]})
        file = DATA / slug / "output" / "items.json"
        for t in json.loads(file.read_text(encoding="utf-8"))["items"] if file.exists() else []:
            if t["wikipedia"]:
                links.append((len(found), ("item", t["wikipedia"]["url"])))
            found.append({"type": "item", "book": slug, "book_title": title, "id": t["id"], "name": t["name"],
                          "names": [t["name"], *t["names"]], "kind": t["kind"],
                          "weight": len(t["scenes"]) + len(t["named"]), "about": t["description"]})
    # Entries sharing any piece of evidence are one being or place (union-find).
    parent = list(range(len(found)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    first = {}
    for i, evidence in links:
        if evidence in first:
            parent[root(i)] = root(first[evidence])
        else:
            first[evidence] = i
    for i, e in enumerate(found):
        e["key"] = root(i)
    _entries = (stamp, found)
    return found


def entry_row(e: dict) -> dict:
    return {k: e[k] for k in ("type", "book", "book_title", "id", "name", "kind")}


@app.get("/api/search")
def search(q: str, book: str = ""):
    """Characters, places and things with a name, or another name, containing the words typed, spelling-insensitive
    ("aeneas" finds Æneas). One row per being, place or thing, however many books it is in (see entries): it opens in
    `book`, the book being read, if it is there, and lists its `books`. Names that start with the words come first,
    then names with a word that does, then the rest; within each the most present first."""
    key = norm(q)
    if len(key) < 2:
        return []
    groups: dict[int, list] = {}
    for e in entries():
        groups.setdefault(e["key"], []).append(e)
    found = []
    for members in groups.values():
        hits = [(0 if norm(n).startswith(key) else 1 if f" {key}" in f" {norm(n)}" else 2, n, e)
                for e in members for n in e["names"] if key in norm(n)]
        if not hits:
            continue
        rank, hit, _ = min(hits, key=lambda h: h[:2])
        main = next((e for e in members if e["book"] == book), max(members, key=lambda e: e["weight"]))
        found.append((rank, -sum(e["weight"] for e in members), {
            **entry_row(main), "as": "" if hit == main["name"] else hit, "about": main["about"],
            "books": [entry_row(e) for e in members]}))
    return [f for _, _, f in sorted(found, key=lambda f: f[:2])][:40]


@app.get("/api/books/{slug}/elsewhere")
def get_elsewhere(slug: str, type: str, id: str):
    """The same character, place or thing in the other books (see entries): [{type, book, book_title, id, name, kind}]."""
    mine = next((e for e in entries() if e["book"] == slug and e["type"] == type and e["id"] == id), None)
    if mine is None:
        return []
    return [entry_row(e) for e in entries() if e["key"] == mine["key"] and e is not mine]


@app.get("/api/books/{slug}/sections")
def list_sections(slug: str, category: str | None = None, include_text: bool = False):
    """Section index (no text unless asked), optionally limited to one category such as 'body'."""
    out = []
    for s in load(slug)["sections"]:
        if category in (None, s["category"]):
            out.append(s if include_text else summary(s))
    return out


NOTE_LISTS = {"footnotes", "endnotes", "notes"}      # lists of notes that the reader already shows beside their sentences


def in_contents(s: dict) -> bool:
    """What the contents list offers: the text itself, the translator's commentary on it, and the introductions.
    Left out: the Gutenberg header and licence, title pages, tables of contents, indexes, lists of footnotes and stubs."""
    if s["category"] == "body":
        return True
    if len(s["text"]) < 200:
        return False
    if s["category"] == "commentary":
        return s["path"][-1].lower() not in NOTE_LISTS and s["path"][0].lower() not in NOTE_LISTS
    return s["category"] == "front_matter" and s["path"][0] not in ("Contents", "Title page")


@app.get("/api/books/{slug}/toc")
def table_of_contents(slug: str, category: str | None = None):
    """Nested contents built from section paths, e.g. Book 1 > Fable 2 > Explanation. Nodes that have text carry an
    id and their category. `category` limits the list to one category such as 'body'."""
    root: dict = {"name": None, "children": []}
    for s in load(slug)["sections"]:
        if s["category"] != category if category else not in_contents(s):
            continue
        node = root
        for depth, name in enumerate(s["path"]):
            child = next((c for c in node["children"] if c["name"] == name), None)
            if child is None or (depth == len(s["path"]) - 1 and "id" in child):     # two sections of one name
                child = {"name": name, "children": []}
                node["children"].append(child)
            node = child
        node["id"], node["category"] = s["id"], s["category"]
    return root["children"]


_argument_cache: dict[str, tuple[int, set]] = {}


def arguments(slug: str) -> set[str]:
    """The sections that open with the translator's "argument": a short paragraph saying what the book or chapter
    holds, before the text itself (Butler's Iliad and Odyssey). Nothing in the file marks it, so it is recognised by
    the pattern: at least four fifths of the sections open with a short paragraph, without speech, that the rest
    of the section dwarfs."""
    book = load(slug)
    if slug not in _argument_cache or _argument_cache[slug][0] != id(book):
        found = set()
        body = [s for s in book["sections"] if s["category"] == "body"]
        for s in body:
            blocks = [" ".join(b.split()) for b in re.split(r"\n\s*\n", s["text"]) if b.strip()]
            if (len(blocks) > 1 and len(blocks[0]) <= 700 and not re.search(r'["“”]', blocks[0])
                    and not re.match(r"\(ll?\.|Fragment|\[", blocks[0]) and sum(map(len, blocks[1:])) > 10 * len(blocks[0])):
                found.add(s["id"])
        _argument_cache[slug] = (id(book), found if len(body) >= 5 and len(found) >= 0.8 * len(body) else set())
    return _argument_cache[slug][1]


@app.get("/api/books/{slug}/sections/{section_id}")
def get_section(slug: str, section_id: str, scope: str = "category"):
    """One section with its footnotes, plus previous/next ids.

    scope=category (default) moves within the same category, so reading 'body' skips the commentary;
    scope=all moves through every section in file order.
    """
    sections = load(slug)["sections"]
    for i, s in enumerate(sections):
        if s["id"] == section_id:
            break
    else:
        raise HTTPException(404, f"No section '{section_id}' in '{slug}'")
    pool = sections if scope == "all" else [t for t in sections if t["category"] == s["category"]]
    j = next(k for k, t in enumerate(pool) if t["id"] == section_id)
    ref = lambda t: {"id": t["id"], "title": t["title"]}
    return {**s, "argument": s["id"] in arguments(slug), "prev": ref(pool[j - 1]) if j > 0 else None,
            "next": ref(pool[j + 1]) if j + 1 < len(pool) else None}


@app.get("/api/books/{slug}/sections/{section_id}/annotations")
def get_annotations(slug: str, section_id: str, spoilers: bool = True):
    """The section as numbered sentences plus Claude's annotations for it (null if not annotated yet).

    The reader always shows everything. Notes the model flagged as spoilers are left out only with spoilers=false; `hidden` says how
    many were left out. Language and context notes carry a sentence number matching `sentences[i].i`.
    """
    section = next((s for s in load(slug)["sections"] if s["id"] == section_id), None)
    if section is None:
        raise HTTPException(404, f"No section '{section_id}' in '{slug}'")
    sentences = [asdict(s) for s in split_sentences(section["text"])]
    labels = {f["label"] for f in section["footnotes"]}
    for sentence in sentences:          # a marker counts only if the section has that note
        sentence["footnotes"] = [label for label in sentence["footnotes"] if label in labels]
    file = DATA / slug / "output" / "annotations" / f"{section_id}.json"
    if not file.exists():
        return {"sentences": sentences, "annotated": False, "annotations": None, "hidden": 0}
    result = json.loads(file.read_text(encoding="utf-8"))["result"]
    apply_namesakes(DATA / slug / "output", {section_id: result})     # "Ptolemy" here is "Ptolemy, son of Lagus"
    for key in ("literary_notes", "analysis", "notable_quotes", "scenes", "legacy"):
        result.setdefault(key, [])         # files made by an older prompt do not have these
    result.setdefault("summary", "")
    hidden = 0
    if not spoilers:
        for key in ("language_notes", "context_notes", "literary_notes", "analysis", "notable_quotes", "fun_facts"):
            kept = [n for n in result[key] if not n["spoiler"]]
            hidden += len(result[key]) - len(kept)
            result[key] = kept
    return {"sentences": sentences, "annotated": True, "annotations": result, "hidden": hidden}


@app.get("/api/books/{slug}/journey")
def get_journey(slug: str):
    """Every scene of the book in reading order with its place, for the map. `lat` and `lon` are null for places
    with no location. Sections annotated before scenes existed contribute nothing."""
    stops = []
    for s in load(slug)["sections"]:
        file = DATA / slug / "output" / "annotations" / f"{s['id']}.json"
        if s["category"] != "body" or not file.exists():
            continue
        scenes = json.loads(file.read_text(encoding="utf-8"))["result"].get("scenes", [])
        for n, scene in enumerate(scenes):
            stops.append({"section": s["id"], "section_title": s["title"], "scene": n, "title": scene["title"],
                          "told_as_story": scene["told_as_story"], **scene["place"]})
    return stops


_scene_cache: dict[str, tuple[float, list]] = {}


def scene_index(slug: str) -> list[dict]:
    """Every scene of the book in reading order with the names of those present, re-read when annotations change."""
    folder = DATA / slug / "output" / "annotations"
    mtime = folder.stat().st_mtime if folder.exists() else 0.0
    if slug not in _scene_cache or _scene_cache[slug][0] != mtime:
        found = []
        for s in load(slug)["sections"]:
            file = folder / f"{s['id']}.json"
            if s["category"] != "body" or not file.exists():
                continue
            for n, scene in enumerate(json.loads(file.read_text(encoding="utf-8"))["result"].get("scenes", [])):
                found.append({"section": s["id"], "scene": n, "title": scene["title"],
                              "present": {norm(name) for name in scene["present"]}})
        _scene_cache[slug] = (mtime, found)
    return _scene_cache[slug][1]


def scenes_with(slug: str, person: dict) -> list[dict]:
    """The scenes of the book in which a person of its tree is present."""
    names = {norm(n) for n in [person["name"], *person["aliases"]]}
    return [sc for sc in scene_index(slug) if sc["present"] & names]


@app.get("/api/books/{slug}/people/{person_id}/references")
def get_references(slug: str, person_id: str):
    """A character's key moments (python -m annotate.moments): `here` in this book, and `elsewhere` in the other
    books where the library index (python -m annotate.library) knows the same being. Both are empty lists when
    those steps have not been run. `scenes` lists every scene of this book the character is present in. A key
    moment carries `scene`, the first scene of its section with the character in it (null if there is none), so
    that a link can open at that scene."""
    person = next((p for p in load_tree(slug)["people"] if p["id"] == person_id), None)
    if person is None:
        raise HTTPException(404, f"No person '{person_id}' in '{slug}'")
    titles = {s["id"]: " \u203a ".join(s["path"]) for s in load(slug)["sections"]}
    present = scenes_with(slug, person)

    def first_scene(found: list[dict], section: str) -> int | None:
        return next((sc["scene"] for sc in found if sc["section"] == section), None)

    here = []
    moments = DATA / slug / "output" / "moments.json"
    if moments.exists():
        names = {norm(n) for n in [person["name"], *person["aliases"]]}
        for name, listed in json.loads(moments.read_text(encoding="utf-8"))["people"].items():
            if norm(name) in names:
                here += [{"section": m["section"], "title": titles.get(m["section"], ""), "what": m["what"],
                          "scene": first_scene(present, m["section"])} for m in listed]
    elsewhere = []
    library = DATA / "library.json"
    if library.exists():
        for being in json.loads(library.read_text(encoding="utf-8"))["beings"]:
            if any(m["book"] == slug and m["id"] == person_id for m in being["members"]):
                there = {}          # the scenes of each other book with that book's person in them
                for member in being["members"]:
                    other = next((p for p in load_tree(member["book"])["people"] if p["id"] == member["id"]), None)
                    there[member["book"]] = scenes_with(member["book"], other) if other and member["book"] != slug else []
                elsewhere = [{**m, "book_title": short_title(m["book"]), "scene": first_scene(there.get(m["book"], []), m["section"])}
                             for m in being["moments"] if m["book"] != slug]
                break
    return {"here": here, "elsewhere": elsewhere,
            "scenes": [{"section": sc["section"], "scene": sc["scene"], "title": titles.get(sc["section"], ""),
                        "scene_title": sc["title"]} for sc in present]}


@app.get("/api/books/{slug}/books")
def get_book_starts(slug: str):
    """Where each numbered book of the work starts, for links from citations ("see book xiii"): {"13": section id},
    the first story section whose path begins "Book 13". Empty for a work not divided into books."""
    starts = {}
    for s in load(slug)["sections"]:
        m = re.match(r"^Book\s+(\d+)$", s["path"][0]) if s["category"] == "body" and s["path"] else None
        if m:
            starts.setdefault(m.group(1), s["id"])
    return starts


@app.get("/api/books/{slug}/places")
def get_places(slug: str):
    """The book's places (python -m annotate.places), each with the scenes set there and the sections that name it,
    with their titles for the links, and `aliases`, the other names worth showing (see place_aliases). An empty
    list when the step has not been run."""
    titles = {s["id"]: " › ".join(s["path"]) for s in load(slug)["sections"]}
    file = DATA / slug / "output" / "places.json"
    if not file.exists():
        return []
    scene_titles = {(sc["section"], sc["scene"]): sc["title"] for sc in scene_index(slug)}
    cast = cast_names(slug)
    return [{**p, "aliases": place_aliases(p, cast), "scenes": [{**sc, "title": titles.get(sc["section"], ""),
                              "scene_title": scene_titles.get((sc["section"], sc["scene"]), "")} for sc in p["scenes"]],
             "named": [{**n, "title": titles.get(n["section"], "")} for n in p["named"]]}
            for p in json.loads(file.read_text(encoding="utf-8"))["places"]]


@app.get("/api/books/{slug}/items")
def get_items(slug: str):
    """The book's notable things (python -m annotate.items), each with the scenes that tell of it and the sections
    that name it, with their titles for the links. An empty list when the step has not been run."""
    file = DATA / slug / "output" / "items.json"
    if not file.exists():
        return []
    titles = {s["id"]: " › ".join(s["path"]) for s in load(slug)["sections"]}
    scene_titles = {(sc["section"], sc["scene"]): sc["title"] for sc in scene_index(slug)}
    return [{**t, "scenes": [{**sc, "title": titles.get(sc["section"], ""),
                              "scene_title": scene_titles.get((sc["section"], sc["scene"]), "")} for sc in t["scenes"]],
             "named": [{**n, "title": titles.get(n["section"], "")} for n in t["named"]]}
            for t in json.loads(file.read_text(encoding="utf-8"))["items"]]


@app.get("/api/books/{slug}/about")
def get_about(slug: str):
    """Sourced background for the book (python -m annotate.about): Wikipedia's summaries, themes and history for
    each work, which work each section belongs to, and the Wikipedia page of each matched character."""
    load(slug)
    file = DATA / slug / "output" / "about.json"
    pictures = DATA / slug / "output" / "images.json"       # python -m annotate.images: {wikipedia url: picture}
    found = json.loads(pictures.read_text(encoding="utf-8"))["pictures"] if pictures.exists() else {}
    if not file.exists():
        return {"works": {}, "sections": {}, "characters": {}, "pictures": found}
    return {**json.loads(file.read_text(encoding="utf-8")), "pictures": found}


@app.get("/api/books/{slug}/tree")
def get_tree(slug: str, upto: str | None = None, spoilers: bool = True, outside: bool = True,
             places: bool = False, relations: str = "parent_of,spouse_of"):
    """The family tree as far as the reader has got.

    upto       the section being read; only people and links first shown at or before it are returned
    spoilers   true (the default) returns the whole book's tree; false limits it to what was read up to `upto`
    outside    true adds links from beyond the book (drawn dashed); false returns only what the book says
    places     true keeps places; relations lists which kinds of link to return
    `current` lists who appears in the section being read; `hidden` counts what spoilers would reveal.
    `others` lists the people with no family link: they are not drawn in the tree but still have a profile.
    """
    tree = load_tree(slug)
    order = {sid: i for i, sid in enumerate(tree["sections"])}
    if upto is not None and upto not in order:
        raise HTTPException(404, f"No section '{upto}' in '{slug}'")
    wanted = {r for r in relations.split(",") if r}

    def build(pos: int) -> tuple[dict, list, list]:
        people = {p["id"]: p for p in tree["people"]
                  if p["first_index"] is not None and p["first_index"] <= pos
                  and (places or p["kind"] != "place") and (p["kind"] != "form" or "transformed_into" in wanted)}
        links = [e for e in tree["relationships"]
                 if e["relation"] in wanted and (outside or e["source"] == "text")
                 and e["first_index"] is not None and e["first_index"] <= pos
                 and e["a"] in people and e["b"] in people]
        linked = {end for e in links for end in (e["a"], e["b"])}
        return {i: p for i, p in people.items() if i in linked}, links, [p for i, p in people.items() if i not in linked]

    pos = len(order) if spoilers else (order[upto] if upto else -1)
    people, links, others = build(pos)
    hidden = {"people": 0, "relationships": 0}
    if not spoilers:
        all_people, all_links, _ = build(len(order))
        hidden = {"people": len(all_people) - len(people), "relationships": len(all_links) - len(links)}

    def describe(p: dict) -> str:
        seen = [d["text"] for d in p["descriptions"] if order[d["section"]] <= pos]
        return seen[-1] if seen else p["about"]

    here = [p["id"] for p in tree["people"] if any(d["section"] == upto for d in p["descriptions"])] if upto else []
    said = pronunciations()

    def person(p: dict) -> dict:
        return {"id": p["id"], "name": p["name"], "say": said.get(norm(p["name"]), {}).get("say", ""),
                "kind": p["kind"], "in_book": p["in_book"],
                "about": p["about"], "description": describe(p), "aliases": p["aliases"], "epithets": p["epithets"],
                "first_section": p["first_section"], "wikidata": p.get("wikidata", [])}

    return {
        "position": upto, "spoilers": spoilers, "hidden": hidden, "current": here,
        "people": [person(p) for p in people.values()], "others": [person(p) for p in others],
        "relationships": [{"a": e["a"], "relation": e["relation"], "b": e["b"], "source": e["source"], "note": e["note"],
                           "first_section": e["first_section"],
                           "evidence": [v for v in e["evidence"] if order[v["section"]] <= pos]} for e in links],
    }
