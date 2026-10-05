"""Say how each character's name is pronounced, as the book spells it: "Ulysses" is "yoo-LISS-eez".

    python -m annotate.pronounce <slug>          show how many names are new and what they cost; spends nothing
    python -m annotate.pronounce <slug> --yes    ask Claude and add them to data/pronunciations.json

Wikipedia gives a readable pronunciation for few of these names, and under its own spelling (Odysseus, not
Ulysses), so Claude writes them: a plain English respelling with the stressed syllable in capitals. They are
Claude's, not sourced; for an obscure name it is the usual English way of saying a name of that shape. One file
serves every book, so a name is asked for once and reads the same wherever it appears. The reader shows the
respelling beside the name at the head of a profile.
"""
import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor

from annotate.merge import ROOT, norm
from annotate.run import cost, get_client
from annotate.schema import MODEL

FILE = ROOT / "data" / "pronunciations.json"
PER_REQUEST = 80
OUT_PER_NAME = 22        # output tokens per name, for the estimate
EFFORT = "low"

SYSTEM_PROMPT = """You write pronunciations of the names in a classic book for English-speaking readers of a reading
companion. You are given the book's title and a numbered list of names, each with what the character is. For each
name give `say`: how the name is usually pronounced in English, as a plain respelling.
- Use ordinary English letters only (no phonetic symbols), syllables joined by hyphens, the stressed syllable in
  CAPITALS: Ulysses is "yoo-LISS-eez", Telemachus is "teh-LEM-uh-kus", Penelope is "peh-NEL-uh-pee", Æneas is
  "ih-NEE-us".
- Give the established English pronunciation where there is one (Achilles is "uh-KIL-eez", not the Greek). For a
  rare Greek or Latin name follow the usual English handling of such names; for a name from another language
  (Russian, French, German and so on) with no established English form, keep the stress that language gives it
  (Raskolnikov is "ras-KOL-nih-kof").
- Pronounce the name as it is spelled here, not another form of it (Jove is "johv", not Zeus).
- Give an empty string when the name is an ordinary English word or phrase that needs no help ("the Sun",
  "Mice", "Pandareus' daughters" needs only "pan-DAIR-ee-us" for the name inside it: give that), or when you do
  not know.
Return one answer for every name, with `n` its number.""".strip()

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["names"], "properties": {"names": {
    "type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["n", "say"],
                               "properties": {"n": {"type": "integer"}, "say": {"type": "string"}}}}}}


def well_formed(say: str) -> bool:
    """Plain letters only, and (in a name of several syllables) exactly the stressed ones in capitals."""
    parts = [p for p in say.replace(" ", "-").split("-") if p]
    plain = all(p.isascii() and p.replace("'", "").isalpha() for p in parts)
    return plain and all(p.isupper() or p.islower() for p in parts) and (len(parts) == 1 or any(p.isupper() for p in parts))


def saved() -> dict[str, dict]:
    return json.loads(FILE.read_text(encoding="utf-8")) if FILE.exists() else {}


def request_text(title: str, batch: list[dict]) -> str:
    return f"Book: {title}\n\nNAMES\n" + "\n".join(
        f"{n}. {p['name']} ({p['kind']}" + (f"; {p['about']}" if p["about"] else "") + ")" for n, p in enumerate(batch, 1))


def ask(client, text: str) -> tuple[list[dict], tuple[int, int]]:
    r = client.messages.create(model=MODEL, max_tokens=8000, system=SYSTEM_PROMPT, messages=[{"role": "user", "content": text}],
                               output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": SCHEMA}})
    usage = (r.usage.input_tokens, r.usage.output_tokens)
    if r.stop_reason != "end_turn":
        return [], usage
    return json.loads(next(b.text for b in r.content if b.type == "text"))["names"], usage


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug")
    ap.add_argument("--yes", action="store_true", help="confirm that you want to spend money")
    args = ap.parse_args()

    out = ROOT / "data" / args.slug / "output"
    tree = json.loads((out / "tree.json").read_text(encoding="utf-8"))
    title = json.loads((out / "sections.json").read_text(encoding="utf-8")).get("title") or args.slug
    known = saved()
    people = [{"name": p["name"], "kind": p["kind"], "about": (p["about"] or "")[:80]} for p in tree["people"]
              if p["kind"] not in ("place", "form") and norm(p["name"]) not in known]
    batches = [people[i:i + PER_REQUEST] for i in range(0, len(people), PER_REQUEST)]
    texts = [request_text(title, b) for b in batches]
    if not people:
        return print(f"{title}: every name already has a pronunciation ({len(known)} saved).")
    client = get_client()
    tokens = sum(client.messages.count_tokens(model=MODEL, system=SYSTEM_PROMPT,
                                              messages=[{"role": "user", "content": t}]).input_tokens for t in texts)
    print(f"{title}: {len(people)} names without a pronunciation, {len(batches)} requests; {tokens:,} input tokens, "
          f"about ${cost(tokens, OUT_PER_NAME * len(people), batch=False):.2f} with {MODEL}.")
    if not args.yes:
        return print(f"Nothing spent. To run it: python -m annotate.pronounce {args.slug} --yes")

    tokens_in = tokens_out = added = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        for batch, (answer, usage) in zip(batches, pool.map(lambda t: ask(client, t), texts)):
            tokens_in, tokens_out = tokens_in + usage[0], tokens_out + usage[1]
            for item in answer:
                if 1 <= item["n"] <= len(batch):
                    name = batch[item["n"] - 1]["name"]
                    if item["say"].strip() and not well_formed(item["say"].strip()):
                        continue                # phonetic symbols or no clear stress: leave the name to be asked again
                    known[norm(name)] = {"name": name, "say": item["say"].strip()}      # an empty answer is kept: do not ask again
                    added += bool(item["say"].strip())
    FILE.write_text(json.dumps(dict(sorted(known.items())), ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Saved data/pronunciations.json: {added} pronunciations added ({len(known)} names in all); "
          f"tokens {tokens_in:,} in / {tokens_out:,} out = about ${cost(tokens_in, tokens_out, batch=False):.2f}")


if __name__ == "__main__":
    main()
