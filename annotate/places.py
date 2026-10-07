"""The places of a book, each with a profile like a character's: Tartarus, Olympus, Ogygia, Troy, Ulysses' house.

    python -m annotate.places <name>          show what would be sent and what it costs; spends nothing
    python -m annotate.places <name> --yes    ask Claude, then save data/<name>/output/places.json
    python -m annotate.places <name> --recheck   fetch the saved places' Wikipedia articles again and apply
                                                 data/<name>/curated/places.json (free): corrected Wikipedia
                                                 articles, and places added by hand

Two lists go to Claude in one request: the place of every scene (written freely while annotating, so one place
has several spellings: "Plain of Troy", "the plain before Ilius"), and the capitalised names of the text that are
not characters, with how often each is used, which catches places named but never visited (Tartarus in the
Iliad). Claude returns one entry per place: its name as this translation spells it, every form in the two lists
that means it, what kind of place it is, whether it is real, legendary or mythical, a line saying what it is, and
its English Wikipedia article. A long list comes back more or less complete from run to run, so a second, short
request offers Claude what its answer left out (scene places and names used twice or more that no entry includes),
and a new answer replaces the saved one only if it gives as many scenes a place profile. The rest is done here, free: the scenes set at each place, the sections that name
it (by searching the text for its names), its map position (from its scenes) and the opening of its Wikipedia
article. annotate.images then fetches its picture.
"""
import argparse
import glob
import json
import re
import sys
import urllib.parse
from collections import Counter

from annotate.about import EMPTY_BRACKETS, WIKIPEDIA, fetch
from annotate.merge import norm
from annotate.run import ROOT, cost, get_client
from annotate.schema import MODEL

MAX_TOKENS = 32000
EFFORT = "medium"
WORD = r"[A-ZÆŒ][a-zæœÀ-ſ]+"
NAME = re.compile(rf"{WORD}(?:\s+(?:of\s+(?:the\s+)?)?{WORD})*")
LEADING = re.compile(r"^(?:The|And|Then|When|But|If|Now|So|For|King|Queen|Father|Mother|Mount|Mt)\s+")
KINDS = ["realm", "region", "city", "island", "mountain", "river", "sea", "building", "other"]
# Wikipedia's page for a name that several things share, and the titles its ancient Greek place usually has.
DISAMBIGUATION = re.compile(r"\bmay (?:also )?refer to\b")
QUALIFIED = ("{} (ancient Greece)", "Ancient {}", "{} (Greece)", "{} (region)", "{}, Greece", "{} (ancient city)")

SYSTEM_PROMPT = """You are listing the places of one book for a reading companion, so that each place can have a
profile. You are given the book's title and two lists. SCENE PLACES are where the scenes of the book happen, as a
note-taker wrote them, so one place often has several forms ("Plain of Troy", "the plain before Ilius"), and some
are descriptions ("Ulysses' house, Ithaca"). NAMES are capitalised words of the text that are not characters, with
how often each is used; most are not places (peoples such as the Trojans, adjectives, gods' titles, the first word
of a sentence). Return JSON that matches the schema: one entry per place.

- A place is somewhere on earth or in the story's world: a realm (Olympus, Tartarus, Erebus, Elysium, the
  underworld), a region, a city, an island, a mountain, a river, a sea, or a building or spot that is the setting
  of a scene more than once (Ulysses' house, Circe's house). Peoples, gods, adjectives ("Trojan") and things are not.
  The same word can be a god and a place (Tartarus, Styx, Oceanus): list the place when the book uses it as one.
- `name` is the place as this translation names it most often (Ilius or Troy, whichever it uses more).
- `forms` lists every entry of the two lists that means this place, spelled exactly as given, so the companion can
  find them: scene places in full ("Plain before Troy") and names ("Ilius", "Troy"). An entry belongs to one place
  only; a scene place naming two places ("Olympus and the plain of Troy") goes with the first.
- `kind` is one of: """ + ", ".join(KINDS) + """.
- `certainty`: "real" (a known place), "traditional" (a legendary place tradition identifies with a real one, such
  as Scheria with Corfu) or "mythical" (no earthly place: Olympus as the gods' home, Tartarus, the Elysian fields).
- `description`: what the place is in this book, at most 20 words, plain ("the deepest pit beneath the underworld,
  where the Titans are imprisoned").
- `wikipedia`: the title of the English Wikipedia article about this place itself, or "" if there is none or you
  are not sure. Never an article about a person, god, people or creature of the same name, nor about a building
  that stood there later.
- Be complete. Every name in NAMES that is a place gets an entry, however rarely the book uses it (a town in a
  list of ships, a river named once, a realm of the dead). Every scene place goes in the `forms` of some entry,
  except one with no name at all ("a road", "the sea"). A building, tent, camp or ship that is the setting of two or
  more scenes is a place of its own (Ulysses' house, Achilles' tent, the ships of the Myrmidons).""".strip()

