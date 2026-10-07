"""Pick each character's key moments in a book: the sections where they are at the centre of the story.

    python -m annotate.moments <slug>          show what would be sent and what it costs; spends nothing
    python -m annotate.moments <slug> --yes    ask Claude and save data/<slug>/output/moments.json
    python -m annotate.moments <slug> --yes --missing   only the people annotate.namesakes split off that have no key
                                               moments yet; the saved ones are kept

A god like Jove is named in almost every section, usually in passing. For a list of references that is worth
reading, Claude is shown every section a character appears in, with what that section says of them, and keeps
only the ones where the character acts, decides, suffers or is the subject of the story, each with a line saying
what happens. The reader shows these as the character's key moments, in this book and (through
annotate.library) in the other books of the library.
"""
import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor

from annotate.merge import merge, norm
from annotate.run import ROOT, cost, get_client
from annotate.schema import MODEL

MAX_TOKENS = 16000
EFFORT = "medium"
PER_REQUEST = 60         # characters per request
OUT_PER_PERSON = 45      # output tokens per character, for the estimate

SYSTEM_PROMPT = """You choose the key moments of each character of a book, for a reading companion that lists them as
references to that character. You are given the book's title, its sections in order, and a numbered list of
characters; under each character are the sections they appear in, with what that section says of them and the
scenes of that section in which they are present. Return JSON that matches the schema.

For each character keep only the sections where they are at the centre of the action: they act, decide, speak at
length, suffer, are transformed, are born or die, or are the subject of the story told there. Leave out sections
where they are only named, prayed to, sworn by, compared to, or play a small part in someone else's story.
- A god or hero who is named everywhere gets only their own episodes: the scenes where they themselves decide,
  speak or act (a council of the gods, an omen they send, a ship they destroy). A character who is only ever mentioned
  gets no moments at all: return an empty list for them.
- At most 6 moments per character, or 10 for the main character of the whole book; choose the most important.
- `section` is the section's number as given. `what` says what the character does or what happens to them there,
  in at most 15 words, in the present tense, with names spelled as the book spells them.
Return one entry for every character, with `n` the character's number.""".strip()

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["characters"], "properties": {"characters": {
    "type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["n", "moments"], "properties": {
        "n": {"type": "integer"},
        "moments": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                               "required": ["section", "what"],
                                               "properties": {"section": {"type": "integer"}, "what": {"type": "string"}}}}}}}}}


def appearances(slug: str) -> tuple[str, list[dict], list[dict]]:
    """The book's title, its body sections in order, and for each person of the tree the sections they appear in."""
    out = ROOT / "data" / slug / "output"
    book = json.loads((out / "sections.json").read_text(encoding="utf-8"))
    sections = [s for s in book["sections"] if s["category"] == "body"]
    tree = merge(slug, ROOT)
    owner = {norm(n): p["id"] for p in tree["people"] for n in [*p["aliases"], p["name"]]}
    people = {p["id"]: {"id": p["id"], "name": p["name"], "kind": p["kind"], "sections": {}, "scenes": {}} for p in tree["people"]}
    listed = []
    for number, s in enumerate(sections, 1):
        file = out / "annotations" / f"{s['id']}.json"
        if not file.exists():
            continue
        result = json.loads(file.read_text(encoding="utf-8"))["result"]
        listed.append({"number": number, "id": s["id"], "title": " > ".join(s["path"]), "summary": result.get("summary", "")})
        for c in result["characters"]:
            person = people.get(owner.get(norm(c["name"])))
            if person is not None and c["kind"] != "place":
                person["sections"].setdefault(number, c["description"])
        # The scenes a character is present in say what they do there; the description alone ("king of the gods")
        # does not, and a god who acts in a few scenes was taken for one who is only named.
        for scene in result.get("scenes", []):
            for name in scene["present"]:
                person = people.get(owner.get(norm(name)))
                if person is not None and number in person["sections"]:
                    person["scenes"].setdefault(number, []).append(f"{scene['title']}: {scene['summary']}")
    return book.get("title") or slug, listed, [p for p in people.values() if p["sections"]]


