"""The notable things of a book, each with a profile like a place's: the Golden Fleece, the aegis, Achilles' shield,
the wooden horse, Pandora's jar, moly, the bow of Ulysses.

    python -m annotate.items <name>          show what would be sent and what it costs; spends nothing
    python -m annotate.items <name> --yes    ask Claude, then save data/<name>/output/items.json
    python -m annotate.items <name> --recheck   fetch the saved things' Wikipedia articles again (free)

One request goes to Claude with two lists: every scene of the book (its title and summary, as written while
annotating), and the capitalised names of the text that are neither characters nor places, with how often each is
used (the Palladium, the Aegis). Claude returns one entry per notable thing: its name as this translation gives it,
the forms the book and the scenes use for it, what kind of thing it is, who owns or uses it, a line saying what it
is, and its English Wikipedia article. The rest is done here, free: the scenes that tell of it (their summaries
name it), the sections that name it (by searching the text for its longer names), and the opening of its Wikipedia
article. annotate.images then fetches its picture.
"""
import argparse
import json
import re
import sys
import urllib.parse
from collections import Counter

from annotate.merge import norm
from annotate.places import EFFORT, LEADING, MAX_TOKENS, NAME, cast_of, relink, wikipedia_leads
from annotate.run import ROOT, cost, get_client
from annotate.schema import MODEL

KINDS = ["weapon", "armour", "treasure", "garment", "vessel", "ship", "plant", "artwork", "instrument", "other"]

SYSTEM_PROMPT = """You are listing the notable things of one book for a reading companion, so that each can have a
profile like a character's or a place's. You are given the book's title and two lists. SCENES are the book's scenes
as a note-taker summarised them. NAMES are capitalised words of the text that are neither characters nor places,
with how often each is used; most are not things (peoples, adjectives, gods' titles). Return JSON that matches the
schema: one entry per thing.

- A thing is a particular object that matters in the story and that a reader may want to look up: a famous object
  of myth (the Golden Fleece, the aegis, Pandora's jar, the Palladium, the golden apple of discord, Zeus' thunderbolt,
  the herb moly), or an object the story turns on or comes back to (Achilles' shield, the bow of Ulysses, the wooden
  horse, the necklace of Harmonia, the ship Argo). Not ordinary things (a cup, a spear, a feast), not people, gods,
  creatures, places or peoples, and not an abstract idea.
- `name` is the thing as this translation names it most often ("the Golden Fleece", "the aegis").
- `forms` lists the other ways the scenes and the text name it ("the fleece", "the golden fleece of the ram"), so
  the companion can find it; leave out forms that would also mean something else ("the bow" alone).
- `kind` is one of: """ + ", ".join(KINDS) + """.
- `owners`: the characters who own, carry or use it, as the scenes name them; [] if none.
- `description`: what the thing is in this book, at most 20 words, plain ("the fleece of the winged ram, kept in
  Colchis, which Jason sails to fetch").
- `wikipedia`: the title of the English Wikipedia article about this thing itself, or "" if there is none or you
  are not sure. Never an article about a person, god, place or creature of the same name, nor a general article
  about that kind of object ("Tripod", "Shield").
- Be complete but selective: every thing that is named in two or more scenes, and every famous object of myth the
  book mentions even once. A short book may have only a few.""".strip()

_STR = {"type": "string"}


def _obj(**props) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


SCHEMA = _obj(items={"type": "array", "items": _obj(
    name=_STR, forms={"type": "array", "items": _STR}, kind={"type": "string", "enum": KINDS},
    owners={"type": "array", "items": _STR}, description=_STR, wikipedia=_STR)})


def gather(slug: str) -> tuple[list[dict], list[dict], Counter]:
    """The book's story sections, every scene (section, number, title, summary) in order, and the capitalised names
    of the text that are neither characters nor places, with their counts."""
    out = ROOT / "data" / slug / "output"
    book = json.loads((out / "sections.json").read_text(encoding="utf-8"))
    body = [s for s in book["sections"] if s["category"] == "body"]
    scenes = []
    for s in body:
        file = out / "annotations" / f"{s['id']}.json"
        if file.exists():
            for n, sc in enumerate(json.loads(file.read_text(encoding="utf-8"))["result"].get("scenes", [])):
                scenes.append({"section": s["id"], "scene": n, "title": sc["title"], "summary": sc["summary"]})
    known = set(cast_of(slug))
    places = out / "places.json"
    if places.exists():
        known |= {norm(n) for p in json.loads(places.read_text(encoding="utf-8"))["places"] for n in [p["name"], *p["names"]]}
    text = "\n".join(s["text"] for s in body)
    lower = set(re.findall(r"\b[a-zæœÀ-ſ]+\b", text))
    names = Counter()
    for m in NAME.finditer(text):
        name = LEADING.sub("", " ".join(m.group(0).split()))
        if not name or (" " not in name and name.lower() in lower) or norm(name) in known:
            continue          # an ordinary word that opens a sentence, a character or a place
        names[name] += 1
    return body, scenes, names


def request_text(title: str, scenes: list[dict], names: Counter) -> str:
    return "\n".join([f"Book: {title}", "", "SCENES"] + [f"- {sc['title']}: {sc['summary']}" for sc in scenes]
                     + ["", "NAMES (how often used)"] + [f"- {n} ({c})" for n, c in names.most_common() if c >= 2])


