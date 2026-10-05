"""Ask Claude once for the "outside the book" layer of the family tree: family links that are well established
beyond the text itself (mythology, history, the author's other works), for any book.

    python -m annotate.outside <slug>                 show who would be asked about, the token count and the cost
    python -m annotate.outside <slug> --yes           call Claude once and save data/<slug>/curated/outside.json
    python -m annotate.outside <slug> --yes --force   replace an existing outside.json
    options: --limit N  ask about the N most important people (default 100)
             --batch N  people per request (default 35); several small requests give fuller answers than one big one

It works for any book: for a self-contained novel the model is told to return nothing rather than invent
links. The result goes in `curated/` because it is meant to be read and edited by hand, and the tree draws it
dashed so it is never mistaken for what the text says. Needs data/<slug>/output/tree.json
(python -m annotate.merge <slug>).
"""
import argparse
import json
import sys
from pathlib import Path

from annotate.merge import norm
from annotate.run import ROOT, cost, get_client
from annotate.schema import MODEL

MAX_TOKENS = 40000     # thinking tokens count against this, and the answer itself is long
EFFORT = "low"         # medium used up the limit on thinking
SKIP_KINDS = {"place", "form", "creature", "other"}
RELATIONS = ["parent_of", "spouse_of"]

SYSTEM_PROMPT = """You are adding an "outside the book" layer to a family tree for a reading companion. You are
given the title of a work and the people named in it. Return JSON that matches the schema. Rules:

SOURCES
- Use only well-established knowledge from beyond the text itself: the wider tradition the work belongs to
  (for example mythology or legend), recorded history, or the author's other works.
- If the work is a self-contained story with no such tradition, return empty lists. That is correct and
  expected for such works. Never invent figures or links.
- Where the tradition is well documented, be thorough: give the full standard genealogy, not a sample. Leave
  out only links that are disputed or that you are not confident about.

PEOPLE
- Include every listed person who appears in the tradition's standard genealogies, minor figures too when
  their parents are known. Continue each line upward through every ancestor to the earliest ancestor of the
  tradition (for a mythology, the primordial beings such as Chaos, Earth and Heaven). Include all
  intermediate ancestors, so that every person can be reached by following parent_of links down from that
  earliest ancestor.
- Use the name exactly as written in the list whenever the person is on it. For a person you add, use the name
  the work or its translator would use. Put another well-known name (for example the Greek name for a Roman
  god) in other_name, otherwise an empty string.
- description: who they are or what they rule or are known for, in at most 12 words.
- Skip groups with no fixed parentage (for example "the nymphs").

RELATIONSHIPS
- Use only parent_of (A is a parent of B) and spouse_of. List both parents as two parent_of links. Do not list
  siblings: they follow from shared parents.
- Where traditions disagree, choose the most widely accepted version and put the other version in note, in at
  most 12 words. Otherwise note is an empty string.
- Every name in a relationship must also appear in people.""".strip()

_STR = {"type": "string"}


def _obj(**props) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


SCHEMA = _obj(
    people={"type": "array", "items": _obj(name=_STR, other_name=_STR, description=_STR)},
    relationships={"type": "array", "items": _obj(a=_STR, relation={"type": "string", "enum": RELATIONS}, b=_STR, note=_STR)},
)


def load_book(slug: str, limit: int, root: Path = ROOT) -> tuple[str, list[str]]:
    """The book's title and its most important people (by how often they appear and how connected they are)."""
    out = root / "data" / slug / "output"
    if not (out / "tree.json").exists():
        sys.exit(f"No tree yet. Run: python -m annotate.merge {slug}")
    tree = json.loads((out / "tree.json").read_text(encoding="utf-8"))
    title = json.loads((out / "sections.json").read_text(encoding="utf-8")).get("title") or slug
    degree: dict[str, int] = {}
    for e in tree["relationships"]:
        for end in (e["a"], e["b"]):
            degree[end] = degree.get(end, 0) + 1
    people = [p for p in tree["people"] if p["kind"] not in SKIP_KINDS
              and (p["section_count"] >= 2 or degree.get(p["id"], 0) >= 1)]
    people.sort(key=lambda p: -(p["section_count"] + 2 * degree.get(p["id"], 0)))
    return title, sorted({p["name"] for p in people[:limit]}, key=norm)


def request_params(title: str, names: list[str]) -> dict:
    content = f"Work: {title}\n\nPeople named in it:\n" + "\n".join(f"- {n}" for n in names)
    return {"model": MODEL, "max_tokens": MAX_TOKENS, "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": content}],
            "output_config": {"effort": EFFORT, "format": {"type": "json_schema", "schema": SCHEMA}}}


