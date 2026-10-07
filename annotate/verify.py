"""Check the people and family links of a book against Wikidata (free, public-domain data).

    python -m annotate.verify <slug>          look every name up on Wikidata (free) and show what matching would cost
    python -m annotate.verify <slug> --yes    let Claude pick the right entry for each name, then save
                                              data/<slug>/output/wikidata.json and print what it changes
    options: --tradition "Greek and Roman mythology"   a hint for ambiguous names

Four steps:
  1. Search: each character name is looked up on Wikidata. A name usually has several candidates (Tethys is a
     Titaness, a moon of Saturn and a genus of molluscs).
  2. Match: Claude (Haiku) picks the candidate that the book means, from the character's description.
  3. Fetch: the parents, spouses, children and siblings of every candidate are read from Wikidata.
  4. Settle namesakes: where the book itself says "A is the parent of B", the pair of candidates that Wikidata
     also links that way is the right one (the Icarius who is Penelope's father, not the Athenian one).

`python -m annotate.merge <slug>` then uses the saved file; see its description for what it changes in the
tree. Lookups and matches are cached, so a second run asks only for what is new.
"""
import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter

from annotate.merge import apply_namesakes, is_proper_name, judge, merge, norm
from annotate.run import ROOT, get_client

MODEL = "claude-haiku-4-5"       # choosing one of a few candidates is simple work
SPARQL = "https://query.wikidata.org/sparql"
USER_AGENT = "PantheonPal/0.1 (personal reading companion; Python urllib)"
PAUSE = 1.0                      # seconds between Wikidata requests
SKIP_KINDS = {"place"}
LOOKUP_STEP = 40                 # names per lookup query
FETCH_STEP = 150                 # entries per family query
PER_REQUEST = 20                 # names per matching request
TOP, EXTRA = 6, 4                # candidates per name: the best known, plus lesser-known ones that look like characters
CHARACTER_WORDS = ("myth", "character", "fiction", "legend", "god", "deity", "nymph", "hero", "king", "queen",
                   "son of", "daughter of", "wife of", "titan", "giant", "monster", "biblical", "saint")
PROPS = {"P22": "father", "P25": "mother", "P26": "spouse", "P451": "partner", "P40": "child", "P3373": "sibling",
         "P460": "same", "P2925": "domain"}       # same = "said to be the same as"; domain = what a god is the god of
SEX = {"Q6581097": "male", "Q6581072": "female", "Q44148": "male", "Q43445": "female"}

SYSTEM_PROMPT = """You match names from a book to Wikidata entries for a reading companion. You are given the
book's title and a numbered list of names; each has what the book says about it and some candidate entries. For
each name choose the candidate that is what the book means by that name. Rules:

- Choose by meaning, not by spelling: the character's description decides. A god, hero, monster or other
  character is a mythological or literary figure, not the asteroid, moon, ship, genus, town, company or work of
  art named after them.
- When the book treats a natural thing as a being (the Sun as a god who sees and acts, Earth as a mother), choose
  the god of that thing if one is offered; if none is offered, answer with an empty id. When the book means only
  the thing itself (the sun in the sky), choose the entry for the thing.
- When both a Greek figure and its Roman counterpart are candidates, choose the one whose name the book uses.
- If two candidates are the same kind of figure with the same name (two heroes called Ajax), use the description
  to decide, and say "low" confidence if it does not settle it.
- If no candidate fits, answer with an empty id. A wrong match is worse than none.
- `confidence` is "high" when the description clearly fits the entry, "low" when you are guessing.
Return one answer for every name; `n` is the number in front of the name.""".strip()

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["matches"], "properties": {"matches": {
    "type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["n", "id", "confidence"],
                               "properties": {"n": {"type": "integer"}, "id": {"type": "string"},
                                              "confidence": {"type": "string", "enum": ["high", "low"]}}}}}}


def out_dir(slug: str):
    return ROOT / "data" / slug / "output"


