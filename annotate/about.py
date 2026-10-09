"""Fetch sourced background for a book from Wikipedia: summaries, themes and history, with links.

    python -m annotate.about <slug>          show which Wikipedia articles would be used; spends nothing
    python -m annotate.about <slug> --yes    fetch them and save data/<slug>/output/about.json

Claude (Haiku) is only asked which Wikipedia article is about each work of the book ("The Æneid of Virgil
translated into English prose" is the article "Aeneid"); that costs a fraction of a cent. Everything shown to
the reader is then Wikipedia's own text, quoted and linked:
  - summaries: sections of the article headed by book numbers ("Book 6: Underworld", "Books 1-4") and lines of
    its synopsis that begin "Book IV -" are matched to the book's own sections by number. Where the synopsis is
    plain paragraphs, Claude is asked which sections each paragraph covers (the paragraph itself stays as
    Wikipedia wrote it);
  - themes: sections about themes, motifs, style or interpretation;
  - history: sections about origins, composition, influence, reception or legacy;
  - sources, for the "About this book" page: primary (how the text itself survives: manuscripts, papyri,
    textual transmission), secondary (what the work drew on, scholarship and translations) and tertiary
    (retellings and adaptations: literature, film, television, opera, music).
It also records the Wikipedia page of every character that the Wikidata check (annotate.verify) matched, with the
opening sentences of that page as the character's description (shown with a "Read more" link). `--characters`
refreshes only those, for free: no request is sent to Claude. Wikipedia's text is licensed CC BY-SA 4.0; the reader names it as the source and links to it.
"""
import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from annotate.run import ROOT, get_client

MODEL = "claude-haiku-4-5"
WIKIPEDIA = "https://en.wikipedia.org/w/api.php"
SPARQL = "https://query.wikidata.org/sparql"
USER_AGENT = "PantheonPal/0.1 (personal reading companion; Python urllib)"
PAUSE = 1.5                 # seconds between requests; Wikipedia answers "429 Too Many Requests" to anything faster
LEAD_SENTENCES = 2         # sentences of a character's article shown as their description
# Brackets left empty where Wikipedia's text had a pronunciation the plain-text extract drops: "Aphrodite ( ) is"
EMPTY_BRACKETS = re.compile(r"\s*\(\s*(?:or\s*)?[,;]?\s*\)")
# What is left of a bracket once Wikipedia's pronunciation is cut out: "Bacchus ( or ; Ancient Greek: ...)".
BRACKET_LEFTOVER = re.compile(r"\(\s*(?:or\s*)?[,;]\s*")


def tidy(lead: str) -> str:
    return " ".join(BRACKET_LEFTOVER.sub("(", EMPTY_BRACKETS.sub("", lead)).split())
EXCERPT = 900               # characters kept from each section, cut at the end of a sentence
NUMBERED = re.compile(r"^(?:Book|Chapter|Part|Canto|Act|Volume|Letter|Stave|Section|Fable)s?\s+\d", re.I)
BOOKS = re.compile(r"\bbooks?\s+(\d+)(?:\s*(?:[-–—]|to|and)\s*(\d+))?", re.I)
THEMES = re.compile(r"theme|motif|interpretation|analysis|style|structure", re.I)
HISTORY = re.compile(r"influence|legacy|reception|histor|context|composition|background|origin|dat(?:e|ing)|sources", re.I)
SYNOPSIS = re.compile(r"synopsis|plot|story|summary|contents", re.I)
PRIMARY = re.compile(r"manuscript|papyr|textual|transmission|tablet|codex|scroll|inscription", re.I)
SECONDARY = re.compile(r"sources|models|influences\b|scholarship|commentar|criticism|translation", re.I)
TERTIARY = re.compile(r"adaptation|film|television|opera|music|game|popular culture|theat|stage|ballet|comics|painting"
                      r"|literature|\barts?\b", re.I)