def request_text(title: str, sections: list[dict], batch: list[dict]) -> str:
    lines = [f"Book: {title}", "", "SECTIONS"]
    lines += [f"{s['number']}. {s['title']}" + (f": {s['summary']}" if s["summary"] else "") for s in sections]
    lines += ["", "CHARACTERS"]
    for n, p in enumerate(batch, 1):
        lines.append(f"{n}. {p['name']} ({p['kind']})")
        for number, text in sorted(p["sections"].items()):
            lines.append(f"   section {number}: {text}")
            lines += [f"      present in the scene \"{scene}\"" for scene in p["scenes"].get(number, [])]
    return "\n".join(lines)


def ask(client, text: str) -> tuple[list[dict], tuple[int, int]]:
    r = client.messages.create(model=MODEL, max_tokens=MAX_TOKENS, system=SYSTEM_PROMPT,
                               messages=[{"role": "user", "content": text}],
                               output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": SCHEMA}})
    usage = (r.usage.input_tokens, r.usage.output_tokens)
    if r.stop_reason != "end_turn":
        return [], usage
    return json.loads(next(b.text for b in r.content if b.type == "text"))["characters"], usage


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug")
    ap.add_argument("--yes", action="store_true", help="confirm that you want to spend money")
    ap.add_argument("--missing", action="store_true", help="keep the saved key moments; ask only for characters without any")
    args = ap.parse_args()

    title, sections, people = appearances(args.slug)
    file = ROOT / "data" / args.slug / "output" / "moments.json"
    kept = json.loads(file.read_text(encoding="utf-8"))["people"] if args.missing and file.exists() else {}
    if args.missing:        # only the people annotate.namesakes split off, not those the first run gave no moments
        split = ROOT / "data" / args.slug / "output" / "namesakes.json"
        new = {g["name"] for s in json.loads(split.read_text(encoding="utf-8"))["split"] for g in s["groups"]} if split.exists() else set()
        people = [p for p in people if p["name"] in new and p["name"] not in kept]
    batches = [people[i:i + PER_REQUEST] for i in range(0, len(people), PER_REQUEST)]
    texts = [request_text(title, sections, b) for b in batches]
    client = get_client()
    tokens = sum(client.messages.count_tokens(model=MODEL, system=SYSTEM_PROMPT,
                                              messages=[{"role": "user", "content": t}]).input_tokens for t in texts)
    print(f"{title}: {len(people)} characters in {len(sections)} sections, {len(batches)} requests; {tokens:,} input tokens, "
          f"about ${cost(tokens, OUT_PER_PERSON * len(people), batch=False):.2f} with {MODEL}.")
    if not args.yes:
        return print(f"Nothing spent. To run it: python -m annotate.moments {args.slug} --yes")

    by_number = {s["number"]: s["id"] for s in sections}
    saved, tokens_in, tokens_out, failed = dict(kept), 0, 0, 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        for batch, (answer, usage) in zip(batches, pool.map(lambda t: ask(client, t), texts)):
            tokens_in, tokens_out = tokens_in + usage[0], tokens_out + usage[1]
            failed += not answer
            for item in answer:
                if not 1 <= item["n"] <= len(batch):
                    continue
                person = batch[item["n"] - 1]
                moments = [{"section": by_number[m["section"]], "what": m["what"].strip()}
                           for m in sorted(item["moments"], key=lambda m: m["section"])
                           if m["section"] in person["sections"] and m["what"].strip()]     # only sections they are really in
                if moments:
                    saved[person["name"]] = moments
    file.write_text(json.dumps(
        {"work": title, "model": MODEL, "people": saved}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Saved output/moments.json: {sum(len(m) for m in saved.values())} key moments for {len(saved)} of {len(people)} "
          f"characters; tokens {tokens_in:,} in / {tokens_out:,} out = about ${cost(tokens_in, tokens_out, batch=False):.2f}"
          + (f"; {failed} requests gave no answer (run again to retry)" if failed else ""))


if __name__ == "__main__":
    main()
