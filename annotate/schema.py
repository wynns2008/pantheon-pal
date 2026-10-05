"""What we ask Claude for each section, and how we check what comes back.

Two passes per section. The model sees the text as numbered sentences (from litparse.sentences) and answers in
those numbers, so every note can be shown on the sentence it belongs to.

  1. Outline: one request for the whole section: summary, scenes (place, stakes), characters, relationships,
     themes, legacy.
  2. Notes: one request per scene-sized chunk: definitions, background and literary devices on single sentences.
     A long section gets as much attention per sentence as a short one, which a single request does not give.
"""
from litparse.sentences import Sentence

MODEL = "claude-sonnet-5-5"
MAX_TOKENS = 16000
EFFORT = "low"          # extraction work; higher effort mostly adds cost
SCHEMA_VERSION = 6      # 6: two passes; added summary, scenes and legacy; dropped the spoiler flags
CHUNK_MIN, CHUNK_MAX = 12, 60   # sentences per notes request: smaller scenes are joined, larger ones are cut

_INTRO = """You annotate a public-domain English book for a reading companion. The book may be a translation of
a classical text, a novel, a play or anything else."""

_NAMES = """NAMES
- Spell every name exactly as this translation spells it. If the text says Ulysses, Jove and Minerva, write Ulysses,
  Jove and Minerva, never Odysseus, Zeus or Athene."""

SYSTEM_PROMPT = (_INTRO + """ You are given the book's title and one section of it as
numbered sentences. Return JSON that matches the schema. Rules:

""" + _NAMES + """ A name from another language or tradition belongs only in `alias_notes`.

SUMMARY AND SCENES
- `summary`: what happens in this section, in two or three plain sentences.
- `scenes`: cut the section into its scenes, in order, so that together they cover every sentence from the first
  to the last. Start a new scene when the place changes, when the action jumps in time, or when a
  story-within-the-story begins or ends. A short section may be one scene; a long one is usually three to ten.
  For each scene:
  - `start_sentence` and `end_sentence` are its first and last sentence numbers.
  - `title` is a short label (at most 6 words). `summary` is one or two sentences.
  - `stakes`: what the main character of the scene stands to lose or is in danger from, in at most 25 words;
    an empty string if nothing is at stake.
  - `present`: names of the characters who take part, spelled as in your `characters` list.
  - `told_as_story` is true when the scene is told by a character about the past or about elsewhere (a flashback,
    a tale told at a feast), rather than happening in the story's present.
  - `place` is where the action of the scene happens; for a told story, where the told events happen, not
    where the teller sits. `name` is the place as the text calls it. `certainty` is "real" (a known place),
    "traditional" (a legendary place that ancient or modern tradition identifies with a real one, even loosely,
    such as the land of the Cyclopes with Sicily or Scheria with Corfu), "mythical" (no earthly location at all,
    such as the underworld or the home of the gods) or "unknown". For "real" and "traditional" places give
    `modern_name` (the place it is identified with today) and `lat` and `lon` in decimal degrees. For the others,
    or when you are not confident, give an empty `modern_name` and 0 and 0.

CHARACTERS AND FAMILY TREE
- List every named character (a person, god, nymph, monster, animal or other being) who appears in this
  section. `name` is the name as spelled in the text. `aliases` are other names the text itself uses for the same
  person in this section.
- A natural thing (the sun, the moon, the sea, a river, the dawn) is a character only when the text treats it as a
  being that acts or speaks. Never list such a thing as an alias of a god, or a god as an alias of it, unless this
  section itself uses the two names for one being. Objects, ships and places are not characters.
- `description` is one short phrase saying who they are, using only what this section shows.
- `relationships` link two characters. Use these relations: parent_of (A is a parent of B), spouse_of,
  sibling_of, lover_of, transformed_into (A becomes B; B may be a creature or thing, not a character).
  Always write parent relations as parent_of, never child_of. `evidence_sentence` is the sentence that states it.
  A title or figure of speech is not a relationship ("father of victory", "son of the soil", "mother of all").
- `source` is "text" if this section states or clearly implies the relationship, and "outside" if it is
  well-known from beyond the text (other myths, history, the author's other works) but NOT stated here. Never
  put an outside relationship under "text".
- `alias_notes` explain different names for the same person: a title, nickname, disguise or alias, a patronymic,
  or a name in another language (a Roman and a Greek name for a god, say). Include one only when the section
  uses such a name, and never list a name as an alias of itself.

THEMES AND QUOTES
- `analysis`: 1 to 3 items about the section as a whole, one or two sentences each. `kind` is "theme" (what the
  passage is about and how it fits the book) or "historical" (the historical, social or cultural significance of
  the passage or its setting, for example attitudes of the time, politics, or the writer's circumstances).
- `notable_quotes`: sentences in this section that are famous, widely quoted, or especially memorable or revealing.
  Choose at most 3, and only when a sentence is truly notable. `sentence` is its number and `reason` says in at
  most 20 words why it matters.

LEGACY
- `legacy`: 0 to 4 items on what this section's events, characters or phrases led to later, one or two sentences
  each. `kind` is "history" (influence on later history, thought, language, art or literature: a word or saying
  that comes from it, a later author or movement that built on it) or "popular_culture" (specific modern works
  that retell or draw on this episode: films, novels, television, games, music, brands). Name the work and its
  year or decade. Include only works you are sure exist and sure draw on this episode; a few certain items are
  better than many doubtful ones, and none is fine.
- `fun_facts`: 0 to 2 interesting facts about this section's sources or real-world background. Keep them different
  from the `analysis` and `legacy` items.

Write plainly. Do not invent facts; omit anything you are not confident about.""").strip()