_STR = {"type": "string"}


def _obj(**props) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


SCHEMA = _obj(places={"type": "array", "items": _obj(
    name=_STR, forms={"type": "array", "items": _STR}, kind={"type": "string", "enum": KINDS},
    certainty={"type": "string", "enum": ["real", "traditional", "mythical"]}, description=_STR, wikipedia=_STR)})


def gather(slug: str) -> tuple[list[dict], dict, Counter, Counter]:
    """The book's story sections, every scene (section, number, place) in order, the scene places with how often
    each is used, and the capitalised names of the text that are not characters, with their counts."""
    out = ROOT / "data" / slug / "output"
    book = json.loads((out / "sections.json").read_text(encoding="utf-8"))
    tree = json.loads((out / "tree.json").read_text(encoding="utf-8"))
    cast = {norm(n) for p in tree["people"] if p["kind"] != "place" for n in [p["name"], *p["aliases"]]}
    body = [s for s in book["sections"] if s["category"] == "body"]
    scenes, places = [], Counter()
    for s in body:
        file = out / "annotations" / f"{s['id']}.json"
        if file.exists():
            for n, sc in enumerate(json.loads(file.read_text(encoding="utf-8"))["result"].get("scenes", [])):
                scenes.append({"section": s["id"], "scene": n, **sc["place"]})
                if sc["place"]["name"].strip():
                    places[sc["place"]["name"].strip()] += 1
    text = "\n".join(s["text"] for s in body)
    lower = set(re.findall(r"\b[a-zæœÀ-ſ]+\b", text))
    names = Counter()
    for m in NAME.finditer(text):
        name = LEADING.sub("", " ".join(m.group(0).split()))
        if not name or (" " not in name and name.lower() in lower) or norm(name) in cast:
            continue          # an ordinary word that opens a sentence, or a character
        names[name] += 1
    return body, scenes, places, names


def request_text(title: str, places: Counter, names: Counter) -> str:
    return "\n".join([f"Book: {title}", "", "SCENE PLACES (how many scenes)"]
                     + [f"- {p} ({c})" for p, c in places.most_common()]
                     + ["", "NAMES (how often used)"] + [f"- {n} ({c})" for n, c in names.most_common()])


def wikipedia_leads(titles: list[str], resolve: bool = True) -> dict[str, dict]:
    """{title asked for: {url, lead}} for the titles that are real articles (redirects followed). A title that is a
    list of things of that name ("Corinth may refer to: ...") gets the ancient place's own article when Wikipedia has
    one under a usual qualified title ("Ancient Corinth", "Aulis (ancient Greece)", "Mount Ossa (Greece)"), and no
    article otherwise."""
    found, ambiguous = {}, []
    for start in range(0, len(titles), 20):
        answer = fetch(WIKIPEDIA + "?" + urllib.parse.urlencode({
            "action": "query", "prop": "extracts|info|pageprops", "ppprop": "disambiguation", "inprop": "url",
            "exintro": "1", "explaintext": "1", "exsentences": "2", "exlimit": "20", "redirects": "1",
            "titles": "|".join(titles[start:start + 20]), "format": "json", "formatversion": "2"}))["query"]
        renamed = {r["to"]: r["from"] for r in answer.get("normalized", []) + answer.get("redirects", [])}
        for page in answer.get("pages", []):
            if page.get("missing") or not page.get("extract"):
                continue
            title = page["title"]
            while title not in titles and title in renamed:
                title = renamed[title]
            if "disambiguation" in page.get("pageprops", {}) or DISAMBIGUATION.search(page["extract"][:300]):
                ambiguous.append(title)
                continue
            lead = re.sub(r"\s*\((?=[^()]*(?:;|[^\x00-\x7f]))(?:[^()]|\([^()]*\))*\)", "", page["extract"], count=1)
            found[title] = {"url": page["fullurl"], "lead": " ".join(EMPTY_BRACKETS.sub("", lead).split())}
    if ambiguous and resolve:
        tries = {t: [q.format(t) for q in QUALIFIED] for t in ambiguous}
        pages = wikipedia_leads(sorted({q for qs in tries.values() for q in qs}), resolve=False)
        for t, qs in tries.items():
            if page := next((pages[q] for q in qs if q in pages), None):
                found[t] = page
    return found


