"""Merge the per-section annotations of a book into one family tree: each person once, each relationship once.

    python -m annotate.merge <slug> [--out PATH]

Reads data/<slug>/output/sections.json and annotations/*.json, writes data/<slug>/output/tree.json.
If data/<slug>/curated/outside.json exists (python -m annotate.outside <slug>) its people and family links are
added as "outside the book" links; people are joined by name or alternate name, and a link the book itself
states stays a book link. People who are not named in the book appear only once someone they are connected
to has been reached (first_index is the earliest such section, or null if none).
Names are matched by spelling (accents and ligatures ignored) and by the aliases the model gave. Only plain
proper-name aliases ("Jove", "Phoebus") join two names; descriptive epithets such as "the son of Saturn" can
fit several people, so they are kept for display but never used to merge.

Two names that the model lists as separate characters in the same section are not merged unless the
aliases clearly say they are one person. Corrections go in data/<slug>/curated/aliases.json:
    {"merge": [["Phoebus", "Apollo"]], "separate": [["Apollo", "Diana"]],
     "remove": [["Atlas", "parent_of", "Jupiter"]],
     "add": [["Phoebe the Titaness", "parent_of", "Latona", "optional note"]],
     "about": {"Phoebe the Titaness": "Titaness of brightness; grandmother of Apollo"}}
"remove" deletes a wrong relationship, whether the book model or the outside layer produced it. "add" supplies
a missing one (parent_of, spouse_of, sibling_of, lover_of or transformed_into); it is drawn as an outside link,
and names that are not in the book become new people. "about" sets the one-line description shown on click.

If data/<slug>/output/wikidata.json exists (python -m annotate.verify <slug>) the tree is checked against
Wikidata, for every book:
  - names matched to different entries are never joined (the sun is not Apollo); names matched to the same
    entry are joined, and so are a Roman god and its Greek counterpart, and a god and the thing it personifies;
  - a parent link that Wikidata states the other way round is turned round;
  - a "parent" who is really a grandparent is dropped (the book said "his mother's father");
  - any other link that Wikidata contradicts is dropped if it came from outside the book, and kept but marked
    "differs" if the book itself states it;
  - Wikidata's own parent links between people of the book are added as outside links.
Hand corrections in aliases.json are applied last and always win. The report lists every change.

If data/<slug>/output/review.json exists (python -m annotate.review <slug>, Claude reading the whole tree once)
its decisions are applied too: names it found to be one being are joined, and names it found wrongly joined are kept
apart. Hand corrections win over these as well.

If data/<slug>/output/namesakes.json exists (python -m annotate.namesakes <slug>) different people who share a name
are kept apart: each section's entry for that name is given the name of the person it is.
"""
import argparse
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SYMMETRIC = {"spouse_of", "sibling_of", "lover_of"}
RELATIONS = SYMMETRIC | {"parent_of", "transformed_into"}
# The model sometimes writes "invalid" or "incorrect" in the note of a link it has just given; drop those.
BAD_NOTE_WORDS = ("invalid", "incorrect", "mistake", "error")
LOVER_NOTE = re.compile(r"\blover\b|not (a |the )?(formal )?(marriage|wife|husband)|\bconsort, not\b", re.I)
GENERIC = {"father", "mother", "son", "daughter", "god", "goddess", "gods", "goddesses", "king", "queen", "lord",
           "lady", "hero", "nymph", "nymphs", "maiden", "youth", "man", "woman", "men", "women", "mortal",
           "mortals", "brother", "sister", "wife", "husband", "he", "she", "the"}


def norm(name: str) -> str:
    """Spelling-insensitive key: 'Phœbus' and 'Phoebus' are the same."""
    s = name.replace("œ", "oe").replace("Œ", "oe").replace("æ", "ae").replace("Æ", "ae")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return " ".join(re.sub(r"[^a-z ]", " ", s).split())


def is_proper_name(alias: str) -> bool:
    """A plain name that may be used to join two entries; not 'the Father' or 'the son of Saturn'."""
    a = alias.strip()
    key = norm(a)
    return bool(a) and a[0].isupper() and " of " not in a.lower() and not a.lower().startswith("the ") \
        and key not in GENERIC and len(key) >= 3 and len(key.split()) <= 3


class Groups:
    """Union-find over name keys that also remembers which names are in each group."""

    def __init__(self):
        self.parent: dict[str, str] = {}
        self.members: dict[str, set[str]] = {}

    def find(self, x: str) -> str:
        if x not in self.parent:
            self.parent[x], self.members[x] = x, {x}
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra
            self.members[ra] |= self.members.pop(rb)


def same_being(a: str, b: str, entities: dict) -> bool:
    """Two Wikidata entries for one being: the same entry, a Roman god and its Greek counterpart (Wikidata's "said
    to be the same as"), or a god and the thing it is the god of (Helios and the Sun)."""
    if a == b:
        return True
    A, B = entities.get(a, {}), entities.get(b, {})
    # "Same as" must be stated by both entries: on its own it also links gods across unrelated pantheons.
    return (b in A.get("same", []) and a in B.get("same", [])) or b in A.get("domain", []) or a in B.get("domain", [])


