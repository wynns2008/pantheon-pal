# Pantheon Pal

A reading companion for long books. It splits a Project Gutenberg text into sections, lets you read them in a web app, and shows a summary, the scene and its place on a map, definitions and literary analysis, history, the translator's footnotes and a family tree beside the text.

**Status:** the library is Greek and Roman: the *Iliad* and the *Odyssey* (both Butler), the *Argonautica* (Seaton), Hesiod with the Homeric Hymns (Evelyn-White), Ovid's *Metamorphoses* (Riley) and the *Aeneid*. All six are done with the current pipeline: two-pass annotation (summaries, scenes, map, notes), the review, the Wikidata check, key moments and the Wikipedia background, and they are cross-referenced with each other (315 shared characters). Three non-classical books used while building the parser (*Hamlet*, *Moby Dick*, *Sherlock Holmes*) are kept out of the reader in `archive/`, with the older annotations of Hesiod and *Metamorphoses*; move a book's folders back into `books/` and `data/` to restore it.

## What you get when reading

- **Text with underlined sentences.** A faint dotted underline marks a sentence that has notes; a notable quote gets a slightly warmer one. Click a sentence to see its notes in the side panes; click again to close. Nothing else clutters the page: a verse translation's line markers ("(ll. 1-25)") shrink to small grey labels, a section's opening argument (Butler's line at the head of each book of the *Iliad* and the *Odyssey*) becomes an italic subheading in ordinary case, and typed double hyphens become dashes.
- **About this book:** the first entry of every book's contents, and where a new book opens: author, translator and edition from the Gutenberg header, then what Wikipedia says of the work, when it was written, and its author, each with a "Read more" link and the article's picture where it has a free one. A **Sources** block follows, as rows of small buttons: primary (how the text itself survives: manuscripts, papyri), secondary (what the work drew on, scholarship and translations) and tertiary (retellings and adaptations in literature, film, opera and so on), as far as the Wikipedia article covers them, each with a link to the section of the article it quotes. Beside it, the map shows every place the book visits and the tree shows the 30 people with the most family links.
- **Contents:** the story's sections, plus the preface, the introductions and the translator's commentary (Riley's explanation after each fable), shown in italics. Previous and Next keep to the story when you are reading the story. Tables of contents, indexes and lists of footnotes are left out; the footnotes show beside their sentences. The book list shows short titles ("The Æneid", not "The Æneid of Virgil translated into English prose").
- **Side panes, top to bottom** (each can be folded, and the state is remembered; drag the edges of the chapter list and side column to resize):
  - **Summary & scene:** the summary, then a card for the scene you are reading: where it happens, what is at stake, who is present, and whether it is a story told by a character. It follows you as you scroll; click a scene in the list to jump to it. The summary is the translator's own where it is written in full sentences (Riley's before each fable of *Metamorphoses*). Where the translator gives only a heading (Butler's lines in capitals, phrases joined by dashes), Wikipedia's summary is shown instead, with its source as a link to that section of the article ("Wikipedia: Odyssey § Synopsis"); if Wikipedia has none for that section, the heading is shown in ordinary case. Where Wikipedia summarises a whole book and the section is one part of it, its line is added as "In Book 2:". A long Wikipedia summary shows its opening with a "Show all" button.
  - **Map** (Leaflet with MapTiler's plain grey "Dataviz" tiles, from OpenStreetMap data; place names may be in the local language): a numbered pin for each scene's place, joined in reading order; scenes at one spot share a pin. Grey dots are places from the rest of the book. Legendary places use their traditional identification (the Cyclopes in Sicily); places with no location (Olympus, the underworld) are listed under the map.
  - **Definitions & literary analysis:** odd phrasing, archaic words and allusions for the clicked sentence, followed by the devices it uses (simile, irony, foreshadowing and so on) and why a quote is notable. With nothing selected it lists the names used in the section (Jove = Jupiter), its notable quotes (click one to jump to it), and the themes of the work as Wikipedia describes them: a row of small buttons, one per theme, with a link to the section quoted ("Wikipedia: Odyssey § Structure").
  - **History:** background on the clicked sentence; the people of the scene with their one-line Wikidata description; and the history of the work from Wikipedia (dating, composition, influence, reception), as a row of small buttons with a link to the section quoted.
  - **References & notes:** the translator's own footnote for the clicked sentence, or all of the section's footnotes when nothing is selected. A citation of a book in a note is a link that opens it: "see book xiii", each book of a range or list ("bks. v. and vi.", "books ii, iii and iv"), and another work of the library ("the *Iliad* ix. 146" opens the *Iliad* at Book 9). Claude's notes in the other panes are linked the same way. Line numbers ("Ver. 4", "l. 82"), works the library does not hold, and book numbers in a work not divided into books (Hesiod) stay plain text.
  - **Family tree** (below).
- **Character profiles:** every character's name in the scene card and the side panes is a link. It opens a window with the name and how it is said ("Ulysses (yoo-LISS-eez)"), a picture where Wikipedia has a free one (with its credit), who they are, their aliases, parents, children and spouse, their **key moments** in this book (Claude's picks, a line each), a folded list of **all the scenes** they are present in, their key moments in the other books (one folding group per book), and the opening of their Wikipedia article with a "Read more" link. Every link opens the section at that scene.
- **Characters in this section** (under the family tree): each character's name links to their profile; one with a Wikipedia page is described by the opening sentences of that page, with a "Read more" link, and the rest by what the book says of them. Everyone in the book has a profile, including the many characters with no family link, who are not drawn in the tree (Teiresias, Eurylochus, Palinurus). Click outside the window or press Close to dismiss it.
- **Family tree** (Cytoscape.js): the whole book's tree. Characters in the current section are outlined. Click a person for the same profile under the tree: who they are (a god's domain), aliases, parents, children, spouse, then their **key moments** (the sections where they are at the centre of the story, each a link) and the same **in other books** of the library (Jove in the *Odyssey* lists Zeus's episodes in Hesiod; the link opens that book at the passage). Only gods, Titans and other creatures are labelled by kind. Click again to close. An **Expand** button opens a larger view.
  - Solid arrows are relationships the book states. Dashed links from beyond the text (for mythology, the Titans and primordial beings) are always shown.
  - A **Filters** menu (folded by default) holds a "Hide children unless they appear in this section" option and a **Connections out** slider (default 1), which limits the tree to people within N links of the characters you are reading about; **All** shows everyone. Click a person to show or hide their children with a button.
- **No spoiler protection:** everything is always shown, including notes that mention later events and the whole family tree. The model is no longer asked to flag spoilers.
- In the text itself, the translator's summary at the top of each fable is collapsed, because it usually gives away the plot.
- **Search:** the box in the top bar finds characters and places by any of their names, spelling-insensitive ("odysseus" finds Ulysses, "aeneas" finds Æneas). Each being or place is one row however many books it is in ("in 4 books"); it opens in the book you are reading if it is there, otherwise in that book. Entries in different books are one being or place only when they were matched to the same Wikipedia article or the cross-book index joins them, never by name alone, so the two Ajaxes stay apart.
- **Place profiles:** Tartarus, Olympus, Ithaca, Ulysses' house. The place in the scene card ("Where:") and the places listed under the map are links. A profile shows the picture, what kind of place it is (a realm, island, city…) and whether it is real, legendary or mythical, a line on it in this book, Wikipedia's account, the scenes set there and the sections that name it (each a link), and "Also in:" the same place in the other books. Character profiles have the same "Also in:" line.
- **Light and dark colours:** a button at the right of the top bar switches between them, map included. The page opens in light colours; a choice of dark is remembered in your browser.
- Reading position is remembered per book.

## Quick start

```bash
pip install -r requirements.txt
uvicorn server.app:app --reload        # then open http://127.0.0.1:8000/
```

The repository already contains parsed data and annotations for all five books, so the reader works immediately. The tree and map libraries load from a CDN, so they need an internet connection.

## Adding a book

Only Project Gutenberg plain-text files are supported (they must contain the `*** START` / `*** END` markers). A verse anthology without chapter headings, marked only with line ranges like `(ll. 1-25)`, is split into its works at their titles in capitals; a long poem is then cut into sections of about 100 lines, and a collection of fragments into groups ("Fragments 1-12").

### The protocol for a new book

Three of the first four books needed a parser fix (the fifth, the *Iliad*, needed none, but its trial showed a display fault that was fixed before the full run), and each fault was found late, after money had been spent on annotation. Follow these steps in order; steps 1 to 3 cost nothing.

1. **Dry run.** `python -m annotate.book <name> --gutenberg <number> --parse-only` downloads the text, parses it, checks the parse and stops. Then `python -m annotate.book <name>` (no `--yes`) prints what annotation would cost. Neither spends anything.
2. **Read the check.** Every line should say `ok` or `note`. A `WARN` or `FAIL` under PARSE or FOOTNOTES means the parser misread the book's layout: fix the parser first (the pipeline refuses to annotate after a `FAIL` unless you add `--ignore-checks`).
3. **Read the section list.** `python -m litparse show <name>`. The titles should be the book's own (no "Work 3" or "Text"), in order, of sensible length, with the introduction and notes kept out of the story. Open the longest and the shortest section (`python -m litparse show <name> <section id>`) and look at how each begins and ends.
4. **Trial.** `python -m annotate.run run <name> --limit 3 --yes` annotates three sections (about 10 to 40 cents). Start the reader and read one: the summary, the scene card, a few sentence notes, the map pins, the footnotes beside their sentences.
5. **Run the rest.** `python -m annotate.book <name> --yes --wikidata --tradition "Greek and Roman mythology"`. It carries on from the trial, runs the remaining steps (tree, outside links, review, Wikidata check, key moments, pronunciations, cross-book index, Wikipedia background, places, pictures), and ends with the full check.
6. **Read the full check.** Deal with every `WARN` and `FAIL`; the `note` lines are worth a glance.
7. **Look at it in the reader.** The About page (is each Wikipedia article really about this work?), the longest section, the last section, and the profile of the main god or hero (the pronunciation beside the name, key moments, all scenes, links to the other books). Say a few of the pronunciations aloud: they are Claude's, and a wrong one can be corrected by hand in `data/pronunciations.json`.

`python -m annotate.check <name>` runs the check on its own at any time; `--parse` limits it to the parse. What it looks for:

| Part | Faults it catches | First seen in |
|---|---|---|
| Parse | placeholder titles; one section far longer than the rest; a list of notes inside the story; lines of the file in no section; two sections with one title | Hesiod |
| Footnotes | markers with no note to show; notes listed but never joined to a sentence; note numbers stuck to words | the *Aeneid*, the *Odyssey* |
| Formatting | markup the reader would show as typed (illustrations, sidenotes, HTML tags, table rules) | none yet |
| Annotations | unannotated sections; scenes that overlap or run past their section; notes pointing past the end; sections with no notes; names in scenes with no profile | none yet |
| Family tree | a third parent that came from outside the book | *Metamorphoses* |
| Key moments | a character present in many scenes with no key moments | Jove in the *Odyssey* |
| Pronunciations | names with no pronunciation (the step was skipped or did not finish) | none yet |
| Wikipedia | no article found; fewer than half the sections covered; a reminder to read the About page | Hesiod's hymns |

It cannot judge whether a summary is accurate, whether a map pin is in the right place, or whether a Wikipedia article is the right one: step 7 is for those.

One command does everything. The number is the one in `gutenberg.org/ebooks/<number>`:

```bash
python -m annotate.book hesiod --gutenberg 348 --yes   # download, parse, annotate, build the tree, add outside links, review it
python -m annotate.book <name> --gutenberg 348 --parse-only   # download, parse and check the parse, then stop; spends nothing
python -m annotate.book <name>                         # no --yes: parse and show the cost; spends nothing
python -m annotate.book <name> --yes --no-outside      # skip the "outside the book" links (about 5 cents)
python -m annotate.book <name> --yes --no-review       # skip Claude's review of the finished tree (about 5 cents)
python -m annotate.book <name> --yes --no-moments      # skip picking each character's key moments (10 to 40 cents)
python -m annotate.book <name> --yes --no-pronounce    # skip how each character's name is said (a few cents)
python -m annotate.book <name> --yes --no-pictures     # skip the pictures from Wikimedia Commons (free)
python -m annotate.book <name> --yes --no-places       # skip the places and their profiles (5 to 20 cents)
python -m annotate.book <name> --yes --wikidata        # also check the tree against Wikidata (a few cents; add
                                                       #   --tradition "Greek and Roman mythology" as a hint)
```

It downloads the text unmodified to `books/<name>/source.txt` (or uses the file already there), parses it, annotates every section with Claude (needs an API key, see below), builds the family tree, adds the outside-the-book links, reviews the tree, picks each character's key moments, writes how each name is said, joins the book to the others and fetches its Wikipedia background: ten steps, with a check of the parse after step 2 and of the whole book at the end. Nothing is downloaded or spent without `--yes`, and already-annotated sections are skipped, so after a failure the same command carries on. The separate steps are below if you want more control.

```bash
# 1. put the text at books/<name>/source.txt (or a loose books/<name>.txt)
python -m litparse add <name>          # parse it; prints what was detected
python -m litparse show <name>         # list sections; add a section id to read one

# 2. optional: annotate it with Claude (needs an API key, see below)
python -m annotate.run estimate <name>             # free: counts tokens, prints the cost
python -m annotate.run run <name> --yes            # annotate; takes minutes

# 3. build the family tree
python -m annotate.merge <name>                    # merge people across sections (free)
python -m annotate.outside <name>                  # optional: one-off "outside the book" links (about 5 cents)
python -m annotate.realign <name> --yes            # after a parser change: parse again and keep the annotations (free)
python -m annotate.review <name> --yes             # Claude reads the whole tree: duplicates and wrong merges (about 5 cents)
python -m annotate.verify <name> --yes             # optional: match the characters to Wikidata (a few cents)
python -m annotate.moments <name> --yes            # each character's key moments (10 to 40 cents)
python -m annotate.pronounce <name> --yes          # how each character's name is said (a few cents)
python -m annotate.library                         # join characters across books (free)
python -m annotate.about <name> --yes              # Wikipedia's summaries, themes and history for the book (a fraction of a cent)
python -m annotate.places <name> --yes             # the book's places, each with a profile (5 to 20 cents)
python -m annotate.places <name> --recheck         # apply curated/places.json again: corrected articles, places added by hand (free)
python -m annotate.images <name>                   # pictures for the About page and profiles from Wikimedia Commons (free)
python -m annotate.merge <name>                    # merge again to include them
python -m annotate.check <name>                    # look for the faults earlier books had (free)
```

`--limit N` and `--only SECTION_ID` on `estimate` and `run` try a few sections first. Finished sections are saved one by one, so any run can be repeated and only redoes what is missing. Source texts are never modified.

### Claude API key

Put your key in `.env` in the project folder: `ANTHROPIC_API_KEY=...`. `.env` is listed in `.gitignore`; note that a synced folder such as OneDrive will still copy it. Scripts never print the key. Everything that spends money asks for `--yes`. A good habit is to set a monthly spend limit on the key in the Anthropic Console.

**Cost reference (Claude Sonnet 5.5):** annotating all four books again with the two-pass prompt cost about $16.60, plus a little for the Haiku steps; the *Odyssey* alone (24 books) was $3.29. The estimate the command prints runs 15 to 25% low. The *Iliad*, added with the protocol below, cost about $5.20 in all ($4.38 for annotation). Annotating the 53 sections of Hesiod that changed when its parse was fixed, with the review, Wikidata check and key moments, cost about $3.50.

## Publishing the website

The reader can be published as a static website: no server, no database, nothing that costs money to run. `python -m server.export` writes every answer the reader would ask the server for into `site/` (about 21 MB in 3,500 files for six books, in under a minute, free), together with the reader itself. On the website the browser reads those files instead of asking a server, and works out three things itself: the search (one index of every name, downloaded once on the first search), the "Also in:" lines, and who is in the section being read. Everything else is the server's own answer, saved, so the website shows exactly what `uvicorn server.app:app` does; locally the reader works as before.

```bash
python -m server.export                                # rebuild site/ (free)
python -m http.server 8002 --directory site            # try it locally at http://localhost:8002/
```

pantheonpal.com is served by Cloudflare Pages, which builds the site from the GitHub repository on every push:

- **Build command:** `pip install -r requirements.txt && python -m server.export`
- **Build output directory:** `site`
- **Environment variable:** `PYTHON_VERSION` = `3.12`
- **Custom domain:** pantheonpal.com (the domain is registered at Cloudflare, so it connects itself, with HTTPS)

So publishing a change is: run the pipeline or edit the code, commit, and push. `site/` is built, never committed (it is in `.gitignore`). The map tiles come from MapTiler with a free key that only works on pantheonpal.com, its `pantheon-pal.pages.dev` address and localhost; a copy hosted elsewhere needs its own free key in `web/app.js`. Running costs: the domain, about $10 a year; hosting and the map are free within their allowances.

## How it works

```
books/<name>/source.txt
   └─ litparse ─────► data/<name>/output/sections.json     sections with categories, paths, footnotes
                         └─ annotate.run ─► output/annotations/<section>.json   per-sentence notes, characters, facts
                                              └─ annotate.merge ─► output/tree.json    people + relationships, merged
   annotate.outside ─► data/<name>/curated/outside.json     links from beyond the book (hand-editable)
                         └─ merged into tree.json
server/app.py serves all of it;  web/ is the reader.
```

- **Parsing (`litparse/`, no AI).** Finds the Gutenberg boundaries, detects headings automatically (`BOOK I`, `CHAPTER 3`, `FABLES IV. V. AND VI.`, `ACT`/`SCENE`, bare `I.` numerals, ordinal and cardinal numbers) and the hierarchy (for example book > fable), and tags each section as body, commentary, front matter, appendix, back matter or boilerplate. A verse anthology with no such headings (Hesiod and the Homeric Hymns) is split at its titles in capitals: 67 works, from the *Theogony* to the fragments of the Epic Cycle. It also extracts footnotes in five styles (the fifth: an "ENDNOTES" list whose entries open with a bare number, as in the *Argonautica*): `[Footnote N: …]`; a list at the end marked in the text by a bare number ("the other East.1 He had gone", Butler's *Odyssey*); and endnotes numbered by chapter ("Scythes 1501." with "1501 (return) [ … ]" at the end, Hesiod). and notes listed book by book under a "Notes" heading whose markers are already in brackets (the *Aeneid*'s 287 notes and its lettered footnotes). Each number becomes a marker and the note is attached to its section. A section of the story over 12,000 words (an hour's reading) is cut into parts of about 4,000 words at paragraph breaks, never between "he said:" and the speech (the four books of the *Argonautica* became 15 parts). It splits text into numbered sentences so a note always lands on the right sentence. Tested on the four books of the library, *Hamlet*, *Moby Dick*, *Sherlock Holmes* and synthetic chaptered books.
- **Annotation (`annotate/`, Claude, run once, results cached).** Each section takes two passes. The outline pass reads the whole section as numbered sentences and returns a summary, its scenes (sentence range, place with map coordinates, what is at stake, who is present, whether it is a story told by a character), characters, relationships, alias notes, themes, notable quotes, legacy (later history and popular culture) and fun facts. The notes pass then goes through the section scene by scene (12 to 60 sentences at a time) for definitions, background and literary devices on single sentences, so a long section gets as many notes per sentence as a short one. The reader only reads these saved files, so reading is instant and free.
- **Merging (`annotate/merge.py`, no AI).** Joins names for the same person using spelling and the aliases the model gave. Only plain proper-name aliases join two entries; epithets like "the son of Saturn" never do. Two names the model lists as separate characters in the same section stay separate (that stopped Diana, Python and Hyperion being merged into Apollo). Corrections go in `data/<name>/curated/aliases.json`: `{"merge": [["Phoebus", "Apollo"]], "separate": [["Neptune", "Nereus"]], "remove": [["Atlas", "parent_of", "Jupiter"]], "add": [], "about": {}}`. Nobody gets a third parent from outside the book: links the book itself states always stay, then links added by hand, then those Wikidata confirms; the rest are dropped.
- **Outside the book (`annotate/outside.py`).** One-off Claude request that asks for well-established family links from beyond the text (mythology, history, the author's other works), for any book. For a self-contained novel it is told to return nothing. The result is saved in `curated/outside.json`, which you can edit by hand, and is drawn dashed.
- **Review (`annotate/review.py`, one Claude request per book).** Each section is annotated without sight of the others, so the merged tree can hold one person twice (Anticlea and Anticleia) or two beings under one name (the goddess Minerva and Mentor, whose shape she takes). Claude reads the whole cast once and returns the names that are one being and the names that were wrongly joined; the merge applies them. It does not judge family links: in a trial it called correct links wrong as often as it caught wrong ones.
- **Wikidata check (`annotate/verify.py`, optional, `--wikidata`).** The only independent check on the family links. It looks every character name up on Wikidata (free, public-domain data), lets Claude Haiku pick the entry the book means from the character's description (it can only choose a candidate or none), and reads each entry's parents, spouses, children and siblings. Where the book itself states a family link, the pair of namesakes that Wikidata links the same way wins. The merge then never joins names matched to different beings (the sun is not Apollo) and joins names matched to the same being, including a Roman god and its Greek counterpart; turns round a parent link that is the wrong way round; drops a "parent" who is really a grandparent when the parent in between is also in the book; drops a link from outside the book that Wikidata contradicts, but keeps one the book states and marks it `differs`; and fills gaps with Wikidata's parents (never against a parent the tree already has, at most two per child). Results are in `output/wikidata.json`; lookups and matches are cached in `output/wikidata_cache.json` (about 10 MB per book), so a rerun is free. Deleting `wikidata.json` turns the check off for that book.
- **Key moments (`annotate/moments.py`, a few Claude requests per book).** A god like Jove is named in almost every section, usually in passing. Claude is shown every section a character appears in, with what that section says of them, and the scenes of that section they are present in, and keeps only those where the character acts, decides, suffers or is the subject of the story (at most 6, or 10 for the main character), each with a line saying what happens. A character who is only ever mentioned gets none.
- **Pronunciations (`annotate/pronounce.py`).** Claude writes a plain English respelling of each character's name as the book spells it, with the stressed syllable in capitals. Wikipedia could not supply these: it gives a readable pronunciation for about one name in ten, and under its own spelling (Odysseus, not Ulysses). One file, `data/pronunciations.json`, serves every book, so a name is asked for once (1,525 names for the five books cost 41 cents). Names that are ordinary English words (Dawn, Earth) get none. Where Wikipedia does give a respelling for the same spelling of a name in the *Odyssey* (17 names), Claude's had the same syllables and stress in every case; for obscure names they are the usual English handling of such a name, not a sourced fact.
- **Library (`annotate/library.py`, free).** Joins characters across books into `data/library.json`. Two characters are the same being when the Wikidata check matched them to the same entry or to a Roman god and its Greek counterpart; names alone are not trusted across books, so a book takes part only once it has had the Wikidata check. A being has at most one person per book.
- **Sourced background (`annotate/about.py`).** Claude Haiku is asked which Wikipedia article is about each work (for a short work with no article of its own, the collection's: the Homeric Hymns), and is then shown the opening of each article found and asked whether it really is about that work, because a title can lead elsewhere ("Hymn to Pan" is a modern poem). The article's own text is then quoted. Sections on themes and style become the themes; sections on origins, influence and reception become the history; sections on manuscripts, on sources and scholarship, and on adaptations become the primary, secondary and tertiary sources of the About page. Summaries come from the synopsis: sections headed by book numbers ("Book 6: Underworld") and lines that begin "Book IV –" are matched to the book's sections by number. Where the synopsis is plain paragraphs (the *Odyssey*: 10 paragraphs for 24 books), Haiku is asked which sections each paragraph covers; the paragraph itself stays as Wikipedia wrote it. The author's article is recorded too (a name the catalogue prints the wrong way round, "Rhodius Apollonius", is tried both ways), and so is the Wikipedia page of every character the Wikidata check matched, with its first two sentences as that character's description; `python -m annotate.about <name> --characters` refreshes only those, for free. Wikipedia's text is CC BY-SA and is credited once, in the footer. The model's own section summaries, themes, historical notes, fun facts and popular-culture items are still saved in the annotations but are no longer shown; the scene cards and the notes on single sentences are the model's.
- **Places (`annotate/places.py`, one Claude request per book).** Claude is given the place of every scene (written freely while annotating, so one place has several forms: "Plain of Troy", "the plain before Ilius") and the capitalised names of the text that are not characters, which catches places named but never visited (Tartarus in the *Iliad*). It returns one entry per place: its name as the translation uses it, every form that means it, its kind, whether it is real, legendary or mythical, a line on it, and its Wikipedia article. The rest is free: the scenes set there, the sections that name it (a text search for its short names; a form naming someone else, "Telemachus' room" of Ulysses' house, is not searched or shown), a map position from its scenes, and the article's opening. The run prints how many scenes have a place profile, so a thin answer shows. Results are in `output/places.json`; hand corrections go in `curated/places.json` (`{"wikipedia": {"Heaven": ""}}` gives Hesiod's Heaven no article instead of Uranus's) and are applied again, free, by `--recheck`. Places Claude missed can be added there by hand, with an `"add"` list of `{"name", "forms", "kind", "certainty", "description", "wikipedia"}` (`forms` being the scene places and names that mean the place, as spelled in the book); their scenes, sections, map position and article are worked out as for Claude's, and an added place wins over one of the same name. Across books, the server joins entries matched to the same Wikipedia article, or joined by the cross-book index, into one search row and the "Also in:" line, characters only with characters and places only with places; a place named exactly as the article a namesake elsewhere links to joins it too (Riley's Tartarus, his word for the whole underworld).
- **Pictures (`annotate/images.py`, free).** For each Wikipedia article the About step found (the work, the author, each matched character) and each place's article, the article's lead image, if Wikipedia marks it as free. Commons supplies a small copy, the artist and the licence, shown as a credit line; clicking a picture opens its Commons page. Results are in `output/images.json`.
- Hand corrections in `curated/aliases.json` win over both, and the merge now gives the same tree on every run.
- **Server (`server/`, FastAPI).** Every route is scoped by book. The book list gives short titles (the catalogue's "of Virgil translated into…" and "Books I-VII" are cut). The contents route lists the story, the commentary and the introductions. The tree route also returns `others`, the people with no family link, so that they have profiles. The tree and annotation routes take `spoilers=false` to hide later links and spoiler-flagged notes, but the reader does not use it.

## Project layout

```
LitAnalyzer/
├── README.md, requirements.txt, .gitignore, .env      .env holds your API key (not shared)
├── .claude/launch.json              how the Claude desktop app starts the server on port 8000
├── books/<name>/source.txt          raw Gutenberg texts, never modified
├── litparse/                        parser: gutenberg, headings, builder, anchors (footnote markers), sentences, model, __main__ (CLI)
├── annotate/                        schema, run, check, sweep, pronounce, merge, outside, review, verify (Wikidata), moments, library, about (Wikipedia), places, images (Commons), realign, book (all in one)
├── server/app.py, export.py         API and static files; export writes the static website into site/
├── web/                             index.html, app.js, style.css
├── data/<name>/
│   ├── output/                      generated: sections.json, annotations/, tree.json
│   └── curated/                     hand-edited: outside.json, aliases.json
└── tests/                           empty so far
```

## A full audit

Run one after adding a book, after changing the parser, the pipeline or the reader, and before sharing the project. It has five parts, in this order; the first three are commands and take under a minute together, the last two are done by hand in the reader. None of it costs anything.

**1. The data, book by book.** Looks in each book's saved data for the faults earlier books had (the table under "The protocol for a new book" lists them).

```bash
python -m annotate.check <name>        # once for each book
```

Pass: no `FAIL`, and every `WARN` read and explained. A `note` needs no action.

**2. Everything the reader can open.** Asks the server for the contents, every section and its annotations, the map, the Wikipedia background, the family tree as seen from every section, and every character's profile; then follows every link from a profile, a key moment and the cross-book index to see that the section and the scene exist. No server needs to be running.

```bash
python -m annotate.sweep               # every book; add a name for one
```

Pass: "Everything opens."

**3. The code.** Every Python file compiles, every command the README names exists, and the static website builds.

```bash
python -m compileall -q litparse annotate server
python -m server.export
```

**4. The reader, by hand.** Start the server and, for every book, open the About page, the first, a middle and the last section of the story, and one preface or commentary page. On each, look at these and keep the browser's console open for errors:

- the heading and the opening of the text: no markup shown as typed (underscores, equals signs, "(ll. 1-25)", double hyphens), no heading in capitals, no stray labels such as "Source:";
- the Summary & scene pane: a summary with its source, the scene card, and the card following the text as you scroll;
- a click on an underlined sentence fills the Definitions, History or References pane; a second click clears it;
- a name in the scene card opens the profile; in it, a key moment and a scene under "All scenes" open the right place in the text, and a link under "In other books" opens the other book at the passage;
- the map shows numbered pins for the section; the family tree shows the section's characters;
- Previous and Next, the contents list, and the Contents and Details buttons;
- the footer's notice and the licence window.

Do the first of these once more with the system set to light colours.

**5. The content, by sampling.** The commands cannot judge meaning. In each book read one section closely against its notes: is the summary true to the text, are the scenes cut where the action turns, does the map pin sit where the scene is, do the notes explain what is actually hard? On the About page, read each Wikipedia article's opening to see that it is about this work. Check one family link you know (a hero's parents) and one you doubt.

Write down what was found, fix what is wrong at its source (the parser, the prompt or the reader, not the saved data by hand), and run parts 1 and 2 again.

### The latest audit (2 October 2026, five books)

- **Data:** no failures. One warning, in the *Iliad*: Peleus is listed in 8 scenes but has no key moments; he is only spoken of there, so the key moments are right.
- **Server:** 429 sections, 2,121 profiles and 9,711 links opened; nothing failed. The cross-book index holds 279 shared characters.
- **Code:** everything compiles; every command in this file exists.
- **Reader:** no script errors in 24 pages across the five books (checked by a script driving the page, not by eye), in dark and light colours. Text contrast is at least 5.4 to 1 everywhere.
- **Content:** not sampled in this audit, apart from Book 1 of the *Iliad* during its trial. Part 5 is still owed for the other books.
- **Fixed afterwards:** title pages in capitals are set in small capitals; italics that run over a line break display properly; the page has a site icon, so the browser no longer logs a "404"; the unused function, the empty `data/output` folder and the unused test packages in `requirements.txt` are gone.

## Known limits

- Wikipedia's coverage is uneven. Its *Odyssey* synopsis has no paragraph for Books 18 to 20 (they show Butler's heading), uses the Greek names (Odysseus, Athena) where Butler's text has Ulysses and Minerva, and one paragraph can span four books. For *Metamorphoses* it only lists each book's episodes. In Hesiod, 95 of 118 sections have an article, but most of the hymns and fragments have no summary at all and show only the scene card; "The Battle of Frogs and Mice" found no article. The *Aeneid* article says nothing of manuscripts. Sections are sorted into themes, history and sources by their headings, so a few land in the wrong group.
- Whether a translator's summary is "full sentences" is judged by its form (not in capitals, no dashes, at least eight words, ends with a full stop), not its grammar. When a heading in capitals is shown in ordinary case, a name keeps its capital only if the section's text or the tree has it: "lotophagi" and "laestrygones" come out lowercase.
- The model makes mistakes. Fix a wrong link with a `remove` entry in `curated/aliases.json` (only *Metamorphoses* has such a file, written for the older annotations and not checked against the new names). Non-person nodes (places, animals) cannot be deleted yet. A person can still have three parents when the book itself names three (Heracles: Zeus, Alcmena and Amphitryon; Phaëton: the Sun, Clymene and Apollo, where the Sun and Apollo are one figure in Ovid).
- On a phone (900 px wide or less) the reader changes layout: the top bar wraps, the contents open as a drawer from the left, and the notes as a sheet from the bottom, which a tap on an underlined sentence opens at that sentence's notes; profiles and the large tree fill the screen. Reading side by side with the notes still needs a wider window.
- Cross-book references need the Wikidata check on each book. The key moments are Claude's choice and vary a little between runs; a link to a moment opens at the first scene of that section with the character in it. Places are not cross-referenced yet.
- The review varies a little from run to run (one run joined Leocritus and Leiocritus, the next did not), and it leaves wrong family links alone. The Wikidata check has its own mistakes: with several namesakes it can match the wrong one (the Athenian Icarius for Penelope's father), which marks a correct link `differs`, and some of the parents it adds are one tradition among several. The reader does not yet show which links are confirmed or differ.
- Sentence-by-sentence reading only applies to annotated prose sections; unannotated sections (and verse) show plain text with line breaks kept.
- Heading detection needs `CHAPTER`/`BOOK`-style headings or standalone numerals, or (for a verse anthology) line markers and titles in capitals. Books with unnumbered chapters get no structure. In Hesiod a group of fragments with no number of its own is called "Part 7".
- The tree and map libraries load from a CDN and the map tiles from MapTiler, so both need internet access. MapTiler's free plan has a monthly allowance; past it the tiles stop loading until the next month (nothing is charged), and the rest of the reader keeps working. The key in `web/app.js` only works on pantheonpal.com, its Cloudflare Pages address and localhost; a copy of the project hosted elsewhere needs its own free key.
- Map coordinates come from the model and are not checked.
- A picture is the lead image of the character's Wikipedia article, so it is sometimes a scene with others in it (Eumaeus gets a vase of Telemachus leaving Penelope). Characters with no article, or no free image, have none. Pictures load from Wikimedia, so they need internet access.
- Each place's Wikipedia article is Claude's choice and is not checked; a wrong one (Hesiod's Heaven was given Uranus's) is corrected in `curated/places.json`. Many figures are both a being and a place (river gods, nymphs who are islands, Hesiod's Tartarus); characters and places are kept apart, so the god Tartarus and the place Tartarus are separate rows in the search, each with its own profile. Scene places with no name ("the woods", "the earth") get no profile.
- There are no automated tests of the code yet; `annotate.check` checks a book's data, not the programs.

## Roadmap

- [x] Parser with auto-detected structure and categories, one-command `add`
- [x] Backend API and web reader with saved position
- [x] Per-sentence notes, translator's footnotes, fun facts, collapsed summaries
- [x] Claude annotation pipeline with cost estimate, two passes per section, resumable (batch mode removed)
- [x] Merged family tree with outside-the-book links, depth slider, expanded view, click details
- [x] Spoiler switch with confirmation (removed again: the reader now always shows everything)
- [x] Literary analysis, historical analysis and notable quotes panes; neutral annotation prompt
- [x] Summary and scene card, map and history panes (the popular-culture pane and fun facts were removed again)
- [x] All four books annotated with the two-pass prompt, reviewed, checked against Wikidata, with key moments
- [x] Sourced background from Wikipedia: About page with sources, themes, history, summaries; character profile window
- [x] Site audit: profiles for every character, Hesiod parsed by its titles with its endnotes as footnotes, commentary and introductions in the contents, short book titles
- [x] The *Iliad*, added with the protocol
- [ ] Add more Greek and Roman books (the *Argonautica*)
- [ ] A layout for phones and narrow windows
- [x] Claude's review of the whole tree (duplicates, wrong merges); optional check against Wikidata
- [x] Key moments per character, and cross-references between books
- [ ] Cross-reference places; show confirmed and differing links in the reader
- [x] A check for each book's data (`annotate.check`), run by the one-command pipeline before and after annotation
- [x] A sweep of everything the reader can open (`annotate.sweep`), and a written full audit
- [ ] Tests for the parser and merge
- [ ] Bundle the tree libraries so the tree works offline
- [ ] A dedicated glossary and cross-reference detection (language notes cover this for now)

## Licence

- **Code** (`litparse/`, `annotate/`, `server/`, `web/`): MIT, see [LICENSE](LICENSE). Copyright (c) 2026 Wynn Shatzer.
- **Annotations made for this project** (scene cards, notes, key moments, places, pronunciations in `data/`): also MIT.
- **Book texts** (`books/`): from Project Gutenberg, public domain in the United States; each file keeps Gutenberg's header and licence. If you are elsewhere, check your local laws.
- **Wikipedia excerpts** (summaries, themes, history, sources and the opening lines on characters and places, in `data/*/output/about.json` and `places.json`): CC BY-SA 4.0, from the English Wikipedia; each links to its article.
- **Wikidata** (character descriptions and family links): CC0.
- **Pictures**: not stored here; they load from Wikimedia Commons, each under its own licence, shown in its credit line with a link to its Commons page.
- **Map tiles**: MapTiler (free plan), map data © OpenStreetMap contributors (ODbL); both credited on the map.