def relink(found: list[dict]) -> int:
    """Fetch the Wikipedia article of each saved place or thing again (free), so that a list of things of one name
    saved earlier is replaced by the right article, or by none. Returns how many changed."""
    titles = {p["id"]: urllib.parse.unquote(p["wikipedia"]["url"].rsplit("/", 1)[1]).replace("_", " ")
              for p in found if p.get("wikipedia")}
    pages = wikipedia_leads(sorted(set(titles.values())))
    changed = 0
    for p in found:
        if p["id"] in titles:
            new = pages.get(titles[p["id"]])
            changed += (new or {}).get("url") != p["wikipedia"]["url"]
            p["wikipedia"] = new
    return changed


def build(answer: dict, body: list[dict], scenes: list[dict], cast: set[str] = frozenset()) -> list[dict]:
    """Each place with its scenes, the sections that name it, a map position and its Wikipedia page. `cast` is the
    book's character names: a form naming someone else ("Telemachus' room" of Ulysses' house) is not searched for."""
    out, taken, ids = [], set(), Counter()
    for p in answer["places"]:
        forms = [f for f in dict.fromkeys([p["name"], *p["forms"]]) if f.strip() and norm(f) not in taken]
        if not forms:
            continue
        taken.update(norm(f) for f in forms)
        keys = {norm(f) for f in forms}
        here = [sc for sc in scenes if norm(sc["name"]) in keys]
        # Names to look for in the text: the short forms ("Troy", "Ilius"), not the scene descriptions.
        main = f" {norm(p['name'])} "
        words = sorted({f for f in forms if len(f.split()) <= 3 and "," not in f
                        and not any(f" {c} " in f" {norm(f)} " and c != norm(f) and f" {c} " not in main for c in cast)},
                       key=len, reverse=True)       # "Scamander" is the river's own name though the god has it too
        pattern = re.compile(r"\b(?:" + "|".join(re.escape(w) for w in words) + r")\b") if words else None
        named = [{"section": s["id"], "count": n} for s in body if pattern and (n := len(pattern.findall(s["text"])))]
        spots = Counter((sc["lat"], sc["lon"]) for sc in here if sc["lat"] or sc["lon"])
        slug_id = re.sub(r"[^a-z0-9]+", "-", norm(p["name"])).strip("-") or "place"
        ids[slug_id] += 1
        out.append({"id": slug_id + (f"-{ids[slug_id]}" if ids[slug_id] > 1 else ""), "name": p["name"],
                    "names": [f for f in forms if f != p["name"]], "kind": p["kind"], "certainty": p["certainty"],
                    "description": p["description"], "wikipedia_title": p["wikipedia"].strip(),
                    "lat": spots.most_common(1)[0][0][0] if spots else None,
                    "lon": spots.most_common(1)[0][0][1] if spots else None,
                    "scenes": [{"section": sc["section"], "scene": sc["scene"]} for sc in here], "named": named})
    pages = wikipedia_leads(sorted({p["wikipedia_title"] for p in out if p["wikipedia_title"]}))
    for p in out:
        p["wikipedia"] = pages.get(p.pop("wikipedia_title"))
    # Most visited and most named first.
    return sorted(out, key=lambda p: (-len(p["scenes"]), -sum(n["count"] for n in p["named"]), p["name"]))