NOTES_PROMPT = (_INTRO + """ You are given the book's title, a section, a short description of one scene, and that
scene as numbered sentences. Write the notes a reader sees when they select a sentence. Return JSON that matches
the schema. Rules:

""" + _NAMES + """ You may add the better-known name in brackets in a note, for example "Jove (the Greek Zeus)".

- `language_notes`: odd, archaic or ambiguous phrasing, an unfamiliar word, or a sentence that alludes to something
  the reader may not know (a custom, a place, an earlier event, a historical or literary reference). `kind` is
  "word", "phrasing" or "allusion". Explain the meaning briefly.
- `context_notes`: short background that helps the reader (who someone is, what a custom, place or object was,
  what the gods named here are known for).
- `literary_notes`: a literary device or technique used in a sentence, for example simile, metaphor, irony, dramatic
  irony, foreshadowing, personification, hyperbole, symbolism, apostrophe, motif, epithet, characterization or
  narrative voice. `device` is its name; `text` explains how it works in this sentence. Do not name the same
  device twice in a row without reason.
- Each note refers to one sentence by its number, is at most 30 words, and does not restate the sentence.
- How many: across `language_notes` and `context_notes` together, about one sentence in five; for
  `literary_notes`, about one sentence in ten. Fewer if the passage is plain, and never filler.

Write plainly. Do not invent facts; omit anything you are not confident about.""").strip()

_STR = {"type": "string"}
_BOOL = {"type": "boolean"}
_INT = {"type": "integer"}
_NUM = {"type": "number"}


def _obj(**props) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


OUTLINE_SCHEMA = _obj(
    summary=_STR,
    scenes={"type": "array", "items": _obj(
        start_sentence=_INT,
        end_sentence=_INT,
        title=_STR,
        summary=_STR,
        stakes=_STR,
        present={"type": "array", "items": _STR},
        told_as_story=_BOOL,
        place=_obj(name=_STR, modern_name=_STR,
                   certainty={"type": "string", "enum": ["real", "traditional", "mythical", "unknown"]},
                   lat=_NUM, lon=_NUM))},
    characters={"type": "array", "items": _obj(
        name=_STR,
        aliases={"type": "array", "items": _STR},
        kind={"type": "string", "enum": ["god", "goddess", "mortal", "nymph", "monster", "creature", "place", "other"]},
        description=_STR)},
    relationships={"type": "array", "items": _obj(
        a=_STR,
        relation={"type": "string", "enum": ["parent_of", "spouse_of", "sibling_of", "lover_of", "transformed_into"]},
        b=_STR,
        source={"type": "string", "enum": ["text", "outside"]},
        evidence_sentence=_INT)},
    alias_notes={"type": "array", "items": _obj(alias=_STR, canonical=_STR, explanation=_STR)},
    notable_quotes={"type": "array", "items": _obj(sentence=_INT, reason=_STR)},
    analysis={"type": "array", "items": _obj(kind={"type": "string", "enum": ["theme", "historical"]}, text=_STR)},
    legacy={"type": "array", "items": _obj(kind={"type": "string", "enum": ["history", "popular_culture"]}, text=_STR)},
    fun_facts={"type": "array", "items": _obj(text=_STR)},
)

