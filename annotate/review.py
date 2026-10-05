"""Let Claude read the whole family tree of a book once and correct it.

    python -m annotate.review <slug>          show what would be sent and what it costs; spends nothing
    python -m annotate.review <slug> --yes    ask Claude, save data/<slug>/output/review.json and print its decisions

Each section is annotated without sight of the others, so the merged tree can hold the same person twice
(Anticlea and Anticleia) and two beings under one name (the goddess Minerva and Mentor, whose shape she takes):
two beings under one name. Here Claude sees every person of the book at once and returns two lists: names that are
one being, and names that were wrongly joined. `python -m annotate.merge <slug>` then applies them. (It does not
judge the family links: in a trial it called correct links wrong as often as it caught wrong ones. That is what
the optional Wikidata check, annotate.verify, is for.) Hand corrections in curated/aliases.json still win.
"""
import argparse
import json
import sys

from annotate.merge import merge, norm
from annotate.run import ROOT, cost, get_client
from annotate.schema import MODEL

MAX_TOKENS = 16000
EFFORT = "medium"        # this is judgement, not extraction

SYSTEM_PROMPT = """You are checking the family tree that a reading companion built for one book. It was put together
from notes made section by section, so it has mistakes. You are given the book's title, its people (each with the
names the tree uses for them and a short description) and its family links, which are there to help you tell
people apart. Return JSON that matches the schema.

1. `same`: entries that are one and the same being and should be joined: a second spelling (Anticlea, Anticleia),
   a Roman and a Greek name for one god when the tree has both, a title or nickname listed as a separate person.
   Give the names of the entries to join. Two different people who share a name are NOT the same; neither are a
   god and a mortal whose shape the god takes, or a parent and child.
2. `different`: an entry that wrongly joins two beings. Give the entry's `name` and the `other` name listed with
   it that belongs to someone else: a real other person whose shape a god takes (Mentor is not Minerva), a thing
   such as the sun joined to a god who is not that thing, a father's name listed for the son. A false name or
   title that a character uses for himself is still that character (Noman is Ulysses), so leave those alone.
Be sure before you list anything: every item changes the tree. Judge by the book and the tradition it belongs to.
`reason` says in at most 15 words why, in plain words. `change` is true when the tree should change as the item
says, and false if on reflection it should be left as it is; only items with `change` true are applied.
Empty lists are fine.""".strip()

_STR = {"type": "string"}


def _obj(**props) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


SCHEMA = _obj(
    same={"type": "array", "items": _obj(names={"type": "array", "items": _STR}, reason=_STR, change={"type": "boolean"})},
    different={"type": "array", "items": _obj(name=_STR, other=_STR, reason=_STR, change={"type": "boolean"})},
)


def tree_text(title: str, tree: dict) -> str:
    name = {p["id"]: p["name"] for p in tree["people"]}
    lines = [f"Book: {title}", "", "PEOPLE"]
    for p in tree["people"]:
        about = p["about"] or (p["descriptions"][0]["text"] if p["descriptions"] else "")
        also = f" [also: {', '.join(p['aliases'])}]" if p["aliases"] else ""
        lines.append(f"- {p['name']}{also} ({p['kind']}{'' if p['in_book'] else ', not named in the book'}): {about}")
    lines += ["", "LINKS"]
    lines += [f"- {name[r['a']]} {r['relation']} {name[r['b']]} ({'book' if r['source'] == 'text' else 'outside the book'})"
              for r in tree["relationships"]]
    return "\n".join(lines)


def check(answer: dict, tree: dict) -> dict:
    """Keep only decisions about names and links that really are in the tree."""
    known = {norm(n) for p in tree["people"] for n in [p["name"], *p["aliases"]]}
    same = [{**s, "names": names} for s in answer["same"]
            if s["change"] and len(names := [n for n in dict.fromkeys(s["names"]) if norm(n) in known]) > 1]
    different = [d for d in answer["different"] if d["change"] and norm(d["name"]) in known and norm(d["other"]) in known
                 and norm(d["name"]) != norm(d["other"])]
    return {"same": same, "different": different}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug")
    ap.add_argument("--yes", action="store_true", help="confirm that you want to spend money")
    args = ap.parse_args()

    out = ROOT / "data" / args.slug / "output"
    title = json.loads((out / "sections.json").read_text(encoding="utf-8")).get("title") or args.slug
    tree = merge(args.slug, ROOT, review=False)          # the tree before any earlier review, so a rerun starts fresh
    text = tree_text(title, tree)
    client = get_client()
    tokens = client.messages.count_tokens(model=MODEL, system=SYSTEM_PROMPT,
                                          messages=[{"role": "user", "content": text}]).input_tokens
    print(f"{title}: {len(tree['people'])} people, {len(tree['relationships'])} links; {tokens:,} input tokens, "
          f"about ${cost(tokens, 1500, batch=False):.2f} with {MODEL}.")
    if not args.yes:
        return print(f"Nothing spent. To run it: python -m annotate.review {args.slug} --yes")
    r = client.messages.create(model=MODEL, max_tokens=MAX_TOKENS, system=SYSTEM_PROMPT,
                               messages=[{"role": "user", "content": text}],
                               output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": SCHEMA}})
    if r.stop_reason != "end_turn":
        sys.exit(f"No answer ({r.stop_reason}); nothing saved.")
    decisions = check(json.loads(next(b.text for b in r.content if b.type == "text")), tree)
    (out / "review.json").write_text(json.dumps(
        {"work": title, "model": MODEL, "usage": {"input": r.usage.input_tokens, "output": r.usage.output_tokens},
         **decisions}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Saved output/review.json; tokens {r.usage.input_tokens:,} in / {r.usage.output_tokens:,} out = about "
          f"${cost(r.usage.input_tokens, r.usage.output_tokens, batch=False):.2f}")
    for s in decisions["same"]:
        print(f"  same being: {' = '.join(s['names'])}  ({s['reason']})")
    for d in decisions["different"]:
        print(f"  not the same: {d['name']} and {d['other']}  ({d['reason']})")
    print(f"To apply them: python -m annotate.merge {args.slug}")


if __name__ == "__main__":
    main()