def cast_of(slug: str) -> set[str]:
    """The book's character names, spelling-insensitive (for build's `cast`)."""
    tree = json.loads((ROOT / "data" / slug / "output" / "tree.json").read_text(encoding="utf-8"))
    return {k for p in tree["people"] if p["kind"] not in ("place", "form") for n in [p["name"], *p["aliases"]] if len(k := norm(n)) > 2}


def apply_curated(found: list[dict], slug: str) -> list[dict]:
    """Hand corrections in data/<slug>/curated/places.json win over Claude's choices.
    "wikipedia": {"Heaven": ""} gives the place Heaven no article; a title instead of "" gives it that article.
    "add": [{"name", "forms", "kind", "certainty", "description", "wikipedia"}] adds places by hand, `forms` being
    the scene places and names that mean the place, spelled as in the book; their scenes, the sections naming them,
    a map position and the article's opening are worked out as for Claude's. An added place replaces one of the
    same name and takes over its scenes from any other place."""
    file = ROOT / "data" / slug / "curated" / "places.json"
    curated = json.loads(file.read_text(encoding="utf-8")) if file.exists() else {}
    fixes = curated.get("wikipedia", {})
    pages = wikipedia_leads(sorted({t for t in fixes.values() if t}))
    for p in found:
        if p["name"] in fixes:
            p["wikipedia"] = pages.get(fixes[p["name"]]) if fixes[p["name"]] else None
    if not curated.get("add"):
        return found
    body, scenes, _, _ = gather(slug)
    hand = build({"places": [{"forms": [], "description": "", "wikipedia": "", **a} for a in curated["add"]]},
                 body, scenes, cast_of(slug))
    theirs = {norm(f) for p in hand for f in [p["name"], *p["names"]]}
    claimed = {(s["section"], s["scene"]) for p in hand for s in p["scenes"]}
    found = [p for p in found if norm(p["name"]) not in theirs]
    for p in found:
        p["names"] = [n for n in p["names"] if norm(n) not in theirs]
        p["scenes"] = [s for s in p["scenes"] if (s["section"], s["scene"]) not in claimed]
    taken = {p["id"] for p in found}
    for p in hand:
        while p["id"] in taken:
            p["id"] += "-hand"
        taken.add(p["id"])
    return sorted(found + hand, key=lambda p: (-len(p["scenes"]), -sum(n["count"] for n in p["named"]), p["name"]))


GAP_PROMPT = """

SECOND PASS. You already listed the PLACES named in the request, but the LEFT OVER items (scene places and names)
are in none of them. Return an entry for each left-over item that is a place, by the same rules: to add it to a
listed place, give that place's `name` exactly as listed, the item in `forms`, its kind and certainty, and ""
for `description` and `wikipedia`; for a new place, a full entry. Leave out the items that are not places."""