def ask_wikidata(cache: dict, key: str, query: str) -> list[dict] | None:
    """Run one SPARQL query (remembered in `cache`). Returns its rows, or None if it failed or timed out."""
    if key in cache:
        return cache[key]
    request = urllib.request.Request(
        SPARQL, data=urllib.parse.urlencode({"query": query, "format": "json"}).encode("utf-8"),
        headers={"User-Agent": USER_AGENT, "Accept": "application/sparql-results+json"})
    try:
        rows = json.loads(urllib.request.urlopen(request, timeout=90).read().decode("utf-8"))["results"]["bindings"]
    except OSError as e:
        print(f"  a Wikidata query failed ({type(e).__name__}); trying smaller pieces", flush=True)
        time.sleep(5)
        return None
    cache[key] = [{k: v["value"].rsplit("/", 1)[-1] if v["type"] == "uri" else v["value"] for k, v in r.items()} for r in rows]
    time.sleep(PAUSE)            # be polite to a free service
    return cache[key]


def halves(cache: dict, prefix: str, items: list[str], build) -> list[dict]:
    """Ask about `items` in one query; if that is too much for the service, ask about each half."""
    rows = ask_wikidata(cache, prefix + "|".join(items), build(items))
    if rows is not None or len(items) == 1:
        return rows or []
    half = len(items) // 2
    return halves(cache, prefix, items[:half], build) + halves(cache, prefix, items[half:], build)


def labelled(labels: list[str]) -> str:
    """Query: every entry whose English label or alias is exactly one of `labels`."""
    return """SELECT ?name ?item ?itemLabel ?itemDescription ?links WHERE {
  VALUES ?name { %s }
  ?item rdfs:label|skos:altLabel ?name .
  ?item wikibase:sitelinks ?links .
  FILTER(?links > 0)
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}""" % " ".join(json.dumps(label, ensure_ascii=False) + "@en" for label in labels)


def gods_of(labels: list[str]) -> str:
    """Query: every god of a thing whose English label is one of `labels` (Helios for "Sun")."""
    return """SELECT ?name ?item ?itemLabel ?itemDescription ?links WHERE {
  VALUES ?name { %s }
  ?thing rdfs:label ?name .
  ?item wdt:P2925 ?thing .
  ?item wikibase:sitelinks ?links .
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}""" % " ".join(json.dumps(label, ensure_ascii=False) + "@en" for label in labels)


def book_names(slug: str) -> tuple[str, dict[str, dict], list[tuple[str, str, str]]]:
    """Every distinct character name in the book: key -> {name, kind, descriptions, aliases}; and the family links
    the book itself states, as (name key, relation, name key)."""
    book = json.loads((out_dir(slug) / "sections.json").read_text(encoding="utf-8"))
    order = {s["id"]: i for i, s in enumerate(book["sections"])}
    names: dict[str, dict] = {}
    stated = []
    files = sorted((out_dir(slug) / "annotations").glob(f"{slug}-body-*.json"), key=lambda f: order.get(f.stem, 0))
    results = {f.stem: json.loads(f.read_text(encoding="utf-8"))["result"] for f in files}
    apply_namesakes(out_dir(slug), results)          # each of several people who share a name under their own name
    for result in results.values():
        for c in result["characters"]:
            key = norm(c["name"])
            if not key or c["kind"] in SKIP_KINDS:
                continue
            n = names.setdefault(key, {"spellings": Counter(), "kinds": Counter(), "descriptions": [], "aliases": set()})
            n["spellings"][c["name"].strip()] += 1
            n["kinds"][c["kind"]] += 1
            if c["description"] and c["description"] not in n["descriptions"]:
                n["descriptions"].append(c["description"])
            n["aliases"].update(a for a in c["aliases"] if is_proper_name(a) and norm(a) != key)
        stated += [(norm(r["a"]), r["relation"], norm(r["b"])) for r in result["relationships"]
                   if r["source"] == "text" and r["relation"] in ("parent_of", "spouse_of")]
    # People the outside-the-book step added (ancestors, or a second spelling of someone in the book).
    outside_file = ROOT / "data" / slug / "curated" / "outside.json"
    if outside_file.exists():
        for p in json.loads(outside_file.read_text(encoding="utf-8"))["people"]:
            key = norm(p["name"])
            if key and key not in names:
                names[key] = {"spellings": Counter({p["name"].strip(): 1}), "kinds": Counter({"outside the book": 1}),
                              "descriptions": [p["description"]] if p["description"] else [],
                              "aliases": {p["other_name"]} if p["other_name"] else set()}
    for n in names.values():
        n["name"] = n.pop("spellings").most_common(1)[0][0]
        n["kind"] = n.pop("kinds").most_common(1)[0][0]
        n["descriptions"] = n["descriptions"][:3]
        n["aliases"] = sorted(n["aliases"])[:4]
    return book.get("title") or slug, names, sorted(set(stated))


