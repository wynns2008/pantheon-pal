"""Find the Wikipedia page of characters the Wikidata step could not match, with Claude choosing among the results.

    python -m annotate.wikimatch fetch          search Wikipedia for each unmatched character (free; saves the results)
    python -m annotate.wikimatch pick           show what choosing would cost; spends nothing
    python -m annotate.wikimatch pick --yes     ask Claude to choose, and save data/wiki_matches.json

A character is looked up when no Wikidata entry was matched and they are likely to have a page of their own: named
in three or more sections, or a god, nymph or monster with family. Spelling ("Œdipus", "Diomed") and the names a
namesake split made up ("Philip of Macedon, son of Amyntas") are why most were missed. A name alone is not enough
(the search for "Diomed" finds a Romanian poet, "Danaüs" a butterfly), so Claude is shown what the book says of the
character and who their relatives are, and picks the page that is that character, or none. Nothing in the trees
changes: the choices are for review, and approved ones become hand matches ("wikidata" in curated/aliases.json).
"""
import argparse
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

from annotate.run import ROOT, cost, get_client
from annotate.schema import MODEL

FOUND = ROOT / "data" / "wiki_search.json"
PICKED = ROOT / "data" / "wiki_matches.json"
UA = {"User-Agent": "LitAnalyzer/1.0 (reading companion for public-domain classics)"}
PER_REQUEST = 20
EFFORT = "low"
FAMILY = ("parent_of", "spouse_of", "sibling_of", "lover_of")

SYSTEM_PROMPT = """You match characters of Greek and Latin works (in old English translations) to their English
Wikipedia pages. For each numbered character you are given the book, the name as the translation spells it, what
the book says of them, their relatives in the book, and up to seven Wikipedia search results (title, short
description, Wikidata id). Choose the result that is this very character: the same person, god or creature, in
the same story or the same history. Answer "none" when no result is them: a namesake (Hadrian's Antinous is not
the suitor), a page listing several people of that name, a place, a work, or anything else. Return the chosen
result's Wikidata id, or "none", with a few words of reason. Return JSON matching the schema."""

SCHEMA = {"type": "object", "properties": {"picks": {"type": "array", "items": {"type": "object", "properties": {
    "n": {"type": "integer"}, "qid": {"type": "string"}, "reason": {"type": "string"}},
    "required": ["n", "qid", "reason"], "additionalProperties": False}}},
    "required": ["picks"], "additionalProperties": False}


def plain(name: str) -> str:
    s = name.replace("Œ", "Oe").replace("œ", "oe").replace("Æ", "Ae").replace("æ", "ae")
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    return re.split(r",| the | son of | daughter of | king ", s)[0].strip()


def get(url: str) -> dict:
    for attempt in range(8):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise
            time.sleep(int(e.headers.get("Retry-After") or 0) or 10 * (attempt + 1))
    raise RuntimeError("Wikipedia kept refusing")


def unmatched() -> list[dict]:
    rows = []
    for f in sorted((ROOT / "data").glob("*/output/tree.json")):
        slug = f.parent.parent.name
        t = json.loads(f.read_text(encoding="utf-8"))
        byid = {p["id"]: p for p in t["people"]}
        title = json.loads((f.parent / "sections.json").read_text(encoding="utf-8")).get("title") or slug
        for p in t["people"]:
            if p.get("from_library") or not p["section_count"] or p["wikidata"] or p["kind"] in ("place", "form", "other"):
                continue
            kin = [f"{r['relation'].replace('_of', '')} {byid[r['b'] if r['a'] == p['id'] else r['a']]['name']}"
                   if r["a"] == p["id"] else f"{byid[r['a']]['name']} {r['relation'].replace('_', ' ')} them"
                   for r in t["relationships"] if r["relation"] in FAMILY and p["id"] in (r["a"], r["b"])]
            if p["section_count"] >= 3 or (p["kind"] != "mortal" and kin):
                said = [d["text"] for d in p["descriptions"]][:3]
                rows.append({"book": slug, "book_title": title, "id": p["id"], "name": p["name"], "kind": p["kind"],
                             "about": p["about"], "said": said, "kin": kin[:8]})
    return rows