def ask(client, system: str, text: str) -> tuple[dict, int, int]:
    """One request (streamed: a long answer must be); the answer and the tokens in and out."""
    with client.messages.stream(model=MODEL, max_tokens=MAX_TOKENS, system=system, messages=[{"role": "user", "content": text}],
                                output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": SCHEMA}}) as stream:
        r = stream.get_final_message()
    if r.stop_reason != "end_turn":
        sys.exit(f"No answer ({r.stop_reason}); nothing saved.")
    return json.loads(next(b.text for b in r.content if b.type == "text")), r.usage.input_tokens, r.usage.output_tokens


def left_over(answer: dict, places: Counter, names: Counter) -> tuple[list, list]:
    """The scene places, and the names used twice or more, that no entry of the answer includes."""
    forms = {norm(f) for p in answer["places"] for f in [p["name"], *p["forms"]]}
    return ([(n, c) for n, c in places.most_common() if norm(n) not in forms],
            [(n, c) for n, c in names.most_common() if c >= 2 and norm(n) not in forms])


def gap_text(title: str, answer: dict, scene_left: list, names_left: list) -> str:
    return "\n".join([f"Book: {title}", "", "PLACES"] + [f"- {p['name']} ({p['kind']})" for p in answer["places"]]
                     + ["", "LEFT OVER: SCENE PLACES (how many scenes)"] + [f"- {n} ({c})" for n, c in scene_left]
                     + ["", "LEFT OVER: NAMES (how often used)"] + [f"- {n} ({c})" for n, c in names_left])


def fill_gaps(answer: dict, extra: dict) -> dict:
    """Add the second answer to the first: forms of a listed place join it, new places are added."""
    listed = {norm(p["name"]): p for p in answer["places"]}
    for p in extra["places"]:
        if norm(p["name"]) in listed:
            listed[norm(p["name"])]["forms"] += p["forms"]
        else:
            answer["places"].append(p)
            listed[norm(p["name"])] = p
    return answer


def covered(found: list[dict], places: Counter) -> int:
    """How many scenes are set at a place that has a profile."""
    forms = {norm(f) for p in found for f in [p["name"], *p["names"]]}
    return sum(c for n, c in places.items() if norm(n) in forms)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name")
    ap.add_argument("--yes", action="store_true", help="confirm that you want to spend money")
    ap.add_argument("--recheck", action="store_true", help="re-fetch the saved places' Wikipedia articles and apply curated/places.json (free)")
    args = ap.parse_args()
    out = ROOT / "data" / args.name / "output"
    if args.recheck:
        saved = json.loads((out / "places.json").read_text(encoding="utf-8"))
        changed = relink(saved["places"])
        saved["places"] = apply_curated(saved["places"], args.name)
        (out / "places.json").write_text(json.dumps(saved, ensure_ascii=False, indent=1), encoding="utf-8")
        return print(f"Re-linked {changed} Wikipedia articles and applied curated/places.json to {len(saved['places'])} "
                     f"saved places (free).")
    title = json.loads((out / "sections.json").read_text(encoding="utf-8")).get("title") or args.name
    body, scenes, places, names = gather(args.name)
    text = request_text(title, places, names)
    client = get_client()
    tokens = client.messages.count_tokens(model=MODEL, system=SYSTEM_PROMPT,
                                          messages=[{"role": "user", "content": text}]).input_tokens
    guess = 60 * min(len(places) + len(names) // 4, 400)       # about 60 output tokens a place
    print(f"{title}: {len(places)} scene places, {len(names)} other names; {tokens:,} input tokens, "
          f"about ${cost(tokens, guess, batch=False):.2f} with {MODEL}.")
    if not args.yes:
        return print(f"Nothing spent. To run it: python -m annotate.places {args.name} --yes")
    answer, used_in, used_out = ask(client, SYSTEM_PROMPT, text)
    # One long list comes back more or less complete from run to run, so a second, short request offers what the
    # first answer left out: scene places no entry includes, and names used twice or more that no entry includes.
    scene_left, names_left = left_over(answer, places, names)
    if scene_left or names_left:
        extra, more_in, more_out = ask(client, SYSTEM_PROMPT + GAP_PROMPT, gap_text(title, answer, scene_left, names_left))
        answer = fill_gaps(answer, extra)
        used_in, used_out = used_in + more_in, used_out + more_out
    found = apply_curated(build(answer, body, scenes, cast_of(args.name)), args.name)
    spent = f"tokens {used_in:,} in / {used_out:,} out = about ${cost(used_in, used_out, batch=False):.2f}"
    # Keep whichever list gives more scenes a place profile (then more places): a worse answer never replaces a better one.
    if (out / "places.json").exists():
        old = json.loads((out / "places.json").read_text(encoding="utf-8"))["places"]
        if (covered(old, places), len(old)) > (covered(found, places), len(found)):
            return print(f"Kept the saved places.json ({len(old)} places, {covered(old, places)} scenes) over this answer "
                         f"({len(found)} places, {covered(found, places)} scenes); {spent}")
    (out / "places.json").write_text(json.dumps(
        {"work": title, "model": MODEL, "usage": {"input": used_in, "output": used_out},
         "places": found}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Saved output/places.json: {len(found)} places, {sum(1 for p in found if p['wikipedia'])} with a Wikipedia "
          f"article; {spent}")
    # A thin answer still shows here: scenes whose place got no profile.
    forms = {norm(f) for p in found for f in [p["name"], *p["names"]]}
    left = [(n, c) for n, c in places.most_common() if norm(n) not in forms]
    print(f"  Scenes with a place profile: {covered(found, places)} of {sum(places.values())}."
          + (" Left out: " + "; ".join(f"{n} ({c})" for n, c in left[:12]) if left else ""))


if __name__ == "__main__":
    main()