# Never shown: the article's apparatus (lists of editions, references and links).
SKIP = re.compile(r"edition|reference|notes|see also|bibliograph|external|further reading|works cited"
                  r"|citation|other resources|illustration", re.I)

SYSTEM_PROMPT = """You are given a book from Project Gutenberg (title and author) and the works it contains. For each
work give the exact title of the English Wikipedia article about that work (the poem, play or story itself, not
its author and not a translation of it). When a short work has no article of its own but belongs to a collection
that has one (one hymn of the Homeric Hymns), give the collection's article. Give an empty string when there is no
such article, or when the name is a fragment, a heading or a placeholder rather than a work. Return one answer
for every work, with `n` its number.""".strip()
CHECK_PROMPT = """You are given a book and pairs of a work in that book and the opening of a Wikipedia article that was
looked up for it. An article title can lead somewhere unexpected, so for each pair say whether the article really
is about that work, or about the collection the work belongs to (`about` true), or about something else, such as a
different work of a similar name, a person, or a modern book (`about` false). Return one answer for every pair,
with `n` its number.""".strip()
CHECK_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["pairs"], "properties": {"pairs": {
    "type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["n", "about"],
                               "properties": {"n": {"type": "integer"}, "about": {"type": "boolean"}}}}}}
LABEL = re.compile(r"^\s*(Source:\s*|(Main article|See also|Further information)s?:[^.\n]*[.]?\s*)", re.I)
BOOK_LINE = re.compile(r"^Book\s+([IVXLC]+|\d+)\s*[\u2013\u2014-]\s*(.+)$")
ALIGN_PROMPT = """You are given the paragraphs of a plot synopsis of a work, and the sections of an edition of that work
in reading order, each with a short description. For each paragraph give the first and the last section whose
events it tells (`first` and `last`, the sections' numbers). A paragraph that tells no part of the story gets 0
and 0. A paragraph may cover one section or several; neighbouring paragraphs may share a section. Return one
answer for every paragraph, with `n` its number.""".strip()
ALIGN_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["paragraphs"], "properties": {"paragraphs": {
    "type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["n", "first", "last"],
                               "properties": {"n": {"type": "integer"}, "first": {"type": "integer"},
                                              "last": {"type": "integer"}}}}}}
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["works"], "properties": {"works": {
    "type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["n", "article"],
                               "properties": {"n": {"type": "integer"}, "article": {"type": "string"}}}}}}


def fetch(url: str, data: bytes | None = None, accept: str = "application/json") -> dict:
    request = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, "Accept": accept})
    for attempt in range(5):
        try:
            answer = json.loads(urllib.request.urlopen(request, timeout=60).read().decode("utf-8"))
            time.sleep(PAUSE)
            return answer
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 4:
                raise
            time.sleep(int(e.headers.get("Retry-After") or 0) or 20 * (attempt + 1))
    return {}


def works_of(book: dict) -> dict[str, list[str]]:
    """The works a book contains: {work name: [section ids]}. A book divided into numbered books or chapters is one work."""
    body = [s for s in book["sections"] if s["category"] == "body"]
    if all(NUMBERED.match(s["path"][0]) for s in body):
        return {book.get("title") or "": [s["id"] for s in body]}
    works: dict[str, list[str]] = {}
    for s in body:
        works.setdefault(s["path"][0], []).append(s["id"])
    return works


def excerpt(text: str) -> str:
    text = " ".join(text.split())
    if len(text) <= EXCERPT:
        return text
    cut = text[:EXCERPT]
    end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
    return cut[:end + 1] if end > EXCERPT // 3 else cut.rsplit(" ", 1)[0] + "…"