def find_candidates(names: dict, cache: dict) -> None:
    """Set n["candidates"] for every name: the best-known entries with that label, plus gods of a thing with that
    name and lesser-known entries that look like characters (the suitor Antinous is far less famous than Hadrian's)."""
    people = list(names.values())
    by_label: dict[str, list[dict]] = {}
    for start in range(0, len(people), LOOKUP_STEP):
        part = [n["name"] for n in people[start:start + LOOKUP_STEP]]
        for prefix, build, god in (("labels:", labelled, False), ("gods:", gods_of, True)):
            for row in halves(cache, prefix, part, build):
                by_label.setdefault(row["name"], []).append({**row, "god": god})
        print(f"  looked up {min(start + LOOKUP_STEP, len(people))} of {len(people)} names", flush=True)
    for n in people:
        rows: dict[str, dict] = {}
        for r in by_label.get(n["name"], []):
            if "disambiguation page" in r.get("itemDescription", ""):
                continue
            god = r["god"] or rows.get(r["item"], {}).get("god", False)
            rows[r["item"]] = {"id": r["item"], "label": r.get("itemLabel", r["item"]), "links": int(r["links"]), "god": god,
                               "description": r.get("itemDescription", "") + (f" (the god of: {n['name']})" if god else "")}
        ranked = sorted(rows.values(), key=lambda r: (-r["links"], r["id"]))
        likely = [r for r in ranked[TOP:] if r["god"] or any(word in r["description"].lower() for word in CHARACTER_WORDS)]
        n["candidates"] = [{"id": r["id"], "label": r["label"], "description": r["description"]}
                           for r in ranked[:TOP] + likely[:EXTRA]]


def match_text(title: str, tradition: str, batch: list[dict]) -> str:
    lines = [f"Book: {title}" + (f"\nTradition: {tradition}" if tradition else ""), ""]
    for number, n in enumerate(batch, 1):
        lines.append(f"{number}. {n['name']} ({n['kind']}" + (f"; also called {', '.join(n['aliases'])}" if n["aliases"] else "") + ")")
        lines.append("  the book says: " + " / ".join(n["descriptions"]))
        lines += [f"  {c['id']}: {c['label']} - {c['description'] or 'no description'}" for c in n["candidates"]]
        lines.append("")
    return "\n".join(lines)


def match(client, title: str, tradition: str, names: dict, cache: dict) -> tuple[int, int]:
    """Ask Claude which candidate each name means. Sets n["qid"] and n["confidence"]. Returns tokens (in, out)."""
    todo = [n for n in names.values() if n["candidates"]]
    tokens_in = tokens_out = 0
    for start in range(0, len(todo), PER_REQUEST):
        batch = todo[start:start + PER_REQUEST]
        text = match_text(title, tradition, batch)
        key = "match:" + MODEL + ":" + text
        if key not in cache:
            r = client.messages.create(model=MODEL, max_tokens=4000, system=SYSTEM_PROMPT,
                                       messages=[{"role": "user", "content": text}],
                                       output_config={"format": {"type": "json_schema", "schema": SCHEMA}})
            tokens_in, tokens_out = tokens_in + r.usage.input_tokens, tokens_out + r.usage.output_tokens
            if r.stop_reason != "end_turn":
                print(f"  names {start + 1}-{start + len(batch)}: no answer ({r.stop_reason}); left unmatched")
                continue
            cache[key] = json.loads(next(b.text for b in r.content if b.type == "text"))["matches"]
        answers = {a["n"]: a for a in cache[key]}
        for number, n in enumerate(batch, 1):
            a = answers.get(number)
            if a and a["id"] in {c["id"] for c in n["candidates"]}:      # never accept an id that was not offered
                n["qid"], n["confidence"] = a["id"], a["confidence"]
        print(f"  matched names {start + 1}-{start + len(batch)} of {len(todo)}", flush=True)
    return tokens_in, tokens_out


