"""Join the characters of every book into one index, so the reader can show a character's key moments in other books.

    python -m annotate.library        build data/library.json (free; no Claude, no internet)

Two characters in different books are the same being when the Wikidata check (annotate.verify) matched them to the
same entry, or to a Roman god and its Greek counterpart: Jove in the Odyssey, Jupiter in Ovid and Zeus in Hesiod.
Names alone are not trusted across books (the Helen of a detective story is not Helen of Troy), so a book that
has not had the Wikidata check takes no part. Each being lists its key moments (annotate.moments) book by book.
"""
import json
import sys

from annotate.merge import norm, same_being
from annotate.run import ROOT


def load(slug: str) -> dict | None:
    out = ROOT / "data" / slug / "output"
    files = {name: out / f"{name}.json" for name in ("tree", "moments", "wikidata", "sections")}
    if not all(f.exists() for f in files.values()):
        return None
    data = {name: json.loads(f.read_text(encoding="utf-8")) for name, f in files.items()}
    titles = {s["id"]: " › ".join(s["path"]) for s in data["sections"]["sections"]}
    return {"slug": slug, "title": data["sections"].get("title") or slug, "tree": data["tree"],
            "moments": data["moments"]["people"], "entities": data["wikidata"]["entities"], "titles": titles}


def build() -> dict:
    books = [b for d in sorted((ROOT / "data").iterdir()) if d.is_dir() and (b := load(d.name))]
    entities = {}
    for b in books:
        entities.update(b["entities"])
    people = [{"book": b["slug"], "id": p["id"], "name": p["name"], "qids": p["wikidata"], "weight": p["section_count"],
               "names": {norm(n) for n in [p["name"], *p["aliases"]]}}
              for b in books for p in b["tree"]["people"] if p.get("wikidata") and not p.get("from_library")]
    # Pairs of people from different books that Wikidata says are one being; the surest and most prominent first.
    pairs = []
    for i, x in enumerate(people):
        for j in range(i + 1, len(people)):
            y = people[j]
            if x["book"] != y["book"] and any(same_being(a, c, entities) for a in x["qids"] for c in y["qids"]):
                pairs.append((0 if set(x["qids"]) & set(y["qids"]) else 1, -(x["weight"] + y["weight"]), i, j))
    # A being has at most one person per book: Diana is "the same as" both Artemis and Selene, but those two are
    # different people in Hesiod, so only the first (more prominent) of them is joined to her.
    group = list(range(len(people)))
    members = {i: [i] for i in group}
    for _, _, i, j in sorted(pairs):
        a, b = group[i], group[j]
        if a == b or {people[k]["book"] for k in members[a]} & {people[k]["book"] for k in members[b]}:
            continue
        for k in members.pop(b):
            group[k] = a
            members[a].append(k)
    moments_of = {b["slug"]: b for b in books}
    beings = []
    for ids in members.values():
        if len(ids) < 2:
            continue
        ids.sort(key=lambda k: (people[k]["book"], people[k]["id"]))
        moments = []
        for k in ids:
            p, b = people[k], moments_of[people[k]["book"]]
            for name, listed in b["moments"].items():
                if norm(name) in p["names"]:
                    moments += [{"book": b["slug"], "book_title": b["title"], "name": p["name"], "section": m["section"],
                                 "title": b["titles"].get(m["section"], m["section"]), "what": m["what"]} for m in listed]
        first = people[ids[0]]["qids"][0]
        beings.append({"wikidata": first, "label": entities.get(first, {}).get("label", people[ids[0]]["name"]),
                       "members": [{"book": people[k]["book"], "id": people[k]["id"], "name": people[k]["name"]} for k in ids],
                       "moments": moments})
    beings.sort(key=lambda being: being["label"])
    return {"books": [b["slug"] for b in books], "beings": beings}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    library = build()
    (ROOT / "data" / "library.json").write_text(json.dumps(library, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Books that take part: {', '.join(library['books']) or 'none'} (a book needs tree.json, moments.json and wikidata.json).")
    print(f"{len(library['beings'])} characters appear in more than one book. Saved data/library.json")


if __name__ == "__main__":
    main()