def is_individual(qid: str, entities: dict) -> bool:
    """An entry for one particular being (it has relatives), not a thing or a kind of being such as "earth goddess"."""
    e = entities.get(qid, {})
    return any(e.get(field) for field in ("father", "mother", "child", "spouse", "sibling"))


VERDICTS = ("confirmed", "reversed", "grandparent", "differs", "unverified")


def judge(relation: str, a: str, b: str, entities: dict) -> str:
    """What Wikidata says about a link between two matched entries (one of VERDICTS)."""
    A, B = entities.get(a), entities.get(b)
    if not A or not B:
        return "unverified"

    def among(x: str, ids: list[str]) -> bool:
        return any(same_being(x, i, entities) for i in ids)

    if relation == "parent_of":
        if among(a, B["father"] + B["mother"]) or among(b, A["child"]):
            return "confirmed"
        if among(b, A["father"] + A["mother"]) or among(a, B["child"]):
            return "reversed"
        for parent in B["father"] + B["mother"]:      # a is really a parent of one of b's parents
            P = entities.get(parent)
            if P and among(a, P["father"] + P["mother"]):
                return "grandparent"
        # Wikidata names someone else as that parent. Without knowing whether a is a father or a mother, no verdict.
        named = {"male": B["father"], "female": B["mother"]}.get(A["sex"])
        return "differs" if named and is_individual(a, entities) else "unverified"
    if relation in ("spouse_of", "lover_of"):
        return "confirmed" if among(b, A["spouse"] + A["partner"]) or among(a, B["spouse"] + B["partner"]) else "unverified"
    if relation == "sibling_of":
        shared = any(among(p, B["father"] + B["mother"]) for p in A["father"] + A["mother"])
        return "confirmed" if shared or among(b, A["sibling"]) or among(a, B["sibling"]) else "unverified"
    return "unverified"


def wikidata_parents(qids: set[str], entities: dict) -> set[tuple[str, str]]:
    """Wikidata's parent links between the given entries: {(parent, child)}, both taken from `qids`."""
    links = set()
    for q in qids:
        e = entities.get(q)
        if not e:
            continue
        for parent in e["father"] + e["mother"]:
            links.update((other, q) for other in qids if other != q and same_being(parent, other, entities))
        for child in e["child"]:
            links.update((q, other) for other in qids if other != q and same_being(child, other, entities))
    return links


def apply_namesakes(out_dir: Path, annotations: dict[str, dict]) -> None:
    """Different people who share a name (python -m annotate.namesakes): in the annotations ({section id: result}),
    each section's entry for such a name takes the name of the person it is ("Ptolemy, son of Lagus"), and the shared
    name no longer joins anyone. Changed in place; nothing happens if the book has no namesakes.json."""
    # Splits made by hand (curated/namesakes.json, same layout) are added to the step's own, and survive a re-run of it.
    files = [f for f in (out_dir / "namesakes.json", out_dir.parent / "curated" / "namesakes.json") if f.exists()]
    if not files:
        return
    renamed, shared = {}, set()
    for s in [s for f in files for s in json.loads(f.read_text(encoding="utf-8"))["split"]]:
        for g in s["groups"]:
            for sid, name in g["entries"]:
                renamed[(sid, norm(name))] = g["name"]
                shared.add(norm(name))
    for sid, a in annotations.items():
        new = lambda name, sid=sid: renamed.get((sid, norm(name)), name)
        for c in a["characters"]:
            c["name"] = new(c["name"])
            c["aliases"] = [x for x in c["aliases"] if norm(x) not in shared]
        for r in a["relationships"]:
            r["a"], r["b"] = new(r["a"]), new(r["b"])
        for sc in a.get("scenes", []):          # who is present in each scene, so the reader can link them
            sc["present"] = [new(x) for x in sc.get("present", [])]
        a["alias_notes"] = [{**n, "canonical": new(n["canonical"])} for n in a["alias_notes"] if norm(n["alias"]) not in shared]


def slugify(name: str) -> str:
    return re.sub(r"\s+", "-", norm(name)) or "unnamed"