NOTES_SCHEMA = _obj(
    language_notes={"type": "array", "items": _obj(sentence=_INT, kind={"type": "string", "enum": ["phrasing", "allusion", "word"]},
                                                    text=_STR)},
    context_notes={"type": "array", "items": _obj(sentence=_INT, text=_STR)},
    literary_notes={"type": "array", "items": _obj(sentence=_INT, device=_STR, text=_STR)},
)

# Every field of a finished annotation, in the order it is saved.
RESULT_FIELDS = list(OUTLINE_SCHEMA["properties"]) + list(NOTES_SCHEMA["properties"])
_NOTE_FIELDS = ("language_notes", "context_notes", "literary_notes")


def _head(title: str, reference: str | None, work: str | None) -> str:
    return (f"Book: {work}\n" if work else "") + f"Section: {title}" + (f" (lines {reference})" if reference else "")


def user_message(title: str, reference: str | None, sentences: list[Sentence], work: str | None = None) -> str:
    return _head(title, reference, work) + "\n\n" + "\n".join(f"[{s.i}] {s.text}" for s in sentences)


def _params(system: str, content: str, schema: dict) -> dict:
    return {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": content}],
        "output_config": {"effort": EFFORT, "format": {"type": "json_schema", "schema": schema}},
    }


def request_params(title: str, reference: str | None, sentences: list[Sentence], work: str | None = None) -> dict:
    """Pass 1: the outline request for a whole section."""
    return _params(SYSTEM_PROMPT, user_message(title, reference, sentences, work), OUTLINE_SCHEMA)


def notes_params(title: str, reference: str | None, sentences: list[Sentence], work: str | None, chunk: dict) -> dict:
    """Pass 2: the notes request for one chunk (see `chunks`). Sentences keep their section-wide numbers."""
    part = [s for s in sentences if chunk["start"] <= s.i <= chunk["end"]]
    content = (_head(title, reference, work) + f"\nScene: {chunk['about']}\n\n"
               + "\n".join(f"[{s.i}] {s.text}" for s in part))
    return _params(NOTES_PROMPT, content, NOTES_SCHEMA)


