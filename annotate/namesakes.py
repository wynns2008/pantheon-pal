"""Keep apart the different people of a book who share one name: the Ptolemies of Pausanias, Diomedes the Thracian
king and Diomedes the son of Tydeus, the choruses of seven plays.

    python -m annotate.namesakes <slug>          show what would be sent and what it costs; spends nothing
    python -m annotate.namesakes <slug> --yes    ask Claude, then save data/<slug>/output/namesakes.json

The family tree joins people by name, so every "Ptolemy" of the book becomes one person, with too many parents.
A person of the tree with three or more parents that the book states, or one who is both parent and child of
another (two kings of one name each), is looked at here: Claude is shown, section
by section, what the book says of that name there (the note's description and its family links in that section)
and puts the entries into groups, one for each different person, each with a name that tells it apart ("Ptolemy,
son of Lagus", "Ptolemy Philadelphus"). Heracles, son of Zeus and of Amphitryon's wife, stays one person.
`python -m annotate.merge <slug>` then gives each group its own entry in the tree.
"""
import argparse
import json
import sys
from collections import defaultdict

from annotate.merge import merge, norm
from annotate.run import ROOT, cost, get_client
from annotate.schema import MODEL

MAX_TOKENS = 16000
EFFORT = "medium"
MIN_PARENTS = 3

SYSTEM_PROMPT = """You are checking a family tree that a reading companion built for one book by joining people by
name. Some names belong to more than one person: several kings called Ptolemy, two different men called Diomedes,
the choruses of different plays. You are given the book's title and, for each name to check, every section that
mentions it, with what the note-taker wrote of that person in that section and the family links stated there.
Return JSON that matches the schema: for each name, `groups`, the different people behind it.

- Each group lists the entry numbers that are one person, and gives that person a `name` that tells them apart:
  the usual name in English if there is one ("Ptolemy Philadelphus", "Diomedes of Thrace"), else the name with what
  the book says of them ("Ptolemy, son of Lagus"). One group keeps the bare name only when the name is one person.
- If all entries are one person, return one group with all of them and the bare name: a hero with a divine and a
  mortal father (Heracles), or a figure whom different accounts give different parents, is still one person.
- Every entry number goes in exactly one group. When an entry is too short to tell, put it with the person it
  most likely is, by its section, its links and the other entries.
- `about` is at most 12 words saying who each person is.""".strip()

_STR = {"type": "string"}


def _obj(**props) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


SCHEMA = _obj(names={"type": "array", "items": _obj(
    name=_STR, groups={"type": "array", "items": _obj(
        name=_STR, about=_STR, entries={"type": "array", "items": {"type": "integer"}})})})


def candidates(slug: str) -> tuple[str, list[dict]]:
    """The book's title, and each person of the tree with MIN_PARENTS or more parents the book states, with every
    section entry of the names that person goes by: [{name, keys, entries: [{section, title, name, description, links}]}]."""
    out = ROOT / "data" / slug / "output"
    book = json.loads((out / "sections.json").read_text(encoding="utf-8"))
    titles = {s["id"]: " > ".join(s["path"]) for s in book["sections"]}
    order = {s["id"]: i for i, s in enumerate(book["sections"])}
    tree = merge(slug)
    parents = defaultdict(set)
    for r in tree["relationships"]:
        if r["relation"] == "parent_of" and r["source"] == "text":
            parents[r["b"]].add(r["a"])
    # Two people who are each other's parent are two pairs of namesakes (Demetrius and Antigonus, kings of each name).
    loops = {x for x, ps in parents.items() for y in ps if x in parents.get(y, ())}
    picked = [p for p in tree["people"] if len(parents[p["id"]]) >= MIN_PARENTS or p["id"] in loops]
    found = []
    for p in picked:
        keys = {norm(n) for n in [p["name"], *p["aliases"]]}
        entries = []
        for f in sorted((out / "annotations").glob(f"{slug}-body-*.json"), key=lambda f: order.get(f.stem, 0)):
            a = json.loads(f.read_text(encoding="utf-8"))["result"]
            for c in a["characters"]:
                if norm(c["name"]) in keys:
                    links = [f"{r['a']} {r['relation'].replace('_', ' ')} {r['b']}" for r in a["relationships"]
                             if c["name"] in (r["a"], r["b"]) and r["relation"] != "transformed_into"]
                    entries.append({"section": f.stem, "title": titles.get(f.stem, f.stem), "name": c["name"],
                                    "description": c["description"], "links": links})
        if len(entries) >= 2:
            found.append({"name": p["name"], "keys": sorted(keys), "entries": entries})
    return book.get("title") or slug, found