def ask(client, title: str, names: list[str], batch: int) -> tuple[dict, int, int]:
    """One request per batch of people; the answers are combined (repeated ancestors are merged later)."""
    combined: dict = {"people": [], "relationships": []}
    tokens_in = tokens_out = 0
    for start in range(0, len(names), batch):
        chunk = names[start:start + batch]
        with client.messages.stream(**request_params(title, chunk)) as stream:
            final = stream.get_final_message()
        tokens_in += final.usage.input_tokens
        tokens_out += final.usage.output_tokens
        if final.stop_reason != "end_turn":
            sys.exit(f"Claude stopped early ({final.stop_reason}) on people {start + 1}-{start + len(chunk)}; nothing saved. "
                     f"Tokens so far: {tokens_in:,} in / {tokens_out:,} out (about ${cost(tokens_in, tokens_out, batch=False):.2f}).")
        raw = json.loads(next(b.text for b in final.content if b.type == "text"))
        combined["people"] += raw["people"]
        combined["relationships"] += raw["relationships"]
        print(f"  people {start + 1}-{start + len(chunk)} of {len(names)} done", flush=True)
    return combined, tokens_in, tokens_out


def validate(raw: dict, requested: list[str]) -> tuple[dict, dict]:
    """Drop duplicates, links to unknown people, self-links and any parent link that would make a cycle."""
    people, seen = [], set()
    for p in raw["people"]:
        key = norm(p["name"])
        if key and key not in seen:
            seen.add(key)
            people.append(p)
    problems = {"unknown_endpoint": [], "self_link": [], "cycle": [], "duplicate": 0}
    parents: dict[str, set[str]] = {}
    links, seen_links = [], set()

    def is_ancestor(a: str, b: str) -> bool:     # is a already a descendant of b?
        stack, visited = [a], set()
        while stack:
            x = stack.pop()
            if x == b:
                return True
            if x in visited:
                continue
            visited.add(x)
            stack += parents.get(x, ())
        return False

    for r in raw["relationships"]:
        a, b = norm(r["a"]), norm(r["b"])
        if a not in seen or b not in seen:
            problems["unknown_endpoint"].append([r["a"], r["relation"], r["b"]])
        elif a == b:
            problems["self_link"].append([r["a"], r["relation"], r["b"]])
        elif (a, r["relation"], b) in seen_links or (r["relation"] == "spouse_of" and (b, r["relation"], a) in seen_links):
            problems["duplicate"] += 1
        elif r["relation"] == "parent_of" and is_ancestor(a, b):
            problems["cycle"].append([r["a"], r["relation"], r["b"]])
        else:
            seen_links.add((a, r["relation"], b))
            if r["relation"] == "parent_of":
                parents.setdefault(b, set()).add(a)
            links.append(r)
    have = {norm(p["name"]) for p in people}
    report = {"people": len(people), "relationships": len(links),
              "requested": len(requested), "requested_missing": [n for n in requested if norm(n) not in have],
              "added_ancestors": len(people) - sum(1 for n in requested if norm(n) in have),
              "dropped": problems}
    return {"people": people, "relationships": links}, report


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug")
    ap.add_argument("--yes", action="store_true", help="confirm that you want to spend money")
    ap.add_argument("--force", action="store_true", help="replace an existing outside.json")
    ap.add_argument("--limit", type=int, default=100, help="ask about the N most important people")
    ap.add_argument("--batch", type=int, default=35, help="people per request")
    args = ap.parse_args()

    out = ROOT / "data" / args.slug / "curated" / "outside.json"
    title, names = load_book(args.slug, args.limit)
    client = get_client()
    requests = -(-len(names) // args.batch)
    tokens_in = sum(client.messages.count_tokens(model=MODEL, system=SYSTEM_PROMPT,
                    messages=request_params(title, names[i:i + args.batch])["messages"]).input_tokens
                    for i in range(0, len(names), args.batch))
    print(f"{title}: {len(names)} people in {requests} requests; input {tokens_in:,} tokens; output assumed 4,000 per request")
    print(f"estimated cost: ${cost(tokens_in, 4000 * requests, batch=False):.2f} (less if the book has little outside tradition)")
    if not args.yes:
        return print(f"People: {', '.join(names)}\nTo run it: python -m annotate.outside {args.slug} --yes")
    if out.exists() and not args.force:
        sys.exit(f"{out} already exists and may have hand edits. Use --force to replace it.")

    raw, used_in, used_out = ask(client, title, names, args.batch)
    result, report = validate(raw, names)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"model": MODEL, "work": title, "requested": names, **result, "report": report},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"saved {out}")
    print(f"{report['people']} people ({report['added_ancestors']} added ancestors), {report['relationships']} links; "
          f"tokens {used_in:,} in / {used_out:,} out = about ${cost(used_in, used_out, batch=False):.2f}")
    print("missing from the answer:", report["requested_missing"] or "none")
    print("dropped:", {k: (len(v) if isinstance(v, list) else v) for k, v in report["dropped"].items()})


if __name__ == "__main__":
    main()