def article(title: str) -> dict | None:
    """One Wikipedia article cut into the parts the reader shows. None if there is no such article."""
    page = fetch(WIKIPEDIA + "?" + urllib.parse.urlencode({
        "action": "query", "prop": "extracts|pageprops", "explaintext": "1", "exsectionformat": "wiki", "redirects": "1",
        "titles": title, "format": "json", "formatversion": "2"}))["query"]["pages"][0]
    if page.get("missing") or "disambiguation" in page.get("pageprops", {}) or not page.get("extract"):
        return None
    if re.search(r"\bmay (also )?refer to\b", page["extract"][:600]):     # a list of people of one name ("Apollodorus")
        return None
    name = page["title"]
    url = "https://en.wikipedia.org/wiki/" + urllib.parse.quote(name.replace(" ", "_"))
    parts = re.split(r"^(==+)\s*(.+?)\s*==+\s*$", page["extract"], flags=re.M)      # [lead, level, heading, text, ...]
    # The opening sentence usually carries a bracket of pronunciations and foreign spellings; leave it out.
    lead = re.sub(r"\s*\((?=[^()]*(?:;|[^\x00-\x7f]))(?:[^()]|\([^()]*\))*\)", "", parts[0], count=1)
    found = {"title": name, "url": url, "lead": excerpt(tidy(lead)), "synopsis": [], "summaries": [], "themes": [], "history": [],
             "primary": [], "secondary": [], "tertiary": [], "paragraphs": []}
    above: list[str] = []                 # the headings this one sits under, outermost first
    for level, heading, text in zip(parts[1::3], parts[2::3], parts[3::3]):
        above = above[:len(level) - 2] + [heading]
        # A section can open with the stub of a citation or a pointer to another article ("Source:", "Main article: ...").
        text = LABEL.sub("", text)
        whole = text
        text = excerpt(text)
        if not text or any(SKIP.search(h) for h in above):
            continue
        item = {"heading": heading, "text": text, "url": url + "#" + urllib.parse.quote(heading.replace(" ", "_"))}
        numbers = BOOKS.search(heading)
        if numbers:                       # "Book 6: Underworld", "Books 1-4"
            first, last = int(numbers.group(1)), int(numbers.group(2) or numbers.group(1))
            found["summaries"].append({**item, "books": list(range(first, last + 1))})
            continue
        for kind, pattern in (("primary", PRIMARY), ("tertiary", TERTIARY), ("secondary", SECONDARY),
                              ("themes", THEMES), ("history", HISTORY), ("synopsis", SYNOPSIS)):
            if any(pattern.search(h) for h in reversed(above)):      # a sub-section belongs where its parent does
                found[kind].append(item)
                if kind == "synopsis":        # keep the whole synopsis, paragraph by paragraph
                    for paragraph in (" ".join(p.split()) for p in whole.split("\n") if p.strip()):
                        line = BOOK_LINE.match(paragraph)
                        if line and not line.group(2).startswith("Book"):     # "Book IV - The daughters of Minyas, ..."
                            found["summaries"].append({"heading": heading, "text": line.group(2), "url": item["url"],
                                                       "books": [roman(line.group(1))]})
                        elif len(paragraph) > 80:
                            found["paragraphs"].append({"text": paragraph, "url": item["url"]})
                break
    return found


def search_articles(query: str, skip: set[str], limit: int = 3) -> list[dict]:
    """The first few articles a Wikipedia search finds, leaving out the titles in skip."""
    hits = fetch(WIKIPEDIA + "?" + urllib.parse.urlencode({
        "action": "query", "list": "search", "srsearch": query, "srlimit": "8", "format": "json"}))
    found = []
    for hit in (h["title"] for h in hits.get("query", {}).get("search", [])):
        if hit not in skip and (a := article(hit)) and a["title"] not in skip:
            found.append(a)
            if len(found) == limit:
                break
    return found


def authorship(title: str) -> tuple[str, str] | None:
    """The part of a work's article on who wrote it ("Authorship"), for an author with no page of their own: (heading, text)."""
    text = fetch(WIKIPEDIA + "?" + urllib.parse.urlencode({
        "action": "query", "prop": "extracts", "explaintext": "1", "exsectionformat": "wiki", "redirects": "1",
        "titles": title, "format": "json", "formatversion": "2"}))["query"]["pages"][0].get("extract", "")
    m = re.search(r"^==\s*(Authorship|Author|Attribution)\s*==\s*$(.*?)(?=^==[^=]|\Z)", text, re.M | re.S)
    return (m.group(1), " ".join(m.group(2).split())) if m else None