def ask(client, text: str) -> tuple[dict, int, int]:
    """One request (streamed: a long answer must be); the answer and the tokens in and out."""
    with client.messages.stream(model=MODEL, max_tokens=MAX_TOKENS, system=SYSTEM_PROMPT, messages=[{"role": "user", "content": text}],
                                output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": SCHEMA}}) as stream:
        r = stream.get_final_message()
    if r.stop_reason != "end_turn":
        sys.exit(f"No answer ({r.stop_reason}); nothing saved.")
    return json.loads(next(b.text for b in r.content if b.type == "text")), r.usage.input_tokens, r.usage.output_tokens


def _pattern(forms: list[str], case: bool) -> re.Pattern | None:
    words = sorted(set(forms), key=len, reverse=True)
    return re.compile(r"\b(?:" + "|".join(re.escape(w) for w in words) + r")\b", 0 if case else re.I) if words else None


def build(answer: dict, body: list[dict], scenes: list[dict]) -> list[dict]:
    """Each thing with the scenes that tell of it, the sections that name it and its Wikipedia page."""
    out, ids = [], Counter()
    for t in answer["items"]:
        forms = [f.strip() for f in dict.fromkeys([t["name"], *t["forms"]]) if f.strip()]
        bare = [re.sub(r"^(?:the|a|an)\s+", "", f, flags=re.I) for f in forms]
        # Scene summaries are the note-taker's words: any form. The text: only forms of two words or more ("golden
        # fleece"), or one capitalised word ("Palladium"), so that "fleece" or "bow" alone does not count.
        in_scenes = _pattern(bare, case=False)
        long = _pattern([f for f in bare if len(f.split()) >= 2], case=False)
        capital = _pattern([f for f in bare if len(f.split()) == 1 and f[:1].isupper()], case=True)
        here = [sc for sc in scenes if in_scenes and in_scenes.search(f"{sc['title']} {sc['summary']}")]
        named = [{"section": s["id"], "count": n} for s in body
                 if (n := sum(len(p.findall(s["text"])) for p in (long, capital) if p))]
        if not here and not named:
            continue                  # named nowhere that can be found
        slug_id = re.sub(r"[^a-z0-9]+", "-", norm(t["name"])).strip("-") or "item"
        ids[slug_id] += 1
        out.append({"id": slug_id + (f"-{ids[slug_id]}" if ids[slug_id] > 1 else ""), "name": t["name"],
                    "names": [f for f in forms if f != t["name"]], "kind": t["kind"], "owners": t["owners"],
                    "description": t["description"], "wikipedia_title": t["wikipedia"].strip(),
                    "scenes": [{"section": sc["section"], "scene": sc["scene"]} for sc in here], "named": named})
    pages = wikipedia_leads(sorted({t["wikipedia_title"] for t in out if t["wikipedia_title"]}))
    for t in out:
        t["wikipedia"] = pages.get(t.pop("wikipedia_title"))
    return sorted(out, key=lambda t: (-len(t["scenes"]), -sum(n["count"] for n in t["named"]), t["name"]))


def not_people(found: list[dict], slug: str) -> int:
    """Drop the Wikipedia article of a thing when it is a character's article (Pygmalion's statue is not Pygmalion),
    unless the article is named as the thing is (the ship Argo, "Argo"). Returns how many were dropped."""
    file = ROOT / "data" / slug / "output" / "about.json"
    people = {urllib.parse.unquote(c["url"]) for c in json.loads(file.read_text(encoding="utf-8")).get("characters", {}).values()
              if c.get("url")} if file.exists() else set()
    dropped = 0
    for t in found:
        url = urllib.parse.unquote(t["wikipedia"]["url"]) if t["wikipedia"] else ""
        title = re.sub(r"\s*\(.*\)$", "", url.rsplit("/", 1)[-1].replace("_", " "))       # "Arion (horse)" is "Arion"
        if url in people and norm(title) != norm(re.sub(r"^the\s+", "", t["name"], flags=re.I)):
            t["wikipedia"] = None
            dropped += 1
    return dropped


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name")
    ap.add_argument("--yes", action="store_true", help="confirm that you want to spend money")
    ap.add_argument("--recheck", action="store_true", help="fetch the saved things' Wikipedia articles again (free)")
    args = ap.parse_args()
    out = ROOT / "data" / args.name / "output"
    if args.recheck:
        saved = json.loads((out / "items.json").read_text(encoding="utf-8"))
        changed, dropped = relink(saved["items"]), not_people(saved["items"], args.name)
        (out / "items.json").write_text(json.dumps(saved, ensure_ascii=False, indent=1), encoding="utf-8")
        return print(f"Re-linked {changed} Wikipedia articles; dropped {dropped} that are a character's (free).")
    title = json.loads((out / "sections.json").read_text(encoding="utf-8")).get("title") or args.name
    body, scenes, names = gather(args.name)
    text = request_text(title, scenes, names)
    client = get_client()
    tokens = client.messages.count_tokens(model=MODEL, system=SYSTEM_PROMPT,
                                          messages=[{"role": "user", "content": text}]).input_tokens
    guess = 70 * min(20 + len(scenes) // 6, 250)        # about 70 output tokens a thing
    print(f"{title}: {len(scenes)} scenes, {sum(1 for c in names.values() if c >= 2)} other names; {tokens:,} input tokens, "
          f"about ${cost(tokens, guess, batch=False):.2f} with {MODEL}.")
    if not args.yes:
        return print(f"Nothing spent. To run it: python -m annotate.items {args.name} --yes")
    answer, used_in, used_out = ask(client, text)
    found = build(answer, body, scenes)
    not_people(found, args.name)
    (out / "items.json").write_text(json.dumps(
        {"work": title, "model": MODEL, "usage": {"input": used_in, "output": used_out}, "items": found},
        ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Saved output/items.json: {len(found)} things, {sum(1 for t in found if t['wikipedia'])} with a Wikipedia "
          f"article; tokens {used_in:,} in / {used_out:,} out = about ${cost(used_in, used_out, batch=False):.2f}")


if __name__ == "__main__":
    main()