def fetch_entities(qids: set[str], cache: dict) -> dict[str, dict]:
    """Family facts for each entry: {qid: {label, description, sex, father: [qid], mother, spouse, partner, child,
    sibling, same, domain}}."""
    ids = sorted(qids)
    entities = {q: {"label": q, "description": "", "sex": "", **{field: [] for field in PROPS.values()}} for q in ids}
    props = " ".join("wdt:" + p for p in [*PROPS, "P21"])

    def facts(items):
        return "SELECT ?item ?p ?v WHERE { VALUES ?item { %s } VALUES ?p { %s } ?item ?p ?v . }" % (
            " ".join("wd:" + q for q in items), props)

    def labels(items):
        return ("SELECT ?item ?itemLabel ?itemDescription WHERE { VALUES ?item { %s } "
                'SERVICE wikibase:label { bd:serviceParam wikibase:language "en". } }' % " ".join("wd:" + q for q in items))

    for start in range(0, len(ids), FETCH_STEP):
        part = ids[start:start + FETCH_STEP]
        for row in halves(cache, "facts:", part, facts):
            e, prop, value = entities[row["item"]], row["p"], row["v"]
            if prop == "P21":
                e["sex"] = e["sex"] or SEX.get(value, "")
            elif value.startswith("Q") and value not in e[PROPS[prop]]:
                e[PROPS[prop]].append(value)
        for row in halves(cache, "names:", part, labels):
            entities[row["item"]]["label"] = row.get("itemLabel", row["item"])
            entities[row["item"]]["description"] = row.get("itemDescription", "")
        if len(ids) > FETCH_STEP:
            print(f"  fetched {min(start + FETCH_STEP, len(ids))} of {len(ids)} entries", flush=True)
    for e in entities.values():
        for field in PROPS.values():
            e[field].sort()
    return entities


def settle(names: dict, entities: dict, stated: list[tuple[str, str, str]]) -> list[str]:
    """Use the family links the book itself states to choose between namesakes. Returns what was changed."""
    def confirmed(rel, a, b):
        return bool(a and b) and judge(rel, a, b, entities) == "confirmed"

    links = [(a, rel, b) for a, rel, b in stated if names.get(a, {}).get("candidates") and names.get(b, {}).get("candidates")]
    settled = set()
    for a, rel, b in links:
        if confirmed(rel, names[a].get("qid"), names[b].get("qid")):
            settled.update((a, b))
    changed = []
    for a, rel, b in links:
        A, B = names[a], names[b]
        if confirmed(rel, A.get("qid"), B.get("qid")):
            continue
        options = {(ca["id"], cb["id"]) for ca in A["candidates"] for cb in B["candidates"]
                   if (a not in settled or ca["id"] == A.get("qid")) and (b not in settled or cb["id"] == B.get("qid"))
                   and confirmed(rel, ca["id"], cb["id"])}
        keeping = {o for o in options if o[0] == A.get("qid") or o[1] == B.get("qid")}
        options = keeping or options
        if len(options) == 1:
            qa, qb = next(iter(options))
            for n, q in ((A, qa), (B, qb)):
                if n.get("qid") != q:
                    was = f"{entities[n['qid']]['label']} ({entities[n['qid']]['description']})" if n.get("qid") else "no match"
                    changed.append(f"{n['name']}: now {entities[q]['label']} ({entities[q]['description']}); was {was}")
                n["qid"], n["confidence"] = q, "high"
            settled.update((a, b))
    return changed