def roman(number: str) -> int:
    if number.isdigit():
        return int(number)
    values, total = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}, 0
    for a, b in zip(number, number[1:] + " "):
        total += -values[a] if b != " " and values[a] < values[b] else values[a]
    return total


def align(client, work: dict, sections: list[dict]) -> None:
    """Tie each paragraph of a synopsis to the sections it covers, and add it to the work's summaries."""
    listing = "\n".join(f"P{n}. {p['text']}" for n, p in enumerate(work["paragraphs"], 1))
    listing += "\n\nSECTIONS\n" + "\n".join(f"{n}. {s['title']}" + (f": {s['about']}" if s["about"] else "")
                                           for n, s in enumerate(sections, 1))
    r = client.messages.create(model=MODEL, max_tokens=4000, system=ALIGN_PROMPT,
                               messages=[{"role": "user", "content": "PARAGRAPHS\n" + listing}],
                               output_config={"format": {"type": "json_schema", "schema": ALIGN_SCHEMA}})
    for a in json.loads(next(b.text for b in r.content if b.type == "text"))["paragraphs"]:
        if 1 <= a["n"] <= len(work["paragraphs"]) and 1 <= a["first"] <= a["last"] <= len(sections):
            p = work["paragraphs"][a["n"] - 1]
            work["summaries"].append({"heading": "Synopsis", "text": p["text"], "url": p["url"], "books": [],
                                      "sections": [s["id"] for s in sections[a["first"] - 1:a["last"]]]})