def merge(slug: str, root: Path = ROOT, review: bool = True, theoi: bool = True, library: bool = True) -> dict:
    out_dir = root / "data" / slug / "output"
    sections = [s for s in json.loads((out_dir / "sections.json").read_text(encoding="utf-8"))["sections"]
                if s["category"] == "body"]
    order = {s["id"]: i for i, s in enumerate(sections)}
    annotations = {}
    for f in (out_dir / "annotations").glob(f"{slug}-body-*.json"):
        d = json.loads(f.read_text(encoding="utf-8"))
        annotations[d["section_id"]] = d["result"]
    ids = sorted(annotations, key=order.get)
    apply_namesakes(out_dir, annotations)

    # 1. Join names that are the same person. Evidence = sections where the model links two names.
    groups = Groups()
    support: Counter = Counter()      # pair of name keys -> sections that link them
    together: Counter = Counter()     # pair of name keys -> sections that list them as two separate characters
    for sid in ids:
        a = annotations[sid]
        linked, primary = set(), set()
        for c in a["characters"]:
            key = norm(c["name"])
            groups.find(key)
            primary.add(key)
            for alias in c["aliases"]:
                if is_proper_name(alias) and norm(alias) != key:
                    linked.add(frozenset((key, norm(alias))))
        for n in a["alias_notes"]:
            if is_proper_name(n["alias"]) and norm(n["canonical"]) and norm(n["alias"]) != norm(n["canonical"]):
                linked.add(frozenset((norm(n["alias"]), norm(n["canonical"]))))
        support.update(linked)
        together.update(frozenset((x, y)) for x in primary for y in primary if x < y)

    outside_file = root / "data" / slug / "curated" / "outside.json"
    outside = json.loads(outside_file.read_text(encoding="utf-8")) if outside_file.exists() else {"people": [], "relationships": []}
    for p in outside["people"]:
        # "Æolus" with the other name "Aeolus" is one spelling twice over, not two names to join.
        if p["other_name"] and is_proper_name(p["name"]) and is_proper_name(p["other_name"]) \
                and norm(p["name"]) != norm(p["other_name"]):
            support[frozenset((norm(p["name"]), norm(p["other_name"])))] += 1
        groups.find(norm(p["name"]))

    curated_file = root / "data" / slug / "curated" / "aliases.json"
    curated = json.loads(curated_file.read_text(encoding="utf-8")) if curated_file.exists() else {}
    forced = [frozenset(map(norm, p)) for p in curated.get("merge", [])]
    never = {frozenset(map(norm, p)) for p in curated.get("separate", [])}
    # The AI review's decisions (python -m annotate.review). A hand correction that says otherwise wins.
    review_file = out_dir / "review.json"
    reviewed = json.loads(review_file.read_text(encoding="utf-8")) if review and review_file.exists() else {}
    by_hand = set(forced)
    for item in reviewed.get("same", []):
        keys = [norm(n) for n in item["names"]]
        forced += [pair for k in keys[1:] if (pair := frozenset((keys[0], k))) not in never and len(pair) == 2]
    never |= {pair for d in reviewed.get("different", [])
              if (pair := frozenset((norm(d["name"]), norm(d["other"])))) not in by_hand and len(pair) == 2}
    forced = [pair for pair in forced if pair in by_hand or pair not in never]

    # Wikidata matches (python -m annotate.verify). Only confident matches count.
    wd_file = out_dir / "wikidata.json"
    wd = json.loads(wd_file.read_text(encoding="utf-8")) if wd_file.exists() else {"names": {}, "entities": {}}
    entities = wd["entities"]
    qid_of = {key: n["qid"] for key, n in wd["names"].items() if n["qid"] and n["confidence"] == "high"}
    # Matches set by hand ("wikidata" in curated/aliases.json: {name: qid}), for a name the Wikidata step did not see,
    # such as one a namesake split made up ("Menoetius, father of Patroclus").
    # A null drops a wrong match (Pausanias' Clymenes are not the Oceanid).
    for name, qid in curated.get("wikidata", {}).items():
        if qid:
            qid_of[norm(name)] = qid
        else:
            qid_of.pop(norm(name), None)

    def blocked(pair) -> bool:
        ra, rb = (groups.find(x) for x in pair)
        for x in groups.members[ra]:
            for y in groups.members[rb]:
                p = frozenset((x, y))
                if p in never or together[p] > support[p]:
                    return True
                if (x in qid_of and y in qid_of and not same_being(qid_of[x], qid_of[y], entities)
                        and is_individual(qid_of[x], entities) and is_individual(qid_of[y], entities)):
                    return True                      # two different beings on Wikidata
        return False

    held_back = []
    for pair in forced:
        groups.union(*sorted(pair))
    matched = sorted(qid_of)
    for i, x in enumerate(matched):               # names that Wikidata says are one being
        for y in matched[i + 1:]:
            if groups.find(x) != groups.find(y) and same_being(qid_of[x], qid_of[y], entities) and not blocked((x, y)):
                groups.union(x, y)
    for pair, n in sorted(support.items(), key=lambda kv: (-kv[1], sorted(kv[0]))):    # same order on every run
        ra, rb = (groups.find(x) for x in pair)
        if ra == rb:
            continue
        if blocked(pair):
            held_back.append((sorted(pair), n))
        else:
            groups.union(*sorted(pair))
    # Names that only appear as someone's alias still need a group.
    for pair in support:
        for x in pair:
            groups.find(x)

    # 2. Collect what is known about each person.
    people: dict[str, dict] = defaultdict(lambda: {
        "names": Counter(), "kinds": Counter(), "epithets": set(), "descriptions": [], "sections": set(), "about": []})
    for sid in ids:
        for c in annotations[sid]["characters"]:
            p = people[groups.find(norm(c["name"]))]
            p["names"][c["name"]] += 1
            p["kinds"][c["kind"]] += 1
            p["sections"].add(sid)
            p["descriptions"].append((order[sid], sid, c["description"]))
            for alias in c["aliases"]:
                if not is_proper_name(alias):
                    p["epithets"].add(alias)
                elif groups.find(norm(alias)) == groups.find(norm(c["name"])):   # only names that really merged
                    p["names"][alias] += 0
        for n in annotations[sid]["alias_notes"]:
            p = people[groups.find(norm(n["canonical"]))]
            if not is_proper_name(n["alias"]):
                p["epithets"].add(n["alias"])
            elif groups.find(norm(n["alias"])) == groups.find(norm(n["canonical"])):
                p["names"][n["alias"]] += 0

    for o in outside["people"]:
        p = people[groups.find(norm(o["name"]))]
        p["names"][o["name"]] += 0
        if o["other_name"] and groups.find(norm(o["other_name"])) == groups.find(norm(o["name"])):
            p["names"][o["other_name"]] += 0
        if o["description"] and o["description"] not in p["about"]:
            p["about"].append(o["description"])

    # An alias note can name a "canonical" form that is not a listed character (a thief, a snail); with nothing to
    # add it would leave an empty entry, so drop those. Relationships that mention them create them again by name.
    for key in [k for k, p in people.items() if not p["names"]]:
        del people[key]

    # 3. Relationships: map each end to a person, creating a node for things like 'a laurel'.
    def person_for(name: str, relation_end: str, relation: str) -> str:
        root_key = groups.find(norm(name))
        if root_key not in people:
            people[root_key]["names"][name] += 0
            people[root_key]["kinds"]["form" if relation == "transformed_into" and relation_end == "b" else "other"] += 1
            unmatched.add(name)
        return root_key

    unmatched: set[str] = set()
    edges: dict[tuple, dict] = {}
    for sid in ids:
        for r in annotations[sid]["relationships"]:
            a = person_for(r["a"], "a", r["relation"])
            b = person_for(r["b"], "b", r["relation"])
            if a == b:
                continue
            if r["relation"] in SYMMETRIC and a > b:
                a, b = b, a
            e = edges.setdefault((a, r["relation"], b), {"source": r["source"], "first": order[sid], "evidence": []})
            if r["source"] == "text":
                e["source"] = "text"
            e["first"] = min(e["first"], order[sid])
            e["evidence"].append({"section": sid, "sentence": r["evidence_sentence"]})

    # Links from beyond the book. A link the book already states stays a book link.
    dropped_outside = 0
    for r in outside["relationships"]:
        if any(w in r["note"].lower() for w in BAD_NOTE_WORDS):
            dropped_outside += 1
            continue
        a = groups.find(norm(r["a"]))
        b = groups.find(norm(r["b"]))
        for key, name in ((a, r["a"]), (b, r["b"])):
            if key not in people:
                people[key]["names"][name] += 0
                people[key]["kinds"]["outside"] += 1
        if a == b:
            continue
        if r["relation"] in SYMMETRIC and a > b:
            a, b = b, a
        # The outside step sometimes files a love affair as a marriage while its own note says otherwise ("Koronis
        # was his lover", "Consort, not formal wife"), or for a pair the book itself calls lovers: those are lovers.
        rel = r["relation"]
        if rel == "spouse_of" and (LOVER_NOTE.search(r["note"]) or (a, "lover_of", b) in edges):
            rel = "lover_of"
        edges.setdefault((a, rel, b), {"source": "outside", "first": None, "evidence": [], "note": r["note"]})

    # Wrong links the reader has marked for removal (spouse/sibling/lover links match in either order). They go first,
    # so a hand addition is never refused, nor a third parent trimmed, because of a link that is about to be removed.
    # The Wikidata step below never puts one back.
    removed, struck = 0, set()
    for a_name, rel, b_name in curated.get("remove", []):
        a, b = groups.find(norm(a_name)), groups.find(norm(b_name))
        for key in {(a, rel, b), (b, rel, a)} if rel in SYMMETRIC else {(a, rel, b)}:
            struck.add(key)
            if key in edges:
                del edges[key]
                removed += 1
    # So do the links the private family check (kept off the repository) shows to be wrong: a parent
    # about to be removed must not take the place of a right one when a third parent is trimmed (Melia for Zeus).
    theoi_file = root / "data" / slug / "curated" / "theoi.json"
    checked = json.loads(theoi_file.read_text(encoding="utf-8")) if theoi and theoi_file.exists() else {}
    for a_name, rel, b_name, *_ in checked.get("remove", []):
        a, b = groups.find(norm(a_name)), groups.find(norm(b_name))
        for key in {(a, rel, b), (b, rel, a)} if rel in SYMMETRIC else {(a, rel, b)}:
            struck.add(key)
            edges.pop(key, None)

    # Missing links the reader has supplied. A link that would make someone their own ancestor is refused.
    added, refused = 0, []

    def is_ancestor(x: str, y: str) -> bool:       # is x already an ancestor of y?
        stack, seen = [y], set()
        while stack:
            node = stack.pop()
            if node == x:
                return True
            if node not in seen:
                seen.add(node)
                stack += [pa for (pa, rel, ch) in edges if rel == "parent_of" and ch == node]
        return False

    # Check the links against Wikidata. A person is matched when at least one of their names has an entry.
    person_qids = {}
    for key in people:
        qids = sorted({qid_of[m] for m in groups.members.get(key, {key}) if m in qid_of})
        if qids:
            person_qids[key] = qids
    owner = {q: key for key, qids in sorted(person_qids.items()) for q in qids}
    wd_report, wd_changed = Counter(), []

    def says(rel: str, a: str, b: str) -> str:
        return min((judge(rel, qa, qb, entities) for qa in person_qids[a] for qb in person_qids[b]), key=VERDICTS.index)

    def someone_between(a: str, b: str) -> bool:      # is the parent in the middle also a person of this book?
        return any(m not in (a, b) and says("parent_of", a, m) == "confirmed" and says("parent_of", m, b) == "confirmed"
                   for m in person_qids)

    for (a, rel, b), e in list(edges.items()):
        if a not in person_qids or b not in person_qids:
            continue
        verdict = says(rel, a, b)
        if verdict == "grandparent" and not someone_between(a, b):
            verdict = "differs"           # perhaps a namesake was matched; without the person in between, do not drop
        text = f"{people[a]['names'].most_common(1)[0][0]} {rel} {people[b]['names'].most_common(1)[0][0]}"
        if verdict == "reversed":                       # no tradition makes a child the parent of its own parent
            del edges[(a, rel, b)]
            edges.setdefault((b, rel, a), {"source": "outside", "first": None, "evidence": [], "note": "Wikidata",
                                           "check": "confirmed"})
            wd_changed.append(f"turned round: {text}")
        elif verdict == "grandparent":                  # a grandparent misread as a parent; the real chain is added below
            del edges[(a, rel, b)]
            wd_changed.append(f"dropped, a grandparent: {text}")
        elif verdict == "differs" and e["source"] == "outside":
            del edges[(a, rel, b)]
            wd_changed.append(f"dropped, not what Wikidata has: {text}")
        elif verdict == "differs":
            e["check"] = "differs"
            wd_changed.append(f"kept (the book says so) but differs from Wikidata: {text}")
        elif verdict == "confirmed":
            e["check"] = "confirmed"
        wd_report[verdict] += 1
    # Fill gaps with Wikidata's parents, but never against the book: a child keeps the parents it has, gets at most
    # two, and gets none if one of its links already differs from Wikidata (a sign that a namesake was matched).
    doubtful = {end for (a, rel, b), e in edges.items() if e.get("check") == "differs" for end in (a, b)}

    def sex(key: str) -> str:
        return next((entities[q]["sex"] for q in person_qids.get(key, []) if entities.get(q, {}).get("sex")), "")

    wd_added = []
    for qa, qb in sorted(wikidata_parents(set(owner), entities)):
        a, b = owner[qa], owner[qb]
        if (a == b or a in doubtful or b in doubtful or (a, "parent_of", b) in edges or (b, "parent_of", a) in edges
                or (a, "parent_of", b) in struck or is_ancestor(b, a)):
            continue
        parents = [pa for (pa, rel, child) in edges if rel == "parent_of" and child == b]
        if len(parents) >= 2 or (sex(a) and any(sex(pa) == sex(a) for pa in parents)):
            continue
        edges[(a, "parent_of", b)] = {"source": "outside", "first": None, "evidence": [], "note": "Wikidata", "check": "confirmed"}
        wd_added.append(f"{people[a]['names'].most_common(1)[0][0]} parent_of {people[b]['names'].most_common(1)[0][0]}")
    wd_report["added"] = len(wd_added)
    for key, qids in person_qids.items():       # what the person is known for, when nothing else says
        known = next((entities[q]["description"] for q in qids if q in entities and entities[q]["description"]), "")
        if known and not people[key]["about"]:
            people[key]["about"].append(known)

    for entry in curated.get("add", []):
        a_name, rel, b_name = entry[:3]
        if rel not in RELATIONS:
            raise ValueError(f"curated add: unknown relation {rel!r} in {entry}; use one of {sorted(RELATIONS)}")
        a, b = groups.find(norm(a_name)), groups.find(norm(b_name))
        for key, name in ((a, a_name), (b, b_name)):
            if key not in people:
                people[key]["names"][name] += 0
                people[key]["kinds"]["outside"] += 1
        if a == b:
            continue
        if rel == "parent_of" and is_ancestor(b, a):
            refused.append(entry[:3])
            continue
        if rel in SYMMETRIC and a > b:
            a, b = b, a
        if (a, rel, b) not in edges:
            edges[(a, rel, b)] = {"source": "outside", "first": None, "evidence": [],
                                  "note": entry[3] if len(entry) > 3 else "added by hand", "hand": True}
            added += 1
    # The family the other books of this library tell. Each person of this book gets the parents, children, spouses
    # and siblings the other books give them (people are matched by Wikidata, as in annotate.library), as outside
    # links naming the books they come from. Only those books' own links count, not ones they took from a third
    # book; only one step out (a relative brought in brings none of their own); and never against this book: no link
    # removed here by hand, no third parent or a second of the same sex, no ancestor loop, and no link kept only for
    # another book's tradition (Hesiod's Oceanid Calypso is not Homer's).
    lib_titles, lib_anchor, lib_people = {}, {}, set()
    all_entities = dict(entities)            # this book's Wikidata entries, and the other books' when they are read
    if library:
        decisions = root / "data" / "theoi_decisions.json"
        kept_for = {(a, b): set(books) for a, b, books in
                    (json.loads(decisions.read_text(encoding="utf-8")).get("keep", []) if decisions.exists() else [])}
        # People are matched by the cross-book index (data/library.json, python -m annotate.library), which joins at
        # most one person per book into each being: Diana is Artemis, not also Selene.
        lib_file = root / "data" / "library.json"
        beings = json.loads(lib_file.read_text(encoding="utf-8")).get("beings", []) if lib_file.exists() else []
        being_of = {(m["book"], m["id"]): i for i, being in enumerate(beings) for m in being["members"]}
        key_of_being = {i: k for i, being in enumerate(beings) for m in being["members"]
                        if m["book"] == slug and (k := groups.find(norm(m["name"]))) in people}
        qid_owner = {q: key for key, qids in sorted(person_qids.items()) for q in qids}
        # Links Claude's review (python -m annotate.judge) found wrong or a namesake in this book are not brought in
        # again (unless the family check confirms them): Antigone, Oedipus' daughter, did not marry Peleus.
        judge_file = root / "data" / "judge.json"
        judged_out = {x["link"] for v in (json.loads(judge_file.read_text(encoding="utf-8")).values() if judge_file.exists() else [])
                      if v["book"] == slug for x in v["links"] if x["fix"] == "remove"}
        shown = lambda key: people[key]["names"].most_common(1)[0][0]
        parents_of = defaultdict(set)
        for (a, rel, b) in edges:
            if rel == "parent_of":
                parents_of[b].add(a)

        def above(x: str, y: str) -> bool:          # is x already an ancestor of y?
            stack, seen = [y], set()
            while stack:
                node = stack.pop()
                if node == x:
                    return True
                if node not in seen:
                    seen.add(node)
                    stack += parents_of.get(node, ())
            return False

        def lib_sex(key: str) -> str:
            return next((all_entities[q]["sex"] for q in person_qids.get(key, []) if all_entities.get(q, {}).get("sex")), "")

        for other in sorted((root / "data").glob("*/output/tree.json")):
            book = other.parent.parent.name
            if book == slug:
                continue
            tree = json.loads(other.read_text(encoding="utf-8"))
            wd_other = other.parent / "wikidata.json"
            their = json.loads(wd_other.read_text(encoding="utf-8")).get("entities", {}) if wd_other.exists() else {}
            all_entities.update(their)
            secs = other.parent / "sections.json"
            lib_titles[book] = (json.loads(secs.read_text(encoding="utf-8")).get("title") if secs.exists() else None) or book
            person = {p["id"]: p for p in tree["people"]}

            def match(p: dict) -> str | None:
                i = being_of.get((book, p["id"]))
                if i is not None and i in key_of_being:
                    return key_of_being[i]
                # Failing the index, the very same Wikidata entry (Athene here, Athena there) is the same person.
                return next((qid_owner[q] for q in p.get("wikidata", []) if q in qid_owner), None)

            def bring(p: dict) -> str | None:
                """This book's person for a relative another book names, or None when a name is all there is to go on:
                a relative with no Wikidata match could be anyone of that name (Pausanias' Thoas is not the son of
                Dionysus), and so could this book's unmatched namesake."""
                key = groups.find(norm(p["name"]))
                mine, theirs = person_qids.get(key, []), p.get("wikidata", [])
                if not theirs or (key in people and not mine):
                    return None
                if key in people and not any(same_being(x, y, all_entities) for x in mine for y in theirs):
                    key = f"{norm(p['name'])} {book}"           # someone else of that name is in this book
                if key not in people:
                    people[key]["names"][p["name"]] += 0
                    people[key]["kinds"][p.get("kind") or "other"] += 1
                    if p.get("about"):
                        people[key]["about"].append(p["about"])
                    lib_people.add(key)
                if p.get("wikidata") and key in lib_people:
                    person_qids[key] = sorted(set(person_qids.get(key, [])) | set(p["wikidata"]))
                    for q in p["wikidata"]:
                        qid_owner.setdefault(q, key)
                if (i := being_of.get((book, p["id"]))) is not None:
                    key_of_being.setdefault(i, key)        # the same being from a later book comes to this one
                return key

            for e in tree["relationships"]:
                rel = e["relation"]
                # Only what that book states in its own text, or what the family check confirms: a guess
                # from Wikidata or the outside step stays in its own book instead of spreading to every other.
                if rel not in ("parent_of", "spouse_of", "sibling_of", "lover_of") or e.get("from_books") \
                        or not (e["source"] == "text" or e.get("confirmed") or e.get("theoi")):
                    continue
                pa, pb = person[e["a"]], person[e["b"]]
                if (pa["name"], pb["name"]) in kept_for and slug not in kept_for[(pa["name"], pb["name"])]:
                    continue
                ka, kb = match(pa), match(pb)
                anchored = [k for k in (ka, kb) if k is not None and people[k]["sections"]]
                if not anchored:
                    continue
                ka, kb = ka or bring(pa), kb or bring(pb)
                if ka is None or kb is None or ka == kb:
                    continue
                a, b = (kb, ka) if rel in SYMMETRIC and ka > kb else (ka, kb)
                if (a, rel, b) in struck or (rel in SYMMETRIC and (b, rel, a) in struck):
                    continue
                if not (e.get("confirmed") or e.get("theoi")) and ({f"{shown(a)} {rel} {shown(b)}", f"{shown(b)} {rel} {shown(a)}"} & judged_out):
                    continue
                if (a, rel, b) in edges:
                    if edges[(a, rel, b)].get("from_books") and book not in edges[(a, rel, b)]["from_books"]:
                        edges[(a, rel, b)]["from_books"].append(book)
                    continue
                if rel == "parent_of":
                    if (b, rel, a) in edges or above(b, a) or b in doubtful or len(parents_of[b]) >= 2 \
                            or (lib_sex(a) and any(lib_sex(x) == lib_sex(a) for x in parents_of[b])):
                        continue
                    parents_of[b].add(a)
                edges[(a, rel, b)] = {"source": "outside", "first": None, "evidence": [], "from_books": [book]}
                for k in (a, b):
                    if k in lib_people:
                        lib_anchor.setdefault(k, anchored[0])
        for e in edges.values():
            if e.get("from_books"):
                e["note"] = "From " + ", ".join(lib_titles[b] for b in e["from_books"])
        for key in lib_people - {end for (a, _, b) in edges for end in (a, b)}:   # brought in, then no link kept
            del people[key]
            person_qids.pop(key, None)
            lib_anchor.pop(key, None)
        lib_people &= set(people)

    # Nobody gets a third parent from outside the book. The outside layer and Wikidata can each name a parent under a
    # different name or from a different tradition (Ops and Cybele for Juno): links the book states always stay, then
    # links added by hand, then those that Wikidata confirms. A parent of a sex the child has none of yet still comes
    # in: where the book names two mothers (Leda and Nemesis for Helen), the father (Zeus) is not a third of a kind.
    def parent_sex(key: str) -> str:
        return next((all_entities[q]["sex"] for q in person_qids.get(key, []) if all_entities.get(q, {}).get("sex")), "")

    trimmed = []
    for child in sorted({b for (a, rel, b) in edges if rel == "parent_of"}):
        parents = [(a, edges[(a, "parent_of", child)]) for (a, rel, b) in edges if rel == "parent_of" and b == child]
        if len(parents) <= 2:
            continue
        extra = sorted((a for a, e in parents if e["source"] == "outside"),
                       key=lambda a: (not edges[(a, "parent_of", child)].get("hand"),
                                      edges[(a, "parent_of", child)].get("check") != "confirmed", a))
        kept = [a for a, e in parents if a not in extra]
        for a in extra:
            s = parent_sex(a)
            if len(kept) < 2 or (s and all(parent_sex(k) and parent_sex(k) != s for k in kept)):
                kept.append(a)
                continue
            del edges[(a, "parent_of", child)]
            trimmed.append(f"{people[a]['names'].most_common(1)[0][0]} parent_of {people[child]['names'].most_common(1)[0][0]}")
    for name, text in curated.get("about", {}).items():
        key = groups.find(norm(name))
        if key not in people:
            people[key]["names"][name] += 0
            people[key]["kinds"]["outside"] += 1
        people[key]["about"].insert(0, text)

    # The private family check (its wrong links were removed above): the parents it cites to this same
    # book are added in place of wrong ones, and the links it confirms carry its citations. Links brought in after
    # the removal (the cross-book step) are removed again here.
    for a_name, rel, b_name, *_ in checked.get("remove", []):
        a, b = groups.find(norm(a_name)), groups.find(norm(b_name))
        for key in {(a, rel, b), (b, rel, a)} if rel in SYMMETRIC else {(a, rel, b)}:
            edges.pop(key, None)
    for a_name, rel, b_name, cite in checked.get("add", []):
        a, b = groups.find(norm(a_name)), groups.find(norm(b_name))
        if a in people and b in people and a != b and (a, rel, b) not in edges:
            edges[(a, rel, b)] = {"source": "outside", "first": None, "evidence": [], "note": f"Ancient source: {cite}"}
    theoi_cites = {}
    for a_name, rel, b_name, cites in checked.get("confirmed", []) + [r[:3] + [[r[3]]] for r in checked.get("add", [])]:
        a, b = groups.find(norm(a_name)), groups.find(norm(b_name))
        theoi_cites[(a, rel, b)] = theoi_cites[(b, rel, a)] = cites
    theoi_places = checked.get("places", {})

    # A being's only marriages (data/only_spouses.json, by Wikidata entry): any other "spouse" of theirs is a lover.
    # Zeus had one wife, Hera; Metis, Leto and the rest were his lovers, whatever a translation or the outside step says.
    only_file = root / "data" / "only_spouses.json"
    only = json.loads(only_file.read_text(encoding="utf-8")).get("spouses", {}) if only_file.exists() else {}

    def being_is(key: str, qid: str) -> bool:
        return any(same_being(x, qid, all_entities) for x in person_qids.get(key, []))

    for (a, rel, b), e in list(edges.items()):
        # (Only against a partner known by Wikidata: an unmatched "Hephæstus" may well be the husband.)
        if rel == "spouse_of" and any((being_is(x, q) and person_qids.get(y) and not any(being_is(y, w) for w in wives))
                                      for x, y in ((a, b), (b, a)) for q, wives in only.items()):
            del edges[(a, rel, b)]
            edges.setdefault((a, "lover_of", b), e)
    # Links the book's sentence does not really state but tradition does ("tradition" in curated/aliases.json, from
    # the family-tree review): kept, but as known from tradition, so the reader shows them greyed.
    for a_name, rel, b_name, *note in curated.get("tradition", []):
        a, b = groups.find(norm(a_name)), groups.find(norm(b_name))
        for key in {(a, rel, b), (b, rel, a)} if rel in SYMMETRIC else {(a, rel, b)}:
            if key in edges and edges[key]["source"] == "text":
                edges[key]["source"] = "outside"
                edges[key]["note"] = note[0] if note else "Known from tradition; not stated in this book"
    # A wife is not also listed as a lover: Ariadne, whom Dionysus made his wife in Hesiod, and his love in Ovid.
    for (a, rel, b) in [k for k in edges if k[1] == "lover_of"]:
        if (a, "spouse_of", b) in edges or (b, "spouse_of", a) in edges:
            del edges[(a, rel, b)]

    # 4. When does each person first appear? Outside-only people appear with the earliest relative who does.
    first_of: dict[str, int | None] = {}
    for key, p in people.items():
        first_of[key] = min((d[0] for d in p["descriptions"]), default=None)
    for (a, rel, b), e in edges.items():          # book people who are only named inside a relationship
        for end in (a, b):
            if first_of.get(end) is None and e["first"] is not None and not people[end]["about"]:
                first_of[end] = e["first"]
    changed = True
    while changed:
        changed = False
        for (a, rel, b) in edges:
            # A parent appears with their earliest child, and spouses with each other; never because of an
            # ancestor, or one early person like Earth would bring every Titan on screen in the first fable.
            for x, y in (((a, b),) if rel == "parent_of" else ((a, b), (b, a)) if rel in SYMMETRIC else ()):
                if first_of[y] is not None and (first_of[x] is None or first_of[y] < first_of[x]) \
                        and not people[x]["sections"]:
                    first_of[x] = first_of[y]
                    changed = True
    for key, anchor in lib_anchor.items():          # a relative from another book appears with the one they belong to
        if first_of.get(key) is None:
            first_of[key] = first_of.get(anchor)

    result_people, id_of, used = [], {}, Counter()
    for key, p in sorted(people.items(), key=lambda kv: (-sum(kv[1]["names"].values()), kv[0])):
        name = p["names"].most_common(1)[0][0]
        pid = slugify(name)
        used[pid] += 1
        if used[pid] > 1:
            pid = f"{pid}-{used[pid]}"
        id_of[key] = pid
        kinds = Counter({k: v for k, v in p["kinds"].items() if k != "other"}) or p["kinds"]
        first = first_of[key]
        descriptions, seen = [], set()
        for idx, sid, text in sorted(p["descriptions"]):
            if text and text not in seen:
                seen.add(text)
                descriptions.append({"section": sid, "text": text})
        result_people.append({
            "id": pid, "name": name, "kind": kinds.most_common(1)[0][0] if kinds else "other",
            "in_book": bool(p["sections"]), "about": p["about"][0] if p["about"] else "",
            "aliases": sorted(n for n in p["names"] if n != name), "epithets": sorted(p["epithets"]),
            "first_index": first, "first_section": sections[first]["id"] if first is not None else None,
            "section_count": len(p["sections"]), "descriptions": descriptions,
            "wikidata": person_qids.get(key, []), "from_library": key in lib_people})
    result_edges = []
    for (a, rel, b), e in edges.items():
        first = e["first"]
        if first is None:      # an outside link shows once both of its people have been reached
            ends = [first_of[a], first_of[b]]
            first = None if None in ends else max(ends)
        result_edges.append({"a": id_of[a], "relation": rel, "b": id_of[b], "source": e["source"], "note": e.get("note", ""),
                             "first_index": first, "first_section": sections[first]["id"] if first is not None else None,
                             "evidence": e["evidence"], "check": e.get("check", ""),
                             # Only whether the private check confirmed the link; its sources stay off the repository.
                             "confirmed": (a, rel, b) in theoi_cites, "from_books": e.get("from_books")})
    result_edges.sort(key=lambda e: (e["first_index"] is None, e["first_index"] or 0))
    raw = sum(len(a["characters"]) for a in annotations.values())
    return {"slug": slug, "sections": [s["id"] for s in sections], "people": result_people,
            "relationships": result_edges,
            "report": {"annotated_sections": len(ids), "raw_character_entries": raw, "people": len(result_people),
                       "relationships": len(result_edges), "unmatched_relationship_names": sorted(unmatched),
                       "outside_links": sum(1 for e in result_edges if e["source"] == "outside"),
                       "outside_links_dropped_as_invalid": dropped_outside,
                       "links_removed_by_curation": removed, "third_parents_dropped": trimmed,
                       "links_added_by_curation": added, "added_links_refused": refused,
                       "library_links_added": sum(1 for e in result_edges if e["from_books"]),
                       "outside_only_people": sum(1 for p in result_people if not p["in_book"]),
                       "never_shown": sum(1 for p in result_people if p["first_index"] is None),
                       "merges_held_back": [{"names": n, "sections": c} for n, c in held_back[:40]],
                       "review": {"joined": len(reviewed.get("same", [])), "kept_apart": len(reviewed.get("different", []))},
                       "wikidata": {"people_matched": len(person_qids), **dict(wd_report), "changes": wd_changed,
                                    "added_links": wd_added}}}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug")
    ap.add_argument("--out", help="where to write tree.json (default: data/<slug>/output/tree.json)")
    args = ap.parse_args()
    tree = merge(args.slug)
    out = Path(args.out) if args.out else ROOT / "data" / args.slug / "output" / "tree.json"
    out.write_text(json.dumps(tree, ensure_ascii=False, indent=1), encoding="utf-8")
    r = tree["report"]
    print(f"{r['raw_character_entries']} character entries -> {r['people']} people, {r['relationships']} relationships")
    print(f"saved to {out}")


if __name__ == "__main__":
    main()