def request_text(title: str, found: list[dict]) -> str:
    lines = [f"Book: {title}"]
    for person in found:
        lines += ["", f"NAME: {person['name']}"]
        for n, e in enumerate(person["entries"], 1):
            links = f" | links: {'; '.join(e['links'])}" if e["links"] else ""
            lines.append(f"{n}. [{e['title']}] {e['name']}: {e['description']}{links}")
    return "\n".join(lines)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug")
    ap.add_argument("--yes", action="store_true", help="confirm that you want to spend money")
    args = ap.parse_args()
    out = ROOT / "data" / args.slug / "output"
    title, found = candidates(args.slug)
    if not found:
        return print(f"{title}: no one has {MIN_PARENTS} or more parents or is both parent and child of one person; nothing to check.")
    text = request_text(title, found)
    client = get_client()
    tokens = client.messages.count_tokens(model=MODEL, system=SYSTEM_PROMPT,
                                          messages=[{"role": "user", "content": text}]).input_tokens
    guess = sum(20 + 6 * len(p["entries"]) for p in found) + 40 * len(found)
    print(f"{title}: {len(found)} names to check ({', '.join(p['name'] for p in found[:12])}"
          f"{', ...' if len(found) > 12 else ''}); {tokens:,} input tokens, about ${cost(tokens, guess, batch=False):.2f} with {MODEL}.")
    if not args.yes:
        return print(f"Nothing spent. To run it: python -m annotate.namesakes {args.slug} --yes")
    with client.messages.stream(model=MODEL, max_tokens=MAX_TOKENS, system=SYSTEM_PROMPT, messages=[{"role": "user", "content": text}],
                                output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": SCHEMA}}) as stream:
        r = stream.get_final_message()
    if r.stop_reason != "end_turn":
        sys.exit(f"No answer ({r.stop_reason}); nothing saved.")
    answer = {norm(a["name"]): a["groups"] for a in json.loads(next(b.text for b in r.content if b.type == "text"))["names"]}
    split = []
    for person in found:
        groups = answer.get(norm(person["name"]), [])
        if len(groups) < 2:
            continue                    # one person after all
        entry = person["entries"]
        split.append({"name": person["name"], "groups": [
            {"name": g["name"], "about": g["about"],
             "entries": [[entry[n - 1]["section"], entry[n - 1]["name"]] for n in g["entries"] if 1 <= n <= len(entry)]}
            for g in groups]})
    # A second run looks only at the tree as the first one left it, so its splits are added to the saved ones.
    file = out / "namesakes.json"
    saved = json.loads(file.read_text(encoding="utf-8")) if file.exists() else {"split": [], "usage": {"input": 0, "output": 0}}
    file.write_text(json.dumps(
        {"work": title, "model": MODEL, "usage": {"input": saved["usage"]["input"] + r.usage.input_tokens,
                                                  "output": saved["usage"]["output"] + r.usage.output_tokens},
         "split": saved["split"] + split}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Saved output/namesakes.json: {len(split)} of {len(found)} names are more than one person; tokens "
          f"{r.usage.input_tokens:,} in / {r.usage.output_tokens:,} out = about "
          f"${cost(r.usage.input_tokens, r.usage.output_tokens, batch=False):.2f}")
    for s in split:
        print(f"  {s['name']}: " + "; ".join(f"{g['name']} ({len(g['entries'])})" for g in s["groups"]))


if __name__ == "__main__":
    main()