def character_pages(slug: str) -> dict[str, dict]:
    """The Wikipedia page of every Wikidata entry the book's characters were matched to, with the opening sentences of
    the page: {qid: {label, description, url, lead}}."""
    file = ROOT / "data" / slug / "output" / "wikidata.json"
    if not file.exists():
        return {}
    saved = json.loads(file.read_text(encoding="utf-8"))
    qids = sorted({n["qid"] for n in saved["names"].values() if n["qid"] and n["confidence"] == "high"})
    pages = {q: {"label": saved["entities"][q]["label"], "description": saved["entities"][q]["description"], "url": ""}
             for q in qids if q in saved["entities"]}
    for start in range(0, len(qids), 200):
        query = ("SELECT ?item ?article WHERE { VALUES ?item { %s } ?article schema:about ?item ; "
                 "schema:isPartOf <https://en.wikipedia.org/> . }" % " ".join("wd:" + q for q in qids[start:start + 200]))
        rows = fetch(SPARQL, urllib.parse.urlencode({"query": query, "format": "json"}).encode("utf-8"),
                     "application/sparql-results+json")["results"]["bindings"]
        for row in rows:
            qid = row["item"]["value"].rsplit("/", 1)[1]
            if qid in pages:
                pages[qid]["url"] = row["article"]["value"]
    # The opening of each page, a few pages per request (the API returns at most 20 openings at a time).
    by_title = {urllib.parse.unquote(p["url"].rsplit("/", 1)[1]).replace("_", " "): q for q, p in pages.items() if p["url"]}
    titles = sorted(by_title)
    for start in range(0, len(titles), 20):
        answer = fetch(WIKIPEDIA + "?" + urllib.parse.urlencode({
            "action": "query", "prop": "extracts", "exintro": "1", "explaintext": "1", "exsentences": str(LEAD_SENTENCES),
            "exlimit": "20", "redirects": "1", "titles": "|".join(titles[start:start + 20]), "format": "json",
            "formatversion": "2"}))["query"]
        renamed = {r["to"]: r["from"] for r in answer.get("normalized", []) + answer.get("redirects", [])}
        for page in answer.get("pages", []):
            title = page["title"]
            while title not in by_title and title in renamed:
                title = renamed[title]
            if title in by_title and page.get("extract"):
                lead = re.sub(r"\s*\((?=[^()]*(?:;|[^\x00-\x7f]))(?:[^()]|\([^()]*\))*\)", "", page["extract"], count=1)
                pages[by_title[title]]["lead"] = tidy(lead)
    return pages


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug")
    ap.add_argument("--yes", action="store_true", help="confirm (the article lookup costs a fraction of a cent)")
    ap.add_argument("--characters", action="store_true", help="only refresh the characters' Wikipedia pages and descriptions (free)")
    args = ap.parse_args()

    out = ROOT / "data" / args.slug / "output"
    if args.characters:
        saved = json.loads((out / "about.json").read_text(encoding="utf-8"))
        saved["characters"] = character_pages(args.slug)
        (out / "about.json").write_text(json.dumps(saved, ensure_ascii=False, indent=1), encoding="utf-8")
        described = sum(1 for c in saved["characters"].values() if c.get("lead"))
        return print(f"Saved output/about.json: {described} of {len(saved['characters'])} matched characters have a Wikipedia description.")
    book = json.loads((out / "sections.json").read_text(encoding="utf-8"))
    header = next((s["text"] for s in book["sections"] if s["category"] == "boilerplate"), "")
    author = (re.search(r"^Author:\s*(.+)$", header, re.M) or [None, "unknown"])[1].strip()
    works = works_of(book)
    names = list(works)
    print(f"{book.get('title')}, by {author}: {len(names)} work{'s' if len(names) != 1 else ''} to look up on Wikipedia.")
    if not args.yes:
        return print(f"Nothing fetched. To do it: python -m annotate.about {args.slug} --yes")

    listing = "\n".join(f"{n}. {name}" for n, name in enumerate(names, 1))
    r = get_client().messages.create(
        model=MODEL, max_tokens=4000, system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"Book: {book.get('title')}\nAuthor: {author}\n\nWorks:\n{listing}"}],
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}})
    chosen = {a["n"]: a["article"].strip() for a in json.loads(next(b.text for b in r.content if b.type == "text"))["works"]}
    articles, of_section = {}, {}
    for n, name in enumerate(names, 1):
        title = chosen.get(n, "")
        if not title:
            continue
        if title not in articles:
            articles[title] = article(title)
            if articles[title] is None:     # a missing page or a disambiguation ("Bibliotheca"): search for it with the author
                articles[title] = next(iter(search_articles(f"{title} {author}", {title}, 1)), None)
            print(f"  {name} -> {title}: " + ("no such article" if articles[title] is None else ", ".join(
                f"{len(articles[title][k])} {k}" for k in ("summaries", "themes", "history", "primary", "secondary", "tertiary"))),
                  flush=True)
    # A title can redirect to something else altogether ("Hymn to Pan" is a modern poem): check each article found.
    client = get_client()
    pairs = [(name, chosen.get(n, "")) for n, name in enumerate(names, 1) if articles.get(chosen.get(n, ""))]
    bare = lambda name: re.sub(r"^\d+\.\s*", "", name)      # "1. Agamemnon" -> "Agamemnon", not mistaken for the item number
    if pairs:
        listing = "\n".join(f"{n}. Work: {bare(name)}\n   Article \"{articles[title]['title']}\": {articles[title]['lead'][:300]}"
                            for n, (name, title) in enumerate(pairs, 1))
        check = client.messages.create(
            model=MODEL, max_tokens=4000, system=CHECK_PROMPT,
            messages=[{"role": "user", "content": f"Book: {book.get('title')}\nAuthor: {author}\n\n{listing}"}],
            output_config={"format": {"type": "json_schema", "schema": CHECK_SCHEMA}})
        wrong = {a["n"] for a in json.loads(next(b.text for b in check.content if b.type == "text"))["pairs"] if not a["about"]}
        for n, (name, title) in enumerate(pairs, 1):
            if n in wrong:
                print(f"  {name}: the article \"{articles[title]['title']}\" is about something else; left out", flush=True)
            else:
                of_section.update({sid: articles[title]["title"] for sid in works[name]})
        # A rejected article ("Thebaid" is a region of Egypt) may have a namesake that is the work: try what a search finds.
        retry = [(name, title, found) for n, (name, title) in enumerate(pairs, 1) if n in wrong
                 for found in search_articles(f"{bare(name)} {author}", {title, articles[title]["title"], author})]
        if retry:
            listing = "\n".join(f"{n}. Work: {bare(name)}\n   Article \"{found['title']}\": {found['lead'][:300]}"
                                for n, (name, _, found) in enumerate(retry, 1))
            check = client.messages.create(
                model=MODEL, max_tokens=4000, system=CHECK_PROMPT,
                messages=[{"role": "user", "content": f"Book: {book.get('title')}\nAuthor: {author}\n\n{listing}"}],
                output_config={"format": {"type": "json_schema", "schema": CHECK_SCHEMA}})
            right = {a["n"] for a in json.loads(next(b.text for b in check.content if b.type == "text"))["pairs"] if a["about"]}
            for n, (name, title, found) in enumerate(retry, 1):
                if n in right and not any(sid in of_section for sid in works[name]):
                    articles[title] = found
                    of_section.update({sid: found["title"] for sid in works[name]})
                    print(f"  {name}: found \"{found['title']}\" instead", flush=True)
    used = set(of_section.values())
    # A synopsis in plain paragraphs: ask which sections each paragraph covers.
    for name in names:
        work = articles.get(chosen.get(names.index(name) + 1, ""))
        if not work or work["title"] not in used or work["summaries"] or not work["paragraphs"] or len(works[name]) < 2:
            continue
        described = []
        for sid in works[name]:
            section = next(s for s in book["sections"] if s["id"] == sid)
            file = out / "annotations" / f"{sid}.json"
            about = json.loads(file.read_text(encoding="utf-8"))["result"].get("summary", "") if file.exists() else ""
            described.append({"id": sid, "title": " > ".join(section["path"]), "about": about})
        align(client, work, described)
        print(f"  {work['title']}: {len(work['summaries'])} paragraphs of the synopsis tied to sections", flush=True)
    writer = article(author) if author != "unknown" else None        # for the "About this book" page
    if writer is None and len(author.split()) == 2:      # catalogues sometimes print the name the wrong way round ("Rhodius Apollonius")
        writer = article(" ".join(reversed(author.split())))
        author = writer["title"] if writer else author
    if writer and any(a and a["title"] == writer["title"] for a in articles.values()):
        writer = None       # no page of the author's own: the name leads to the work's page ("Pseudo-Apollodorus")
    if writer is None:      # then say what the work's article says of its author
        for a in (a for a in articles.values() if a and a["title"] in used):
            if found := authorship(a["title"]):
                writer = {"title": a["title"], "url": a["url"] + "#" + found[0], "lead": found[1], "primary": []}
                break
    saved = {"source": "Wikipedia", "license": "CC BY-SA 4.0",
             "author": {"name": author, "url": writer["url"], "lead": writer["lead"], "primary": writer["primary"]} if writer else None,
             "works": {a["title"]: a for a in articles.values() if a and a["title"] in used},
             "sections": of_section, "characters": character_pages(args.slug)}
    (out / "about.json").write_text(json.dumps(saved, ensure_ascii=False, indent=1), encoding="utf-8")
    linked = sum(1 for c in saved["characters"].values() if c["url"])
    print(f"Saved output/about.json: {len(saved['works'])} articles for {len(of_section)} sections; "
          f"{linked} of {len(saved['characters'])} matched characters have a Wikipedia page. "
          f"Tokens {r.usage.input_tokens:,} in / {r.usage.output_tokens:,} out.")


if __name__ == "__main__":
    main()