def report(slug: str) -> None:
    """Print what the saved file changes in the tree, by running the merge with it (the tree file is not written)."""
    r = merge(slug, ROOT)["report"]
    w = r["wikidata"]
    print(f"\nWith this file the tree has {r['people']} people and {r['relationships']} links.")
    print(f"  {w['people_matched']} people matched; links checked: " + ", ".join(
        f"{w.get(k, 0)} {k}" for k in ("confirmed", "reversed", "grandparent", "differs", "unverified"))
          + f"; {w.get('added', 0)} added from Wikidata")
    for line in w["changes"][:40]:
        print("  " + line)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug")
    ap.add_argument("--yes", action="store_true", help="confirm that you want to spend money on the matching")
    ap.add_argument("--tradition", default="", help='a hint for ambiguous names, e.g. "Greek and Roman mythology"')
    args = ap.parse_args()

    cache_file = out_dir(args.slug) / "wikidata_cache.json"
    cache = json.loads(cache_file.read_text(encoding="utf-8")) if cache_file.exists() else {}
    title, names, stated = book_names(args.slug)
    print(f"{title}: {len(names)} names. Looking them up on Wikidata (free)...")
    try:
        find_candidates(names, cache)
        with_candidates = [n for n in names.values() if n["candidates"]]
        batches = [with_candidates[i:i + PER_REQUEST] for i in range(0, len(with_candidates), PER_REQUEST)]
        new = [b for b in batches if "match:" + MODEL + ":" + match_text(title, args.tradition, b) not in cache]
        print(f"{len(with_candidates)} names have candidates ({len(names) - len(with_candidates)} have none). "
              f"Matching needs {len(new)} requests to {MODEL} ({len(batches) - len(new)} already answered).")
        if new and not args.yes:
            client = get_client()
            tokens = sum(client.messages.count_tokens(model=MODEL, system=SYSTEM_PROMPT, messages=[
                {"role": "user", "content": match_text(title, args.tradition, b)}]).input_tokens for b in new)
            print(f"About {tokens:,} input tokens and {sum(len(b) for b in new) * 25:,} output tokens. Nothing spent.")
            return print(f"To match and save: python -m annotate.verify {args.slug} --yes")

        tokens_in, tokens_out = match(get_client() if new else None, title, args.tradition, names, cache)
        candidates = {c["id"] for n in names.values() for c in n["candidates"]}
        print(f"Reading the family of {len(candidates)} candidate entries from Wikidata (free)...")
        entities = fetch_entities(candidates, cache)
        changed = settle(names, entities, stated)
        chosen = {n["qid"] for n in names.values() if n.get("qid")}
        parents = {q for c in chosen for f in ("father", "mother") for q in entities[c][f]}
        entities.update(fetch_entities(parents - set(entities), cache))      # for their names, and their own parents
    finally:
        cache_file.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    saved = {"work": title, "model": MODEL, "tradition": args.tradition,
             "names": {key: {"name": n["name"], "qid": n.get("qid", ""), "confidence": n.get("confidence", "")}
                       for key, n in sorted(names.items())},
             "entities": {q: entities[q] for q in sorted(chosen | parents)}}
    (out_dir(args.slug) / "wikidata.json").write_text(json.dumps(saved, ensure_ascii=False, indent=1), encoding="utf-8")
    high = sum(1 for n in names.values() if n.get("confidence") == "high")
    print(f"Matched {sum(1 for n in names.values() if n.get('qid'))} of {len(names)} names to {len(chosen)} entries "
          f"({high} with high confidence); tokens {tokens_in:,} in / {tokens_out:,} out. Saved output/wikidata.json")
    if changed:
        print(f"Namesakes settled by the book's own family links: {len(changed)}")
        for line in changed[:20]:
            print("  " + line)
    report(args.slug)


if __name__ == "__main__":
    main()