def chunks(scenes: list[dict], n_sentences: int) -> list[dict]:
    """Cut a section into pieces for the notes pass, following its scenes: [{start, end, about}].

    Scenes shorter than CHUNK_MIN sentences are joined to the next one; anything longer than CHUNK_MAX is cut evenly.
    """
    if n_sentences <= 0:
        return []
    spans = [[s["start_sentence"], s["end_sentence"], [s["title"] + ": " + s["summary"]]] for s in scenes] \
        or [[0, n_sentences - 1, [""]]]
    spans[0][0], spans[-1][1] = 0, n_sentences - 1          # cover the whole section even if the scenes do not
    for a, b in zip(spans, spans[1:]):
        b[0] = a[1] + 1
    joined = []
    for span in spans:
        if joined and joined[-1][1] - joined[-1][0] + 1 < CHUNK_MIN:
            joined[-1][1] = span[1]
            joined[-1][2] += span[2]
        else:
            joined.append(span)
    if len(joined) > 1 and joined[-1][1] - joined[-1][0] + 1 < CHUNK_MIN:
        last = joined.pop()
        joined[-1][1] = last[1]
        joined[-1][2] += last[2]
    out = []
    for start, end, about in joined:
        size = end - start + 1
        parts = -(-size // CHUNK_MAX)
        step = -(-size // parts)
        for a in range(start, end + 1, step):
            out.append({"start": a, "end": min(a + step - 1, end), "about": " ".join(about).strip()})
    return out


def _clean_scenes(scenes: list[dict], n_sentences: int) -> list[dict]:
    """Keep scenes that point at real sentences, in order and without overlap; drop impossible coordinates."""
    kept, last_end = [], -1
    for sc in sorted(scenes, key=lambda s: s["start_sentence"]):
        start, end = max(sc["start_sentence"], last_end + 1, 0), min(sc["end_sentence"], n_sentences - 1)
        if start > end:
            continue
        place = dict(sc["place"])
        lat, lon = place["lat"], place["lon"]
        # 0, 0 is the model's "no coordinates"; so is anything outside the globe or on a place with no earthly location.
        if (lat == 0 and lon == 0) or not (-90 <= lat <= 90 and -180 <= lon <= 180) \
                or place["certainty"] in ("mythical", "unknown"):
            place["lat"] = place["lon"] = None
        place["name"], place["modern_name"] = place["name"].strip(), place["modern_name"].strip()
        kept.append({**sc, "start_sentence": start, "end_sentence": end, "place": place,
                     "title": sc["title"].strip(), "summary": sc["summary"].strip(), "stakes": sc["stakes"].strip(),
                     "present": [p.strip() for p in sc["present"] if p.strip()]})
        last_end = end
    return kept


def clean_result(result: dict, n_sentences: int) -> dict:
    """Check the outline (pass 1): drop what points at a sentence that does not exist, trim text, remove duplicates.

    The note fields come back empty; `add_notes` fills them from pass 2.
    """
    def ok(item):
        return 0 <= item["sentence"] < n_sentences

    cleaned = {k: result.get(k, "" if k == "summary" else []) for k in RESULT_FIELDS}
    cleaned["summary"] = cleaned["summary"].strip()
    cleaned["scenes"] = _clean_scenes(cleaned["scenes"], n_sentences)
    cleaned["notable_quotes"] = [n for n in cleaned["notable_quotes"] if ok(n)]
    cleaned["relationships"] = [r for r in cleaned["relationships"]
                                if r["a"].strip() and r["b"].strip() and 0 <= r["evidence_sentence"] < n_sentences]
    seen, characters = set(), []
    for c in cleaned["characters"]:
        key = c["name"].strip().lower()
        if key and key not in seen:
            seen.add(key)
            characters.append(c)
    cleaned["characters"] = characters
    # An alias note must explain a different name; the model sometimes writes "Ceres = Ceres".
    seen_aliases, alias_notes = set(), []
    for a in cleaned["alias_notes"]:
        key = a["alias"].strip().lower()
        if key and key != a["canonical"].strip().lower() and key not in seen_aliases:
            seen_aliases.add(key)
            alias_notes.append(a)
    cleaned["alias_notes"] = alias_notes
    for key in ("analysis", "legacy", "fun_facts"):
        for item in cleaned[key]:
            item["text"] = item["text"].strip()
    for item in cleaned["notable_quotes"]:
        item["reason"] = item["reason"].strip()
    # The reader no longer hides spoilers, so the model is not asked to flag them; older code still expects the field.
    for key in ("notable_quotes", "analysis", "fun_facts"):
        for item in cleaned[key]:
            item.setdefault("spoiler", False)
    return cleaned


def add_notes(cleaned: dict, notes: dict, chunk: dict) -> None:
    """Add one chunk's notes (pass 2) to a cleaned outline, keeping only notes on that chunk's own sentences."""
    for key in _NOTE_FIELDS:
        for item in notes.get(key, []):
            if chunk["start"] <= item["sentence"] <= chunk["end"] and item["text"].strip():
                cleaned[key].append({**item, "text": item["text"].strip(), "spoiler": False})
        cleaned[key].sort(key=lambda n: n["sentence"])