def fetch() -> None:
    rows = unmatched()
    found = json.loads(FOUND.read_text(encoding="utf-8")) if FOUND.exists() else {}
    for n, row in enumerate(rows, 1):
        q = plain(row["name"])
        if q in found:
            continue
        # The book's own context steers the search: "Diomed" alone finds a poet, with "Greek mythology" the hero.
        context = "Greek mythology" if row["kind"] != "mortal" or row["book"] not in ("pausanias",) else "ancient Greece"
        url = ("https://en.wikipedia.org/w/api.php?action=query&format=json&generator=search&gsrlimit=7"
               "&prop=pageprops|description&ppprop=wikibase_item&gsrsearch=" + urllib.parse.quote(f"{q} {context}"))
        pages = get(url).get("query", {}).get("pages", {})
        found[q] = [{"title": p["title"], "description": p.get("description", ""),
                     "qid": p.get("pageprops", {}).get("wikibase_item", ""), "rank": p.get("index", 99)}
                    for p in sorted(pages.values(), key=lambda p: p.get("index", 99))]
        time.sleep(0.5)
        if n % 25 == 0:
            FOUND.write_text(json.dumps(found, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"  {n}/{len(rows)}", flush=True)
    FOUND.write_text(json.dumps(found, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Searched {len(rows)} characters; saved {FOUND.relative_to(ROOT)}")


def describe(batch: list[dict], found: dict) -> str:
    out = []
    for n, row in enumerate(batch, 1):
        lines = [f"{n}. {row['name']} — {row['book_title']} ({row['kind']})"]
        if row["about"]:
            lines.append(f"   known as: {row['about']}")
        if row["said"]:
            lines.append("   the book says: " + " / ".join(row["said"]))
        if row["kin"]:
            lines.append("   relatives: " + "; ".join(row["kin"]))
        for c in found.get(plain(row["name"]), []):
            if c["qid"]:
                lines.append(f"   - {c['qid']}: {c['title']} — {c['description']}")
        out.append("\n".join(lines))
    return "\n\n".join(out)


def ask(client, text: str) -> tuple[list[dict], tuple[int, int]]:
    r = client.messages.create(model=MODEL, max_tokens=8000, system=SYSTEM_PROMPT,
                               messages=[{"role": "user", "content": text}],
                               output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": SCHEMA}})
    usage = (r.usage.input_tokens, r.usage.output_tokens)
    if r.stop_reason != "end_turn":
        return [], usage
    return json.loads(next(b.text for b in r.content if b.type == "text"))["picks"], usage


def pick(yes: bool) -> None:
    found = json.loads(FOUND.read_text(encoding="utf-8"))
    picked = json.loads(PICKED.read_text(encoding="utf-8")) if PICKED.exists() else {}
    rows = [r for r in unmatched() if f"{r['book']}/{r['id']}" not in picked
            and any(c["qid"] for c in found.get(plain(r["name"]), []))]
    batches = [rows[i:i + PER_REQUEST] for i in range(0, len(rows), PER_REQUEST)]
    texts = [describe(b, found) for b in batches]
    client = get_client()
    if not yes:
        tokens = sum(client.messages.count_tokens(model=MODEL, system=SYSTEM_PROMPT,
                                                  messages=[{"role": "user", "content": t}]).input_tokens for t in texts)
        out = 40 * len(rows)
        print(f"{len(rows)} characters in {len(batches)} requests: {tokens:,} tokens in, about {out:,} out: "
              f"${cost(tokens, out, batch=False):.2f}. Add --yes to run.")
        return
    tokens_in = tokens_out = 0
    for batch, text in zip(batches, texts):
        picks, (i, o) = ask(client, text)
        tokens_in, tokens_out = tokens_in + i, tokens_out + o
        cand = lambda row: {c["qid"]: c for c in found.get(plain(row["name"]), [])}
        for p in picks:
            if not 1 <= p["n"] <= len(batch):
                continue
            row = batch[p["n"] - 1]
            chosen = cand(row).get(p["qid"])
            picked[f"{row['book']}/{row['id']}"] = {"book": row["book"], "name": row["name"],
                                                    "qid": p["qid"] if chosen else None,
                                                    "title": chosen["title"] if chosen else "", "reason": p["reason"]}
        PICKED.write_text(json.dumps(picked, ensure_ascii=False, indent=1), encoding="utf-8")
    matched = sum(1 for v in picked.values() if v["qid"])
    print(f"{matched} of {len(picked)} matched. Spent: {tokens_in:,} in, {tokens_out:,} out = "
          f"${cost(tokens_in, tokens_out, batch=False):.2f}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["fetch", "pick"])
    ap.add_argument("--yes", action="store_true", help="ask Claude (costs money) and save the choices")
    args = ap.parse_args()
    fetch() if args.command == "fetch" else pick(args.yes)


if __name__ == "__main__":
    main()
