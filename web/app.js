// Pantheon Pal reader: book picker, contents tree, section reading, saved position.
// Talks to the API in server/app.py. The annotation panes listen for the "section-loaded" event.
"use strict";

const $ = (id) => document.getElementById(id);
const els = {
  select: $("book-select"), toc: $("toc"), layout: document.querySelector(".layout"),
  tocToggle: $("toc-toggle"), title: $("section-title"), reference: $("section-reference"),
  text: $("section-text"), prev: $("prev"), next: $("next"), position: $("position"), refs: $("refs"),
  tree: $("tree"), defs: $("defs"),
  graph: $("tree-graph"), info: $("tree-info"), expand: $("tree-expand"), treeDialog: $("tree-dialog"),
  graphBig: $("tree-graph-big"), infoBig: $("tree-info-big"), treeClose: $("tree-close"),
  depth: $("tree-depth"), depthLabel: $("tree-depth-label"), autoCollapse: $("tree-autocollapse"), depthBig: $("tree-depth-big"),
  depthBigLabel: $("tree-depth-big-label"), count: $("tree-count"),
  legalText: $("legal-text"), licenseBody: $("license-body"), licenseDialog: $("license-dialog"),
  scene: $("scene"), map: $("map"), mapNote: $("map-note"), mapPane: $("pane-map"),
  lit: $("lit"), hist: $("hist"), treePane: $("pane-tree"), autoCollapseBig: $("tree-autocollapse-big"),
};
// The whole book is always shown, and so are the outside-the-book links.
const state = {
  slug: null, ids: [], current: null, ann: null, selected: null, outside: true,
  titles: new Map(), treeData: null, treeToken: 0, cy: null, cyBig: null,
  scene: 0, journey: null, map: null, mapLayer: null, mapFrame: null, sceneLock: 0, about: null, pills: {},
};

// Browser storage can be blocked; the reader must work without it.
const store = {
  get(key) { try { return JSON.parse(localStorage.getItem("litanalyzer:" + key)); } catch { return null; } },
  set(key, value) { try { localStorage.setItem("litanalyzer:" + key, JSON.stringify(value)); } catch { /* ignore */ } },
};

state.depth = Number(store.get("depth-user")) || 1;   // connections out from this section's characters; 7 = all
state.depthSet = store.get("depth-user") != null;     // true once the reader has moved the slider themselves
state.autoCollapse = store.get("autocollapse") !== false;   // hide the children of people with very many
state.collapse = new Map();                                  // person id -> true/false, set by the reader's own clicks

// The data: from server/app.py, or on the website (python -m server.export) from the files it wrote, which the
// browser reads like the server's answers. Search, "Also in:" and the section's people are worked out here then.
async function api(path) {
  if (window.STATIC_SITE) return siteApi(path);
  const res = await fetch("/api/" + path);
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
  return res.json();
}

const siteFiles = new Map();          // file -> its contents (a promise), each fetched once
function siteFile(name) {
  if (!siteFiles.has(name)) {
    siteFiles.set(name, fetch(`/api/${name}.json`).then((r) => { if (!r.ok) throw new Error(r.statusText); return r.json(); })
      .catch((e) => { siteFiles.delete(name); throw e; }));
  }
  return siteFiles.get(name);
}

async function siteApi(path) {
  const [route, query] = path.split("?");
  const q = new URLSearchParams(query || "");
  if (route === "books") return siteFile("books");
  if (route === "search") return siteSearch(q.get("q") || "", q.get("book") || "");
  const [, slug, what, id, sub] = route.split("/");
  const base = `books/${slug}/`;
  switch (what) {
    case "toc": case "about": case "places": case "items": case "journey": return siteFile(base + what);
    case "books": return siteFile(base + "book-starts");
    case "sections": return siteFile(!id ? base + "boilerplate" : base + (sub === "annotations" ? "annotations/" : "sections/") + id);
    case "people": return siteFile(base + "people/" + decodeURIComponent(id));
    case "elsewhere": return (await siteFile(base + "elsewhere"))[`${q.get("type")}:${q.get("id")}`] || [];
    case "tree": {
      const tree = await siteFile(base + "tree"), upto = q.get("upto");
      return upto ? { ...tree, position: upto, current: (await siteFile(base + "tree-current"))[upto] || [] } : tree;
    }
  }
  throw new Error("Not on the website: " + path);
}

// The server's search (server/app.py: search), on the website: same spelling-insensitive names, same order.
const normName = (s) => s.replace(/[œŒ]/g, "oe").replace(/[æÆ]/g, "ae").normalize("NFKD").replace(/[̀-ͯ]/g, "")
  .toLowerCase().replace(/[^a-z ]/g, " ").split(/\s+/).filter(Boolean).join(" ");

async function siteSearch(q, book) {
  const key = normName(q);
  if (key.length < 2) return [];
  const groups = await siteFile("search-index");
  const row = (e) => ({ type: e.type, book: e.book, book_title: e.book_title, id: e.id, name: e.name, kind: e.kind });
  const found = [];
  for (const members of groups) {
    let best = null;
    for (const e of members) {
      e.keys ??= e.names.map(normName);           // worked out once, on the first search
      e.names.forEach((n, i) => {
        const k = e.keys[i];
        if (!k.includes(key)) return;
        const rank = k.startsWith(key) ? 0 : (" " + k).includes(" " + key) ? 1 : 2;
        if (!best || rank < best.rank || (rank === best.rank && n < best.n)) best = { rank, n };
      });
    }
    if (!best) continue;
    const main = members.find((e) => e.book === book) || members.reduce((a, b) => (b.weight > a.weight ? b : a));
    found.push({ rank: best.rank, weight: members.reduce((s, e) => s + e.weight, 0),
      row: { ...row(main), as: best.n === main.name ? "" : best.n, about: main.about, books: members.map(row) } });
  }
  return found.sort((a, b) => a.rank - b.rank || b.weight - a.weight).slice(0, 40).map((f) => f.row);
}

function showError(message) {
  els.title.textContent = "Something went wrong";
  els.text.replaceChildren(Object.assign(document.createElement("p"), { textContent: message }));
}

// ---------- Books and contents ----------

async function loadBooks() {
  const books = await api("books");
  if (!books.length) return showError("No books parsed yet. Run: python -m litparse add <name>");
  els.select.replaceChildren(...books.map((b) =>
    Object.assign(document.createElement("option"), { value: b.slug, textContent: b.title || b.slug })));
  const [hashSlug, hashId] = location.hash.slice(1).split("/");
  const last = store.get("last") || {};
  const slug = books.some((b) => b.slug === hashSlug) ? hashSlug
    : books.some((b) => b.slug === last.slug) ? last.slug : books[0].slug;
  els.select.value = slug;
  loadCitations(books);
  await openBook(slug, hashSlug === slug ? hashId : null);
}

async function openBook(slug, sectionId) {
  state.slug = slug;
  const tree = await api(`books/${slug}/toc`);
  state.ids = [];
  state.kinds = new Map();         // section id -> category (body, commentary, front_matter)
  state.titles = new Map();
  state.collapse = new Map();
  state.journey = null;
  state.about = null;
  state.places = [];
  state.items = [];
  loadJourney(slug);
  loadPlaces(slug);
  loadItems(slug);
  loadAbout(slug);
  els.toc.replaceChildren(...tree.map((n) => renderNode(n)));
  const first = el("a", { href: `#${slug}/${ABOUT}`, textContent: "About this book" });
  first.dataset.id = ABOUT;
  els.toc.prepend(el("li", {}, first));
  state.ids.unshift(ABOUT);
  state.titles.set(ABOUT, "About this book");
  state.pills = {};
  const saved = store.get("pos:" + slug);
  goTo(sectionId || saved || ABOUT);
  loadLegal(slug);
}

// ---------- Project Gutenberg notice ----------

// Each book keeps its own Gutenberg header and license as "boilerplate" sections.
async function loadLegal(slug) {
  let parts = [];
  try { parts = (await api(`books/${slug}/sections?category=boilerplate&include_text=true`)).map((s) => s.text); } catch { /* none */ }
  if (state.slug !== slug) return;                       // the reader moved to another book meanwhile
  const text = parts.join("\n\n" + "-".repeat(60) + "\n\n");
  const numbers = [...new Set([...text.matchAll(/\[eBook #(\d+)\]/g)].map((m) => m[1]))];
  const title = els.select.selectedOptions[0]?.textContent || slug;
  const author = (text.match(/^Author:\s*(.+)$/m) || [])[1];
  const source = (text.match(/^Source:\s*(.+)$/m) || [])[1];             // a text from another open source (litparse.perseus)
  const licence = (text.match(/^Licence:\s*(.+)$/m) || [])[1];
  const note = [document.createTextNode(`Text: ${title}${author ? ", " + author : ""}, ${source ? "from " + source.split(": ")[0] : "Project Gutenberg eBook "}`)];
  if (source) {
    const url = (source.match(/https?:\/\/\S+/) || [])[0];
    if (url) note.push(document.createTextNode(" ("), Object.assign(document.createElement("a"), { href: url, target: "_blank", rel: "noopener", textContent: "source" }), document.createTextNode(")"));
    note.push(document.createTextNode(`. ${licence || ""}. `));
  }
  numbers.forEach((num, i) => {
    if (i) note.push(document.createTextNode(", "));
    note.push(Object.assign(document.createElement("a"), { href: `https://www.gutenberg.org/ebooks/${num}`, target: "_blank", rel: "noopener", textContent: "#" + num }));
  });
  note.push(document.createTextNode((source ? "" : ", unmodified. Public domain in the United States; if you are elsewhere, check your local laws. "
    + "Not affiliated with or endorsed by Project Gutenberg. ") + "Notes, analysis may contain errors. "
    + "Summaries, themes and history are excerpted from Wikipedia ("),
    Object.assign(document.createElement("a"), { href: "https://creativecommons.org/licenses/by-sa/4.0/", target: "_blank", rel: "noopener", textContent: "CC BY-SA 4.0" }),
    document.createTextNode("); character descriptions are from Wikidata."));
  els.legalText.replaceChildren(...note);
  els.licenseBody.textContent = text || "The Project Gutenberg license is at https://www.gutenberg.org/policy/license.html";
}

function firstId(node) {
  return node.id || node.children.map(firstId).find(Boolean) || null;
}

function renderNode(node, prefix = []) {
  const li = document.createElement("li");
  if (node.id) {
    state.ids.push(node.id);
    state.kinds.set(node.id, node.category);
    state.titles.set(node.id, [...prefix, node.name].join(" \u203a "));
    const a = Object.assign(document.createElement("a"), { href: `#${state.slug}/${node.id}`, textContent: node.name });
    a.dataset.id = node.id;
    if (node.category !== "body") a.className = "extra";        // an introduction or the translator's commentary
    li.append(a);
  } else {
    // A heading with no text of its own (a story split into parts, or a book of fables): it opens its first part.
    const first = firstId(node);
    const a = Object.assign(document.createElement("a"), { className: "group", textContent: node.name });
    if (first) a.href = `#${state.slug}/${first}`;
    li.append(a);
  }
  if (node.children.length) {
    const ul = document.createElement("ul");
    ul.append(...node.children.map((c) => renderNode(c, [...prefix, node.name])));
    li.append(ul);
  }
  return li;
}

// ---------- Reading ----------

function goTo(id) {
  if (!id) return showError("This book has no readable sections.");
  const hash = `#${state.slug}/${id}`;
  if (location.hash === hash) showSection(id);   // no hashchange fires when the hash is unchanged
  else location.hash = hash;
}

async function showSection(id) {
  if (id === ABOUT) return showAbout();
  let s, a;
  try {
    [s, a] = await Promise.all([
      api(`books/${state.slug}/sections/${id}`),
      api(`books/${state.slug}/sections/${id}/annotations`).catch(() => null),
    ]);
  } catch (e) { return showError(e.message); }
  state.current = s;
  state.ann = a && a.annotated ? a : null;
  state.selected = null;
  state.scene = 0;
  els.title.textContent = s.path.join(" › ");
  els.reference.hidden = !s.reference;
  els.reference.textContent = s.reference ? `Lines ${s.reference}` : "";
  if (state.ann) renderSentences(s, state.ann); else renderText(s.text);
  renderPanes();
  renderScene();
  drawMap();
  loadTree();
  setPager(s);
  markActive(s.id);
  store.set("last", { slug: state.slug });
  store.set("pos:" + state.slug, s.id);
  document.querySelector(".reader").scrollTop = 0;
  showFound();
  document.dispatchEvent(new CustomEvent("section-loaded", { detail: { slug: state.slug, section: s } }));
}

// Previous and Next follow the contents list. From a part of the story they skip the commentary and go to the
// story's next part; from an introduction or a commentary they go to whatever comes next.
function setPager(s) {
  const i = state.ids.indexOf(s.id);
  const story = (id) => state.kinds.get(id) === "body";
  const step = (by) => {
    for (let k = i + by; i >= 0 && k >= 0 && k < state.ids.length; k += by) {
      if (!story(s.id) || story(state.ids[k]) || state.ids[k] === ABOUT) return state.ids[k];
    }
    return "";
  };
  const prev = step(-1) || (s.id !== ABOUT ? ABOUT : ""), next = step(1);     // the About page comes first
  els.prev.disabled = !prev;
  els.next.disabled = !next;
  els.prev.dataset.id = prev;
  els.next.dataset.id = next;
  els.position.textContent = i >= 0 ? `${i + 1} of ${state.ids.length}` : "";
}

function markActive(id) {
  for (const a of els.toc.querySelectorAll("a")) a.removeAttribute("aria-current");
  const a = els.toc.querySelector(`a[data-id="${CSS.escape(id)}"]`);
  if (a) { a.setAttribute("aria-current", "true"); a.scrollIntoView({ block: "nearest" }); }
}

// Gutenberg text is hard-wrapped. Join wrapped prose into paragraphs; keep verse and short lines as they are.
function renderText(text) {
  const blocks = text.split(/\n\s*\n/).map((b) => b.split("\n").filter((l) => l.trim()));
  els.text.replaceChildren(...blocks.filter((b) => b.length).map((lines) => {
    const lengths = lines.map((l) => l.trim().length);
    const verse = lines.length > 1 && lengths.reduce((a, b) => a + b, 0) / lines.length < 50;
    const p = document.createElement("p");
    // A title page or a heading set in capitals is shown in small capitals, which shout less.
    const letters = lines.join("").replace(/[^A-Za-z]/g, "");
    if (letters.length > 3 && letters === letters.toUpperCase()) p.classList.add("caps");
    if (verse) {
      p.classList.add("verse");
      withMarkers(p, lines.map((l) => l.replace(/\s{4,}\d+\s*$/, "").trimEnd()).join("\n"));
    } else {
      // Drop right-hand line numbers such as "as he                5", then join the wrapped lines.
      withMarkers(p, lines.map((l) => l.replace(/\s{4,}\d+\s*$/, "").trim()).join(" "));
    }
    return p;
  }));
}

// Turn footnote markers like [12] into superscripts. A lettered marker ([B]) counts only if the section has that note.
function withMarkers(el, text) {
  const lettered = new Set(((state.current && state.current.footnotes) || []).map((f) => `[${f.label}]`));
  for (const part of text.split(/(\[(?:\d+|[A-Z])\])/)) {
    if (/^\[\d+\]$/.test(part) || lettered.has(part)) {
      el.append(Object.assign(document.createElement("sup"), { className: "fn", textContent: part }));
    } else if (part) {
      el.append(rich(part));
    }
  }
}

// Project Gutenberg marks italics with underscores ("_the_ woman" is shown as <em>the</em> woman) and bold with
// equals signs ("=Laocoon.="); italics may run over a line break. Riley's Metamorphoses puts the words he added to the Latin in {braces}; they are
// shown in muted italics. A verse translation's line markers ("(ll. 1-25)") and its gaps ("((LACUNA))") become
// small labels, and a dash typed as "--" becomes a dash.
function rich(text, cite = false) {
  const out = document.createDocumentFragment();
  for (const part of text.split(/(_[^_]{1,300}_|\{[^{}\n]+\}|=(?=\S)[^=\n]+(?<=\S)=|\(ll?\.\s*\d+(?:\s*[-\u2013]\s*\d+)?\)|\(\([A-Z ]+\)\))/)) {
    const italic = /^_[^_]+_$/.test(part), added = /^\{[^{}\n]+\}$/.test(part);
    if (italic || added) {
      const em = document.createElement("em");
      if (added) em.className = "added";
      em.textContent = part.slice(1, -1);
      out.append(em);
    } else if (/^=\S[^=\n]*=$/.test(part) && part.length > 2) {
      out.append(Object.assign(document.createElement("strong"), { textContent: part.slice(1, -1) }));
    } else if (/^\(ll?\./.test(part)) {
      out.append(Object.assign(document.createElement("span"), { className: "lines", textContent: part.replace(/^\(ll?\.\s*/, "").slice(0, -1) }));
    } else if (/^\(\(/.test(part)) {
      out.append(Object.assign(document.createElement("span"), { className: "lines", textContent: part.slice(2, -2).toLowerCase() }));
    } else if (part) {
      const plain = part.replace(/\u2014-+|--/g, "\u2014");
      out.append(...(cite ? citations(plain) : [plain]));
    }
  }
  return out;
}

// ---------- Citations in notes ("see book xiii", "the Iliad ix. 146") ----------

// Where each numbered book of every work starts ({slug: {"13": section id}}), and the works' names as a note
// would cite them ("Iliad", "\u00c6neid" or "Aeneid"). Loaded once with the book list.
state.cite = { starts: {}, works: [] };

async function loadCitations(books) {
  const found = await Promise.all(books.map((b) => api(`books/${b.slug}/books`).catch(() => ({}))));
  books.forEach((b, i) => { state.cite.starts[b.slug] = found[i]; });
  state.cite.works = books.filter((b, i) => Object.keys(found[i]).length).flatMap((b) => {
    const name = b.title.replace(/^The\s+/, "");
    const plain = name.replace(/\u00c6/g, "Ae").replace(/\u00e6/g, "ae");
    return [...new Set([name, plain])].map((n) => ({ slug: b.slug, name: n, title: b.title }));
  });
  if (state.current) renderPanes();
}

const ROMAN = { i: 1, v: 5, x: 10, l: 50, c: 100 };
function bookNumber(s) {
  if (/^\d+$/.test(s)) return Number(s);
  let n = 0;
  const v = s.toLowerCase().split("").map((c) => ROMAN[c]);
  v.forEach((x, k) => { n += x < (v[k + 1] || 0) ? -x : x; });
  return n;
}

// A note's text with its citations of a numbered book turned into links: "book xiii" and "bk. ii." open that book
// of the work being read, "the Iliad ix. 146" that book of the Iliad, if the library has it. Line numbers are not
// linked; a citation of a work the library does not hold stays text.
function citations(text) {
  const names = state.cite.works.map((w) => w.name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const num = "([ivxlc]+|\\d+)";
  const other = names.length ? `\\b(${names.join("|")})[\u201d"\u2019']*,?\\s+(?:(?:books?|bks?\\.?)\\s*)?${num}\\b` : "(?!)()()";
  const pattern = new RegExp(`${other}|\\b(?:books?|bks?\\.)\\s*${num}\\b`, "gi");
  const nodes = [];
  let last = 0;
  const link = (slug, id, words, n, work) => el("a", { href: `#${slug}/${id}`, className: "cite", textContent: words,
    title: `Open ${work ? work.title + ", " : ""}Book ${n}` });
  for (const m of text.matchAll(pattern)) {
    if (m.index < last) continue;                    // inside a range already linked
    const work = m[1] ? state.cite.works.find((w) => w.name.toLowerCase() === m[1].toLowerCase()) : null;
    const slug = work ? work.slug : state.slug;
    const n = bookNumber(m[2] || m[3]);
    const id = (state.cite.starts[slug] || {})[String(n)];
    if (!id) continue;                               // no such book: leave the words as they are
    if (m.index > last) nodes.push(text.slice(last, m.index));
    nodes.push(link(slug, id, m[0], n, work));
    last = m.index + m[0].length;
    // The rest of a range or list links too: "bks. v. and vi.", "books ii, iii and iv", "ix-x".
    for (let more; (more = /^(\.?\s*(?:,|and|&|to|[-–])\s*)([ivxlc]+|\d+)\b/i.exec(text.slice(last)));) {
      const k = bookNumber(more[2]), next = (state.cite.starts[slug] || {})[String(k)];
      if (!next) break;
      nodes.push(more[1], link(slug, next, more[2], k, work));
      last += more[0].length;
    }
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

// A Wikipedia link labelled as the source it is: "Wikipedia: Odyssey \u00a7 Structure".
function wikiCite(url) {
  if (!/\/wiki\/./.test(url || "")) return "Read more";
  const [page, section] = url.split("/wiki/")[1].split("#");
  const name = (s) => decodeURIComponent(s).replace(/_/g, " ");
  return "Wikipedia: " + name(page) + (section ? " \u00a7 " + name(section) : "");
}

// ---------- Annotated sections: clickable sentences and the side panes ----------

const el = (tag, props = {}, ...children) => {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children);
  return node;
};

function noteIndex(a) {
  const idx = new Map();   // sentence number -> { lang: [], ctx: [], lit: [], quote: [] }
  const slot = (i) => { if (!idx.has(i)) idx.set(i, { lang: [], ctx: [], lit: [], quote: [] }); return idx.get(i); };
  for (const n of a.annotations.language_notes) slot(n.sentence).lang.push(n);
  for (const n of a.annotations.context_notes) slot(n.sentence).ctx.push(n);
  for (const n of a.annotations.literary_notes || []) slot(n.sentence).lit.push(n);
  for (const n of a.annotations.notable_quotes || []) slot(n.sentence).quote.push(n);
  return idx;
}

// The translator's summary at the top of each fable usually gives away what happens, so it starts collapsed.
function hasSummary(s) {
  const blocks = s.text.split(/\n\s*\n/);
  const first = blocks[0].split("\n");
  // Riley indents the later lines of a summary and of nothing else. A text whose every paragraph is set like that
  // (the prose Aeneid) has no summary: its first paragraph is the poem itself.
  const indented = (block) => block.split("\n").slice(1).some((line) => line.startsWith("  "));
  return s.category === "body" && first.length > 1 && first[1].startsWith("  ") && blocks.length > 1 && !indented(blocks[1]);
}

function renderSentences(s, a) {
  const notes = noteIndex(a);
  const paragraphs = [];
  for (const sentence of a.sentences) (paragraphs[sentence.p] ||= []).push(sentence);
  const nodes = paragraphs.map((sentences, p) => {
    const para = document.createElement("p");
    // A heading printed in capitals at the top of a section (Butler's Odyssey) is shown as a subheading in ordinary case.
    const letters = p === 0 ? sentences.map((x) => x.text).join(" ").replace(/[^A-Za-z]/g, "") : "";
    const capitals = letters.length > 8 && letters === letters.toUpperCase();
    // So is the translator's argument in ordinary case (Butler's Iliad), which the server recognises by its pattern.
    const heading = capitals || (p === 0 && s.argument && !hasSummary(s));
    let italic = false;          // an italic stretch (_..._) of a stage direction can run over a sentence break
    sentences.forEach((sentence, k) => {
      const span = document.createElement("span");
      span.className = "s";
      span.dataset.i = sentence.i;
      if (notes.has(sentence.i) || sentence.footnotes.length) {
        if (notes.get(sentence.i)?.quote.length) span.classList.add("quoted");
        span.classList.add("has-note");
        span.tabIndex = 0;
        span.setAttribute("role", "button");
      }
      // Close an italic stretch at the end of the sentence it starts in, and open it again in the next.
      let text = (italic ? "_" : "") + (capitals ? normalCase(sentence.text, s) : sentence.text);
      italic = (text.match(/_/g) || []).length % 2 === 1;
      withMarkers(span, italic ? text + "_" : text);
      para.append(span, k < sentences.length - 1 ? " " : "");
    });
    if (heading) para.className = "argument";
    if (p === 0 && hasSummary(s)) {
      const details = el("details", { className: "summary" }, el("summary", { textContent: "Translator\u2019s summary (may contain spoilers)" }));
      const body = el("div", { className: "text-body" });
      body.append(para);
      details.append(body);
      return details;
    }
    return para;
  });
  els.text.replaceChildren(...nodes);
}

// A notable quote as a block you can click to jump to its sentence.
function quoteBlock(q) {
  const block = el("blockquote", { className: "quote", tabIndex: 0 },
    rich(state.ann.sentences[q.sentence].text.replace(/\[\d+\]/g, "")), el("span", { className: "why", textContent: q.reason }));
  block.addEventListener("click", () => {
    if (state.selected !== q.sentence) selectSentence(q.sentence);
    els.text.querySelector(`.s[data-i="${q.sentence}"]`)?.scrollIntoView({ block: "center", behavior: "smooth" });
  });
  return block;
}

function selectSentence(i) {
  state.selected = state.selected === i ? null : i;
  for (const node of els.text.querySelectorAll(".s.selected")) node.classList.remove("selected");
  if (state.selected !== null) els.text.querySelector(`.s[data-i="${i}"]`)?.classList.add("selected");
  if (state.selected !== null && scenes().length) setScene(sceneAt(i));
  renderPanes();
  // On a phone the notes are out of sight: open the sheet at the sentence's notes.
  if (state.selected !== null && phone.matches) {
    openPanel("side");
    // The first pane with something on this sentence: definitions, then history, then the footnote.
    const filled = [els.defs, els.lit, els.hist, els.refs].find((n) => !n.hidden && !n.classList.contains("placeholder")) || els.defs;
    const pane = filled.closest("details"), side = $("side-panel");
    pane.open = true;
    side.scrollTop = pane.offsetTop - side.querySelector(".sheet-bar").offsetHeight - 8;
  }
}

function placeholder(node, text) { node.className = "placeholder"; delete node.dataset.person; node.replaceChildren(text); }

function renderPanes() {
  const s = state.current, a = state.ann, sel = state.selected;
  const footnotes = s.footnotes || [];
  const byLabel = new Map(footnotes.map((f) => [f.label, f]));
  const notes = a ? noteIndex(a) : new Map();
  const here = sel !== null && notes.get(sel) || { lang: [], ctx: [], lit: [], quote: [] };
  // What an empty pane says: the About page has no sentences to select.
  const idle = s.id === ABOUT ? "Open a section to see its notes."
    : s.category !== "body" ? "Notes are made for the story itself, not for this part of the book." : "Not annotated yet.";

  // Definitions: language notes for the selected sentence, otherwise the names used in this section.
  if (sel !== null && here.lang.length) {
    els.defs.className = "";
    els.defs.replaceChildren(...here.lang.map((n) => el("p", { className: "note" },
      el("span", { className: "label", textContent: n.kind + ":" }), " ", rich(n.text, true))));
  } else if (sel !== null) {
    placeholder(els.defs, "No language notes on this sentence.");
  } else if (a && a.annotations.alias_notes.length) {
    els.defs.className = "";
    els.defs.replaceChildren(el("h4", { textContent: "Names in this section:" }),
      ...a.annotations.alias_notes.map((n) => el("p", { className: "note" },
        el("span", { className: "label" }, `${n.alias} = `, personLink(n.canonical), ":"), " ", rich(n.explanation, true))));
  } else {
    placeholder(els.defs, a ? "Select an underlined sentence to see what it means." : idle);
  }

  // Literary analysis: the selected sentence's quote and devices; otherwise the section's themes and notable quotes.
  const work = workAbout();
  const themes = work ? work.themes : [];
  const quotes = a ? a.annotations.notable_quotes || [] : [];
  if (sel !== null && (here.lit.length || here.quote.length)) {
    els.lit.className = "";
    els.lit.replaceChildren(
      ...here.quote.map((n) => el("p", { className: "note" }, el("span", { className: "label", textContent: "Notable quote:" }), " " + n.reason)),
      ...here.lit.map((n) => el("p", { className: "note" },
        el("span", { className: "label", textContent: n.device.charAt(0).toUpperCase() + n.device.slice(1) + ":" }), " ", rich(n.text, true))));
  } else if (sel !== null) {
    placeholder(els.lit, "No literary devices or quotes noted on this sentence.");
  } else if (themes.length || quotes.length) {
    els.lit.className = "";
    els.lit.replaceChildren(
      ...(quotes.length ? [el("h4", { textContent: "Notable quotes:" }), ...quotes.map(quoteBlock)] : []),
      ...(themes.length ? [el("h4", { textContent: `Themes of ${workName(work)}:` }), sourcedGroup("themes", themes, work.url)] : []));
  } else {
    placeholder(els.lit, a ? "Select an underlined sentence to see literary devices." : idle);
  }
  els.lit.hidden = els.lit.classList.contains("placeholder");     // one shared pane: show this part only when it has content

  // History: background on the selected sentence, the section's significance in its own time, then what it led to.
  const people = scenePeople();
  const histParts = [
    ...(sel !== null ? here.ctx.map((n) => el("p", { className: "note" }, el("span", { className: "label", textContent: "Background:" }), " ", rich(n.text, true))) : []),
    ...(people.length ? [el("h4", { textContent: scenes().length ? "In this scene:" : "In this section:" }), ...people.map((p) => el("p", { className: "note" },
      personLink(p.name), ": " + p.description))] : []),
    ...(work && work.history.length ? [el("h4", { textContent: `History of ${workName(work)}:` }), sourcedGroup("history", work.history, work.url)] : []),
  ];
  if (histParts.length) { els.hist.className = ""; els.hist.replaceChildren(...histParts); }
  else placeholder(els.hist, a ? "Select an underlined sentence for background." : idle);

  // References: the translator's footnotes cited by the selected sentence, or all of the section's notes.
  if (sel !== null) {
    const cited = a.sentences[sel].footnotes.map((l) => byLabel.get(l)).filter(Boolean);
    if (cited.length) {
      els.refs.className = "";
      els.refs.replaceChildren(...cited.map((f) => el("p", { className: "note" },
        el("span", { className: "label", textContent: `Translator\u2019s note ${f.label}:` }), " ", rich(f.text, true))));
    } else {
      placeholder(els.refs, "No translator's note on this sentence.");
    }
  } else if (footnotes.length) {
    els.refs.className = "";
    els.refs.replaceChildren(el("h4", { textContent: "Translator\u2019s notes:" }), el("ol", {}, ...footnotes.map((f) =>
      el("li", { value: Number(f.label) || undefined }, rich(f.text, true)))));
  } else {
    placeholder(els.refs, "No footnotes in this section.");
  }

  // Family tree pane: the characters in this section and how they are related. A character with a Wikipedia page is
  // described by its opening sentences, with a "Read more" link; the others by what the book says of them.
  if (a && a.annotations.characters.length) {
    const rels = a.annotations.relationships.filter((r) => state.outside || r.source === "text");
    els.tree.className = "";
    els.tree.replaceChildren(
      ...a.annotations.characters.map((c) => {
        const page = wikiPage(personNamed(c.name));
        return el("p", { className: "note" },
          el("span", { className: "label", textContent: [KIND_SHOWN.has(c.kind) ? c.kind : null, ...c.aliases].filter(Boolean).join(" \u00b7 ") }),
          " ", el("strong", {}, personLink(c.name)), ` \u2013 ${page ? page.lead : c.description}`,
          ...(page ? [" ", el("a", { href: page.url, target: "_blank", rel: "noopener", textContent: "Read more" })] : []));
      }),
      ...(rels.length ? [el("h4", { textContent: "Relationships:" }), ...rels.map((r) => el("p", {
        className: "note" + (r.source === "outside" ? " outside" : ""),
        textContent: `${r.a} ${r.relation.replace("_", " ")} ${r.b}${r.source === "outside" ? " (other myths)" : ""}` }))] : []));
  } else {
    placeholder(els.tree, a ? "No characters named here." : idle);
  }
}

// ---------- About this book: the first page of every book ----------

const ABOUT = "about";

// Who wrote the book, who translated it, and what Wikipedia says of the work and its author.
// A picture from Wikimedia Commons for a Wikipedia article (python -m annotate.images), with its credit line.
function picture(url) {
  const p = url && state.about && (state.about.pictures || {})[url];
  if (!p) return null;
  return el("figure", { className: "picture" },
    el("a", { href: p.page, target: "_blank", rel: "noopener", title: "Open on Wikimedia Commons" },
      el("img", { src: p.thumb, alt: "", loading: "lazy" })),
    el("figcaption", { textContent: [p.artist, p.license].filter(Boolean).join(", ") + " · Wikimedia Commons" }));
}

async function showAbout() {
  const slug = state.slug;
  let header = "";
  try { header = (await api(`books/${slug}/sections/${slug}-boilerplate-1`)).text; } catch { /* no header */ }
  if (!state.about) { try { state.about = await api(`books/${slug}/about`); } catch { /* none */ } }
  if (state.slug !== slug) return;
  const about = state.about || { works: {}, author: null };
  const field = (name) => {
    const line = header.split("\n").find((l) => l.startsWith(name + ":"));
    return line ? line.slice(name.length + 1).trim() : "";
  };
  state.current = { id: ABOUT, path: ["About this book"], text: "", footnotes: [], category: "about",
                    prev: null, next: state.ids[1] ? { id: state.ids[1] } : null };
  state.ann = null;
  state.selected = null;
  state.scene = 0;
  els.title.textContent = els.select.selectedOptions[0]?.textContent || slug;
  els.reference.hidden = true;
  const more = (url) => el("p", {}, el("a", { href: url, target: "_blank", rel: "noopener", textContent: "Read more" }));
  const fact = (label, value) => (value ? [el("p", { className: "facts" }, el("strong", { textContent: label + ": " }), value)] : []);
  const page = el("div", { className: "about" },
    ...fact("Author", field("Author")), ...fact("Translator", field("Translator")),
    ...fact("This edition", field("Source") ? field("Source").split(": ")[0] + ", " + (field("Licence") || "") : field("Release date") ? "Project Gutenberg, released " + field("Release date") : ""));
  const works = [...new Map(Object.values(about.works || {}).map((w) => [w.url, w])).values()];     // one block per article
  for (const work of works) {
    page.append(el("h3", { textContent: works.length > 1 ? work.title : "About the work" }), ...[picture(work.url)].filter(Boolean),
                el("p", { textContent: work.lead }));
    const when = work.history.find((h) => /dat(e|ing)|compos|origin|written/i.test(h.heading));
    if (when) page.append(el("p", {}, el("strong", { textContent: when.heading + ". " }), when.text));
    page.append(more(work.url));
    // Where the text comes from and what came of it, as Wikipedia describes them.
    const kinds = [["primary", "Primary: how the text itself survives"], ["secondary", "Secondary: what it drew on, scholarship and translations"],
                   ["tertiary", "Tertiary: retellings and adaptations"]];
    // The article on Homer, not the one on the Odyssey, tells how Homer's text survives.
    const from = (kind) => ((work[kind] || []).length ? work[kind] : kind === "primary" && about.author ? about.author.primary || [] : []);
    if (kinds.some(([kind]) => from(kind).length)) page.append(el("h3", { textContent: works.length > 1 ? `Sources for ${work.title}` : "Sources" }));
    for (const [kind, label] of kinds) {
      const items = from(kind);
      if (items.length) page.append(el("p", { className: "facts" }, el("strong", { textContent: label })), sourcedGroup(`${kind}:${work.title}`, items, items[0].url.split("#")[0]));
    }
  }
  if (about.author) {
    page.append(el("h3", { textContent: "About " + about.author.name }), ...[picture(about.author.url)].filter(Boolean),
                el("p", { textContent: about.author.lead }), more(about.author.url));
  }
  if (!works.length && !about.author) {
    page.append(el("p", { className: "facts", textContent: "No background has been fetched for this book yet (python -m annotate.about)." }));
  }
  els.text.replaceChildren(page);
  renderPanes();
  renderScene();
  drawMap();
  loadTree();
  setPager(state.current);
  markActive(ABOUT);
  store.set("last", { slug: state.slug });
  store.set("pos:" + state.slug, ABOUT);
  document.querySelector(".reader").scrollTop = 0;
}

// Wikipedia's sections on a subject as a row of small buttons; the chosen one's text shows underneath. Which
// button is open is remembered, because the panes are drawn again whenever the scene changes.
function sourcedGroup(key, items, url) {
  const text = el("p", { className: "note" });
  const link = el("a", { href: url, target: "_blank", rel: "noopener", textContent: "Read more" });
  const show = (item) => {
    state.pills[key] = item ? item.heading : null;
    for (const pill of pills) pill.setAttribute("aria-pressed", String(!!item && pill.textContent === item.heading));
    text.hidden = !item;
    text.textContent = item ? item.text : "";
    link.href = item ? item.url : url;
    link.textContent = wikiCite(link.href);       // the section quoted: "Wikipedia: Odyssey § Structure"
  };
  const pills = items.map((item) => {
    const pill = el("button", { type: "button", className: "pill", textContent: item.heading });
    pill.addEventListener("click", () => show(state.pills[key] === item.heading ? null : item));
    return pill;
  });
  show(items.find((item) => item.heading === state.pills[key]) || null);
  return el("div", {}, el("div", { className: "pills" }, ...pills), text, el("p", { className: "source" }, link));
}

// ---------- Sourced background: the translator's summary, Wikipedia and Wikidata ----------

async function loadAbout(slug) {
  let about = null;
  try { about = await api(`books/${slug}/about`); } catch { /* none */ }
  if (state.slug !== slug) return;
  state.about = about;
  if (state.current) { renderPanes(); renderScene(); }
  // A profile opened before this arrived (a search result in another book) is drawn again, now with its picture.
  const info = $("person-info");
  if ($("person-dialog").open && info.dataset.place) openPlace(info.dataset.place);
  else if ($("person-dialog").open && info.dataset.item) openItem(info.dataset.item);
  else if ($("person-dialog").open && info.dataset.person && state.treeData) openProfile(info.dataset.person);
}

// Wikipedia's article on the work the section being read belongs to, or null.
function workAbout() {
  const about = state.about;
  return about && state.current ? about.works[about.sections[state.current.id]] || null : null;
}

// How a heading names the work: "the work" when the book is one work, its Wikipedia title when it holds several.
const workName = (work) => (Object.keys(state.about.works).length > 1 ? work.title : "the work");

// A summary printed in capitals, as ordinary text. A word keeps its capital if the section's own text always
// writes it with one (Minerva, Ithaca), or if it is part of a character's name.
function normalCase(summary, s) {
  const word = /[A-Za-zÀ-ÿ]+/g;
  const body = s.text.split(/\n\s*\n/).slice(1).join(" ").match(word) || [];
  const lower = new Set(body.filter((w) => w[0] === w[0].toLowerCase()));
  const proper = new Map();
  for (const w of body) if (w[0] !== w[0].toLowerCase() && !lower.has(w.toLowerCase())) proper.set(w.toLowerCase(), w);
  for (const p of cast()) {
    for (const w of [p.name, ...p.aliases].join(" ").match(word) || []) if (w[0] !== w[0].toLowerCase()) proper.set(w.toLowerCase(), w);
  }
  const text = summary.replace(word, (w) => proper.get(w.toLowerCase()) || w.toLowerCase()).replace(/—-+/g, "—");
  return text[0].toUpperCase() + text.slice(1);
}

// The translator's own summary at the head of the section (an "argument" in capitals, or an indented paragraph);
// otherwise Wikipedia's summary of this section where its article has one. A summary of the whole book (Ovid's
// Book 1) is shown as well as the translator's summary of one fable in it.
function sourcedSummaries() {
  const s = state.current, out = [];
  if (!s) return out;
  const first = s.text.split(/\n\s*\n/)[0].trim();
  const letters = first.replace(/[^A-Za-z]/g, "");
  const capitals = letters.length > 20 && letters === letters.toUpperCase() && first.length < 700;
  const work = workAbout();
  const number = Number((s.path[0].match(/\d+/) || [])[0]);
  const fromWiki = !work ? [] : work.summaries.filter((x) =>
    (x.sections || []).includes(s.id) || (number && x.books.includes(number)));
  const whole = fromWiki.length && (s.path.length === 1 || fromWiki.some((x) => (x.sections || []).includes(s.id)));
  const own = capitals || hasSummary(s) || s.argument ? first.replace(/\s+/g, " ").replace(/\[\d+\]/g, "") : "";
  // A summary in full sentences is the translator's own account. One printed in capitals, or made of phrases joined
  // by dashes (Butler's), is a heading: Wikipedia's summary replaces it where there is one.
  const sentences = own && !capitals && !own.includes("\u2014") && own.split(" ").length >= 8 && /[.!?]["'\u201d\u2019)]*$/.test(own);
  if (own && (sentences || !whole)) {
    out.push(el("p", { className: "note" }, el("span", { className: "label", textContent: "Translator\u2019s summary:" }),
      " ", rich(capitals ? normalCase(own, s) : own)));
  }
  if (fromWiki.length && !(sentences && whole)) {
    // A long summary (one Wikipedia paragraph can cover several books) shows its opening, with a button for the rest.
    const full = fromWiki.map((x) => x.text).join(" ");
    const long = full.length > SUMMARY_SHOWN + 150;
    const span = fromWiki.length === 1 ? fromWiki[0].books : [];        // one Wikipedia section can cover several books
    const open = !long || state.summaryOpen === s.id;
    const cut = full.slice(0, SUMMARY_SHOWN), stop = Math.max(cut.lastIndexOf(". "), cut.lastIndexOf("? "), cut.lastIndexOf("! "));
    const shown = open ? full : stop > 100 ? cut.slice(0, stop + 1) : cut.slice(0, cut.lastIndexOf(" ")) + "…";
    const more = el("button", { type: "button", className: "link", textContent: open ? "Show less" : "Show all" });
    more.addEventListener("click", () => { state.summaryOpen = open ? null : s.id; renderScene(); });
    out.push(el("p", { className: "note" }, el("span", { className: "label", textContent: !whole ? `In ${s.path[0]}:` : span.length > 1 ? `Summary of Books ${span[0]}–${span[span.length - 1]}:` : "Summary:" }),
      " " + shown + " ", ...(long ? [more, " · "] : []),
      el("a", { href: fromWiki[0].url, target: "_blank", rel: "noopener", textContent: wikiCite(fromWiki[0].url) })));
  }
  return out;
}

const SUMMARY_SHOWN = 400;    // characters of a long Wikipedia summary shown before "Show all"

// The people of the scene being read (or of the section, for books without scenes) who have a Wikidata entry.
function scenePeople() {
  const about = state.about, a = state.ann, tree = state.treeData;
  if (!about || !a || !tree) return [];
  const scene = scenes()[state.scene];
  const names = scene ? scene.present : a.annotations.characters.map((c) => c.name);
  const seen = new Set(), out = [];
  for (const name of names) {
    const person = personNamed(name);
    const entry = person && (person.wikidata || []).map((q) => about.characters[q]).find((c) => c && c.description);
    if (entry && !seen.has(person.id)) {
      seen.add(person.id);
      out.push({ name: person.name, description: entry.description, url: entry.url });
    }
  }
  return out;
}

// ---------- Summary, scenes and the map ----------

const scenes = () => (state.ann && state.ann.annotations.scenes) || [];
const onMap = (place) => place.lat !== null && place.lat !== undefined;

function sceneAt(i) {
  const n = scenes().findIndex((sc) => i >= sc.start_sentence && i <= sc.end_sentence);
  return n >= 0 ? n : 0;
}

function placeText(place) {
  const modern = place.modern_name && place.modern_name !== place.name
    ? ` (${place.certainty === "traditional" ? "traditionally " : ""}${place.modern_name})` : "";
  return place.name + modern + (place.certainty === "mythical" ? " \u2013 a mythical place" : "");
}

// The section's summary, the scene being read (where, what is at stake, who is there) and the list of scenes.
function renderScene() {
  const a = state.ann, list = scenes();
  const summaries = sourcedSummaries();
  if (!summaries.length && !list.length) {
    return placeholder(els.scene, a ? "No summary or scenes for this section."
      : state.current.category !== "body" ? "Open a part of the story to see its summary and scenes." : "Not annotated yet.");
  }
  const sc = list[state.scene], parts = [];
  parts.push(...summaries);
  if (sc) {
    parts.push(el("h4", { textContent: list.length > 1 ? `Scene ${state.scene + 1} of ${list.length}: ${sc.title}` : sc.title }),
      el("p", { className: "note" }, ...withPeople(sc.summary, sc.present)));
    if (sc.place && sc.place.name) parts.push(el("p", { className: "note" }, el("span", { className: "label", textContent: "Where:" }), " ",
      placeLink(sc.place.name, placeText(sc.place)), sc.told_as_story ? " \u2013 told as a story" : ""));
    if (sc.stakes) parts.push(el("p", { className: "note" }, el("span", { className: "label", textContent: "At stake:" }), " ", ...withPeople(sc.stakes, sc.present)));
    if (sc.present.length) {
      const names = sc.present.flatMap((name, k) => [k ? ", " : " ", personLink(name)]);
      parts.push(el("p", { className: "note" }, el("span", { className: "label", textContent: "Present:" }), ...names));
    }
  }
  if (list.length > 1) {
    parts.push(el("h4", { textContent: "Scenes:" }), el("ol", { className: "scene-list" }, ...list.map((s, n) => {
      const link = el("button", { type: "button", className: "link scene-link" + (n === state.scene ? " current" : ""), textContent: s.title });
      link.addEventListener("click", () => goToScene(n));
      return el("li", {}, link);
    })));
  }
  els.scene.className = "";
  els.scene.replaceChildren(...parts);
}

// A person's Wikipedia page with its opening sentences, if the Wikidata check matched them to one.
function wikiPage(person) {
  const pages = (state.about && state.about.characters) || {};
  return person ? (person.wikidata || []).map((q) => pages[q]).find((c) => c && c.url && c.lead) || null : null;
}

// Everyone in the book: the people of the family tree, and those with no family link (who are not drawn in it).
const cast = () => (state.treeData ? [...state.treeData.people, ...(state.treeData.others || [])] : []);

// The person of the book that a name belongs to, or undefined.
function personNamed(name) {
  const key = name.toLowerCase();
  return cast().find((p) => p.name.toLowerCase() === key || p.aliases.some((x) => x.toLowerCase() === key));
}

// A character's name as a link to their profile: it opens their details under the family tree.
function personLink(name) {
  const person = personNamed(name);
  if (!person) return name;
  const link = el("button", { type: "button", className: "link person-link", textContent: name, title: "Who is this?" });
  link.addEventListener("click", () => openProfile(person.id));
  return link;
}

// A character's profile, in a window of its own.
function openProfile(id) {
  showPerson($("person-info"), id, { ...state.treeData, people: cast() }, false);
  const dialog = $("person-dialog");
  if (!dialog.open) dialog.showModal();
  dialog.scrollTop = 0;
}

// A sentence with the names of the people in it turned into profile links.
function withPeople(text, names) {
  const known = [...new Set(names)].filter((n) => personNamed(n)).sort((x, y) => y.length - x.length);
  const nodes = [];
  let rest = text;
  while (rest) {
    let at = -1, found = "";
    for (const n of known) {          // the earliest name in what is left; the longer name wins a tie
      const k = rest.indexOf(n);
      if (k >= 0 && (at < 0 || k < at)) { at = k; found = n; }
    }
    if (at < 0) { nodes.push(rest); break; }
    if (at) nodes.push(rest.slice(0, at));
    nodes.push(personLink(found));
    rest = rest.slice(at + found.length);
  }
  return nodes;
}

function setScene(n) {
  if (n === state.scene) return;
  state.scene = n;
  renderScene();
  renderPanes();
  drawMap();
}

function goToScene(n) {
  const sc = scenes()[n];
  if (!sc) return;
  // Smooth scrolling passes other scenes on the way: ignore them until the scrolling stops (or, failing that, 2.5 s).
  state.sceneLock = Date.now() + 2500;
  for (const target of [window, document.querySelector(".reader")]) {
    target.addEventListener("scrollend", () => { state.sceneLock = 0; }, { once: true });
  }
  els.text.querySelector(`.s[data-i="${sc.start_sentence}"]`)?.scrollIntoView({ block: "start", behavior: "smooth" });
  setScene(n);
}

// The scene on screen is the one holding the first sentence below the top of the reading column.
function trackScene() {
  if (scenes().length < 2 || Date.now() < (state.sceneLock || 0)) return;
  const top = Math.max(document.querySelector(".reader").getBoundingClientRect().top, 0) + 24;
  for (const span of els.text.querySelectorAll(".s")) {
    if (span.getBoundingClientRect().bottom > top) return setScene(sceneAt(Number(span.dataset.i)));
  }
}

// ---------- Places ----------

// The book's places (python -m annotate.places): Tartarus, Olympus, Ithaca, Ulysses' house. Each has a profile.
async function loadPlaces(slug) {
  let places = [];
  try { places = await api(`books/${slug}/places`); } catch { /* not made for this book yet */ }
  if (state.slug !== slug) return;
  state.places = places;
  if (state.current) { renderScene(); drawMap(); }      // their names become links
  const wanted = state.pendingPlace;                   // a search result in another book
  if (wanted && wanted.book === slug) { state.pendingPlace = null; openPlace(wanted.id); }
}

// The book's notable things (python -m annotate.items): the Golden Fleece, the aegis, Achilles' shield. Each has a
// profile like a place's.
async function loadItems(slug) {
  let items = [];
  try { items = await api(`books/${slug}/items`); } catch { /* not made for this book yet */ }
  if (state.slug !== slug) return;
  state.items = items;
  const wanted = state.pendingItem;                    // a search result in another book
  if (wanted && wanted.book === slug) { state.pendingItem = null; openItem(wanted.id); }
}

// The place of the book that a name (as a scene gives it, or another of its names) belongs to, or undefined.
function placeNamed(name) {
  const key = (name || "").trim().toLowerCase();
  return key ? (state.places || []).find((p) => p.name.toLowerCase() === key || p.names.some((x) => x.toLowerCase() === key)) : undefined;
}

// A place's name as a link to its profile; plain text where it has none.
function placeLink(name, text) {
  const place = placeNamed(name);
  if (!place) return text;
  const link = el("button", { type: "button", className: "link person-link", textContent: text, title: "About this place" });
  link.addEventListener("click", () => openPlace(place.id));
  return link;
}

const CERTAINTY = { real: "real", traditional: "legendary", mythical: "mythical" };

// Each drawing of a profile has a number. A profile can be drawn twice in a row (when a book's list of places or
// things comes in, then again when its pictures do), and an answer that arrives for an older drawing is dropped,
// so that "Also in:" or the key moments are not added twice.
let renders = 0;

// The other names worth listing under "Also called": not the name itself in other letters ("Laius" for Laïus,
// "the golden fleece" for the Golden Fleece), and each only once.
const plainName = (n) => n.normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/æ/gi, "ae").replace(/œ/gi, "oe")
  .toLowerCase().replace(/^the\s+/, "").trim();
function otherNames(name, names) {
  const seen = new Set([plainName(name)]);
  return names.filter((n) => !seen.has(plainName(n)) && seen.add(plainName(n)));
}

// A profile's "Also called" row: the names other books give the being first, then the book's own (see showPerson).
function fillCalled(row, name, fromOtherBooks = []) {
  const names = otherNames(name, [...fromOtherBooks, ...JSON.parse(row.dataset.names || "[]")]).slice(0, 6);
  row.hidden = !names.length;
  row.replaceChildren(row.firstChild, " " + names.join(", "));
}

function openPlace(id) { openThing("place", id); }
function openItem(id) { openThing("item", id); }

// A place's or a thing's profile, in the same window as a character's: what it is, its picture and Wikipedia's
// account, who owns a thing, the scenes set at a place or telling of a thing, and the sections that name it (each a
// link that opens the section there).
function openThing(type, id) {
  const p = ((type === "place" ? state.places : state.items) || []).find((x) => x.id === id);
  if (!p) return;
  const info = $("person-info");
  info.dataset.person = "";                 // so that a character's references still loading do not land here
  const rows = [];
  const add = (label, text) => { if (text) rows.push(el("p", { className: "note" }, el("span", { className: "label", textContent: label + ":" }), " " + text)); };
  const pic = p.wikipedia && picture(p.wikipedia.url);
  if (pic) rows.push(pic);
  add("What", type === "place" ? `${p.kind === "other" ? "place" : p.kind} · ${CERTAINTY[p.certainty] || p.certainty}`
                                  : (p.kind === "other" ? "thing" : p.kind));
  add("In the book", p.description);
  if ((p.owners || []).length) {
    rows.push(el("p", { className: "note" }, el("span", { className: "label", textContent: "Owned by:" }),
      ...p.owners.flatMap((name, k) => [k ? ", " : " ", personLink(name)])));
  }
  add("Also called", otherNames(p.name, p.aliases || p.names || []).slice(0, 6).join(", "));
  if (p.wikipedia) {
    rows.push(el("p", { className: "note" }, el("span", { className: "label", textContent: "Wikipedia:" }), " " + p.wikipedia.lead + " ",
      el("a", { href: p.wikipedia.url, target: "_blank", rel: "noopener", textContent: "Read more" })));
  }
  const names = [p.name, ...p.names];
  const item = (m, label, after = "") => {
    const a = el("a", { href: `#${state.slug}/${m.section}`, textContent: label });
    a.addEventListener("click", () => {
      state.find = { section: m.section, scene: m.scene ?? null, names };
      $("person-dialog").close();
      if (m.section === state.current.id) showFound();      // already on that section: no reload
    });
    return el("li", {}, a, after);
  };
  if (p.scenes.length) {
    rows.push(el("h4", { textContent: type === "place" ? "Scenes set here:" : "Scenes it appears in:" }),
      el("ul", { className: "moments" }, ...p.scenes.map((m) => item(m, `${m.title} › ${m.scene_title}`))));
  }
  if (p.named.length) {
    const total = p.named.reduce((s, n) => s + n.count, 0);
    rows.push(el("details", { className: "book-refs" },
      el("summary", { textContent: `Named in ${p.named.length} section${p.named.length > 1 ? "s" : ""} · ${total} time${total > 1 ? "s" : ""}` }),
      el("ul", { className: "moments" }, ...p.named.map((m) => item(m, m.title, ` (${m.count})`)))));
  }
  info.className = "tree-info";
  info.dataset.place = type === "place" ? id : "";
  info.dataset.item = type === "item" ? id : "";
  info.dataset.render = ++renders;
  info.replaceChildren(el("strong", { textContent: p.name }), ...rows);
  loadElsewhere(info, type, id, p.name);
  const dialog = $("person-dialog");
  if (!dialog.open) dialog.showModal();
  dialog.scrollTop = 0;
}

// "Also in:" the same character, or the same place, in the other books, each a link to its profile there.
// Added under a profile once the server answers.
async function loadElsewhere(info, type, id, name) {
  let others = [];
  const render = info.dataset.render;
  try { others = await api(`books/${state.slug}/elsewhere?type=${type}&id=${encodeURIComponent(id)}`); } catch { return; }
  if (info.dataset.render !== render || info.dataset[type] !== id) return;   // closed, moved on or drawn again
  const unreferenced = info.querySelector(":scope > p.unreferenced");
  if (unreferenced && !others.length) unreferenced.textContent = "Not referenced in any of the books on this site.";
  if (!others.length) return;
  const links = others.flatMap((o, k) => {
    // Another book's name for it, where that differs: "The Iliad (as Jove)".
    const label = o.book === state.slug ? o.name : o.book_title + (o.name !== name ? ` (as ${o.name})` : "");
    const a = el("a", { href: `#${o.book}`, textContent: label });
    a.addEventListener("click", (e) => { e.preventDefault(); openResult(o); });
    return [k ? ", " : " ", a];
  });
  const called = info.querySelector(":scope > p.called");
  if (called) fillCalled(called, name, others.map((o) => o.name));     // "Jove", "Jupiter"
  const row = el("p", { className: "note" }, el("span", { className: "label", textContent: "Also in:" }), ...links);
  const after = info.querySelector(":scope > p.note:last-of-type");     // with the facts, before the scenes and key moments
  after ? after.after(row) : info.append(row);
}

// Every scene of the book with its place, so the map can also show where the rest of the story goes.
async function loadJourney(slug) {
  let stops = [];
  try { stops = await api(`books/${slug}/journey`); } catch { /* nothing annotated yet */ }
  if (state.slug !== slug) return;
  state.journey = stops;
  if (state.current) drawMap();
}

// ---------- Light and dark colours ----------

// Dark if the reader chose it with the button, or made no choice and the system asks for dark.
const isDark = () => {
  const chosen = document.documentElement.dataset.theme;
  return chosen ? chosen === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
};

// Map tiles from MapTiler (OpenStreetMap data; free plan, key limited to pantheonpal.com and localhost), in a
// light or dark version to match the page.
const MAPTILER_KEY = "4j2aT4ZayjiQygOJZDWQ";

function mapTiles() {
  if (!state.map) return;
  for (const layer of state.mapTiles || []) layer.remove();
  const style = isDark() ? "dataviz-dark" : "dataviz";
  state.mapTiles = [L.tileLayer(`https://api.maptiler.com/maps/${style}/256/{z}/{x}/{y}.png?key=${MAPTILER_KEY}`, {
    maxZoom: 12, attribution: '<a href="https://www.maptiler.com/copyright/" target="_blank" rel="noopener">&copy; MapTiler</a> '
      + '<a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">&copy; OpenStreetMap contributors</a>' }).addTo(state.map)];
  for (const layer of state.mapTiles) layer.bringToBack();
}

// The button names the colours a click switches to. The choice is remembered; until one is made the page follows the system.
function showTheme() {
  $("theme-toggle").textContent = isDark() ? "Light mode" : "Dark mode";
  mapTiles();
}

$("theme-toggle").addEventListener("click", () => {
  const theme = isDark() ? "light" : "dark";
  document.documentElement.dataset.theme = theme;
  store.set("theme", theme);
  showTheme();
});
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", showTheme);
showTheme();

function drawMap() {
  const list = scenes();
  const elsewhere = (state.journey || []).filter((p) => onMap(p) && p.section !== state.current.id);
  // Scenes at the same spot share one pin, labelled with their numbers ("4-8").
  const pins = new Map();
  list.forEach((sc, n) => {
    if (!onMap(sc.place)) return;
    const key = sc.place.lat + "," + sc.place.lon;
    if (!pins.has(key)) pins.set(key, { lat: sc.place.lat, lon: sc.place.lon, scenes: [] });
    pins.get(key).scenes.push(n);
  });
  const offMap = [...new Set(list.filter((sc) => !onMap(sc.place) && sc.place.name).map((sc) => sc.place.name))];
  const note = [];
  if (state.current.category !== "body") note.push(elsewhere.length ? "Every place the book visits." : "");
  else if (!state.ann) note.push("Not annotated yet.");
  else if (!list.length) note.push("This section was annotated before places were recorded.");
  else if (!pins.size) note.push("No place in this section can be put on a map.");
  if (offMap.length) note.push(["Not on the map: ", ...offMap.flatMap((n, k) => [k ? ", " : "", placeLink(n, n)]), "."]);
  if (typeof L === "undefined") note.push("The map needs the Leaflet library, which loads from the internet.");
  els.mapNote.replaceChildren(...note.flatMap((part, k) => [k ? " " : "", ...[part].flat()]));
  els.map.hidden = typeof L === "undefined" || (!pins.size && !elsewhere.length);
  if (els.map.hidden || !els.mapPane.open) return;

  if (!state.map) {
    state.map = L.map(els.map, { worldCopyJump: true });
    mapTiles();
    state.mapLayer = L.layerGroup().addTo(state.map);
  }
  state.map.invalidateSize();
  state.mapLayer.clearLayers();
  const seen = new Set(pins.keys());
  for (const p of elsewhere) {
    const key = p.lat + "," + p.lon;
    if (seen.has(key)) continue;
    seen.add(key);
    L.circleMarker([p.lat, p.lon], { radius: 4, weight: 1, color: "#8b8a85", fillColor: "#8b8a85", fillOpacity: 0.5 })
      .bindTooltip(`${p.name} (${state.titles.get(p.section) || p.section_title})`).addTo(state.mapLayer);
  }
  const route = list.filter((sc) => onMap(sc.place)).map((sc) => [sc.place.lat, sc.place.lon]);
  if (pins.size > 1) L.polyline(route, { color: "#c8763f", weight: 2, opacity: 0.8, dashArray: "4 6" }).addTo(state.mapLayer);
  let currentAt = null;
  for (const pin of pins.values()) {
    const current = pin.scenes.includes(state.scene);
    const first = pin.scenes[0], last = pin.scenes[pin.scenes.length - 1];
    const label = first === last ? String(first + 1) : `${first + 1}-${last + 1}`;
    if (current) currentAt = [pin.lat, pin.lon];
    L.marker([pin.lat, pin.lon], { zIndexOffset: current ? 1000 : 0, keyboard: false,
      icon: L.divIcon({ className: "map-pin" + (current ? " current" : ""), html: label, iconSize: [label.length > 2 ? 40 : 22, 22] }) })
      .bindTooltip(pin.scenes.map((n) => `${n + 1}. ${list[n].title}`).join("<br>") + `<br><i>${placeText(list[first].place)}</i>`)
      .on("click", () => goToScene(current && pin.scenes.includes(state.scene + 1) ? state.scene + 1 : first))
      .addTo(state.mapLayer);
  }
  // Frame the section's places when the section changes; after that only follow the current scene if it leaves the view.
  const frame = state.slug + "/" + state.current.id + "/" + (state.journey ? 1 : 0);
  if (state.mapFrame !== frame) {
    state.mapFrame = frame;
    const points = route.length ? route : elsewhere.map((p) => [p.lat, p.lon]);
    if (points.length === 1) state.map.setView(points[0], 6);
    else state.map.fitBounds(points, { padding: [24, 24], maxZoom: 8 });
  } else if (currentAt && !state.map.getBounds().contains(currentAt)) {
    state.map.panTo(currentAt);
  }
}

// ---------- Family tree (Cytoscape.js) ----------

// Only gods and fantasy beings are labelled in the character list; "mortal", "other" and "place" are left off.
const KIND_SHOWN = new Set(["god", "goddess", "nymph", "monster", "creature"]);
const KIND_COLOR = { god: "#2f6f9f", goddess: "#2f6f9f", nymph: "#3f8f6b", monster: "#a33a3a", creature: "#a33a3a", mortal: "#b5651d" };
const kindColor = (kind) => KIND_COLOR[kind] || "#7b6d8d";

async function loadTree() {
  const token = ++state.treeToken;
  let data = null;
  try {
    const upto = state.current.category === "body" ? `upto=${state.current.id}&` : "";      // outside the story: the whole book's tree
    data = await api(`books/${state.slug}/tree?${upto}outside=${state.outside}`);
    // Lovers are not drawn in the tree, but a profile lists them.
    state.loverData = await api(`books/${state.slug}/tree?${upto}outside=${state.outside}&relations=lover_of`).catch(() => null);
  } catch { /* this book has no tree yet */ }
  if (token !== state.treeToken) return;      // a newer request has taken over
  state.treeData = data;
  drawBoth();
  renderPanes();
  renderScene();          // the names in the scene card link to people of the tree
  const wanted = state.pendingProfile;        // a search result in another book: open it now that its book is here
  if (data && wanted && wanted.book === state.slug) { state.pendingProfile = null; openProfile(wanted.id); }
}

const DEPTH_ALL = 7;

// Keep only the people within `depth` connections of the characters in the section being read.
function limitDepth(data, depth) {
  if (!data || depth >= DEPTH_ALL) return { data, shown: null };
  const start = data.current.filter((id) => data.people.some((p) => p.id === id));
  if (!start.length) return { data, shown: null };       // nobody here has links yet: show everything
  const near = new Map(start.map((id) => [id, 0]));
  const queue = [...start];
  while (queue.length) {
    const id = queue.shift(), d = near.get(id);
    if (d >= depth) continue;
    for (const r of data.relationships) {
      const other = r.a === id ? r.b : r.b === id ? r.a : null;
      if (other && !near.has(other)) { near.set(other, d + 1); queue.push(other); }
    }
  }
  const people = data.people.filter((p) => near.has(p.id));
  const relationships = data.relationships.filter((r) => near.has(r.a) && near.has(r.b));
  return { data: { ...data, people, relationships }, shown: [people.length, data.people.length] };
}

const SIMPLIFY_ABOVE = 30;    // the connection limit applies to trees with more people than this

// By default everyone's children are collapsed, so only the characters of the section being read and their ancestors
// are drawn; a person's other children stay behind a "show children" button. A person the reader has clicked open or
// shut follows that click.
function isCollapsed(id) {
  if (state.collapse.has(id)) return state.collapse.get(id);
  return state.autoCollapse;
}

function toggleChildren(id) {
  state.collapse.set(id, !isCollapsed(id));
  drawBoth();
}

// Hide the children of collapsed people, and anyone descended from a hidden child. The section's characters and
// everyone above them always stay, so each lineage still joins up. collapsedCounts says how many children each
// person has hidden.
function applyCollapse(data) {
  if (!data) return data;
  const kids = new Map(), parents = new Map();
  for (const r of data.relationships) {
    if (r.relation !== "parent_of") continue;
    if (!kids.has(r.a)) kids.set(r.a, []);
    if (!parents.has(r.b)) parents.set(r.b, []);
    kids.get(r.a).push(r.b);
    parents.get(r.b).push(r.a);
  }
  const keep = new Set(data.current), hidden = new Set();
  for (const queue = [...keep]; queue.length;) {
    for (const q of parents.get(queue.pop()) || []) if (!keep.has(q)) { keep.add(q); queue.push(q); }
  }
  const closed = (q) => hidden.has(q) || isCollapsed(q);                 // collapsed by default, by a click, or itself hidden
  const shut = (q) => hidden.has(q) || state.collapse.get(q) === true;   // hidden by the reader's own "Hide children" click
  for (let changed = true; changed;) {
    changed = false;
    for (const p of data.people) {
      const ps = parents.get(p.id) || [];
      if (hidden.has(p.id) || keep.has(p.id) || !ps.length) continue;
      // "Hide children" hides all of someone's children; a default collapse hides a child only when none of its parents is open.
      if (ps.some(shut) || ps.every(closed)) {
        hidden.add(p.id);
        changed = true;
      }
    }
  }
  // Someone outside the section's lineage whose relatives are all hidden would just float on their own: hide them too.
  for (let changed = true; changed;) {
    changed = false;
    for (const p of data.people) {
      if (hidden.has(p.id) || keep.has(p.id)) continue;
      const linked = data.relationships.some((r) => (r.a === p.id && !hidden.has(r.b)) || (r.b === p.id && !hidden.has(r.a)));
      if (!linked) { hidden.add(p.id); changed = true; }
    }
  }
  const counts = new Map();
  for (const [id, list] of kids) {
    const n = list.filter((c) => hidden.has(c)).length;
    if (n) counts.set(id, n);
  }
  return { ...data, people: data.people.filter((p) => !hidden.has(p.id)),
           relationships: data.relationships.filter((r) => !hidden.has(r.a) && !hidden.has(r.b)), collapsedCounts: counts };
}

const ABOUT_TREE = 30;        // the About page draws this many people: those with the most family links

// The best-connected people of the book and the links between them, for the About page (no section is being read).
function mostConnected(data, count) {
  const degree = new Map();
  for (const r of data.relationships) for (const id of [r.a, r.b]) degree.set(id, (degree.get(id) || 0) + 1);
  const top = new Set([...degree].sort((x, y) => y[1] - x[1] || (x[0] < y[0] ? -1 : 1)).slice(0, count).map(([id]) => id));
  return { ...data, people: data.people.filter((p) => top.has(p.id)),
           relationships: data.relationships.filter((r) => top.has(r.a) && top.has(r.b)) };
}

function drawBoth() {
  if (state.treeData && state.current && state.current.category !== "body") {
    const data = mostConnected(state.treeData, ABOUT_TREE);
    els.count.textContent = data.people.length < state.treeData.people.length
      ? `The ${data.people.length} people with the most family links, of ${state.treeData.people.length}. Open a section to follow its characters.` : "";
    if (els.treePane.open) state.cy = drawTree(els.graph, els.info, state.cy, data, 11, state.treeData, els.info.dataset.person);
    else if (state.cy) { state.cy.destroy(); state.cy = null; }
    if (els.treeDialog.open) state.cyBig = drawTree(els.graphBig, els.infoBig, state.cyBig, data, 14, state.treeData, els.infoBig.dataset.person);
    return;
  }
  const base = applyCollapse(state.treeData);
  // The connection limit applies to big trees, or to any tree once the reader has set the slider themselves.
  const limit = state.depthSet || (state.treeData && state.treeData.people.length > SIMPLIFY_ABOVE);
  const { data, shown } = limit ? limitDepth(base, state.depth) : { data: base, shown: null };
  const parts = [];
  if (base && base.people.length < state.treeData.people.length) {
    parts.push(`${state.treeData.people.length - base.people.length} hidden in collapsed families`);
  }
  if (shown && shown[0] < shown[1]) {
    parts.push(`showing ${shown[0]} of ${shown[1]} people within ${state.depth} connection${state.depth > 1 ? "s" : ""} of this section's characters`);
  }
  els.count.textContent = parts.length ? parts.join("; ").replace(/^./, (c) => c.toUpperCase()) + "." : "";
  const keepSmall = els.info.dataset.person, keepBig = els.infoBig.dataset.person;
  if (els.treePane.open) state.cy = drawTree(els.graph, els.info, state.cy, data, 11, state.treeData, keepSmall);
  else if (state.cy) { state.cy.destroy(); state.cy = null; }       // folded away: draw it again when it is opened
  if (els.treeDialog.open) state.cyBig = drawTree(els.graphBig, els.infoBig, state.cyBig, data, 14, state.treeData, keepBig);
}

function setDepth(value, user = true) {
  state.depth = value;
  if (user) { state.depthSet = true; store.set("depth-user", value); }
  for (const [input, label] of [[els.depth, els.depthLabel], [els.depthBig, els.depthBigLabel]]) {
    input.value = value;
    label.textContent = value >= DEPTH_ALL ? "All" : String(value);
  }
  if (state.treeData) drawBoth();
}

// A family-chart layout. The two parents of a child, and two people linked as spouses or lovers, meet at an invisible
// point on the row between them, and their children hang from that point: couples sit side by side and children sit
// under both parents. Spouse and other links are not used to place people, only drawn between them afterwards.
function layoutFamily(part) {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "TB", nodesep: 14, ranksep: 19 });      // half the old row gap: a couple's point takes a row of its own
  g.setDefaultEdgeLabel(() => ({}));
  part.nodes().forEach((n) => { const b = n.boundingBox({ includeLabels: true }); g.setNode(n.id(), { width: b.w, height: b.h }); });
  const couples = new Map();
  const couple = (a, b) => {
    const key = [a, b].sort().join("\n");
    if (!couples.has(key)) {
      couples.set(key, "couple:" + key);
      g.setNode("couple:" + key, { width: 1, height: 1 });
      for (const q of [a, b]) g.setEdge(q, "couple:" + key, { minlen: 1, weight: 3 });
    }
    return couples.get(key);
  };
  const parents = new Map();
  part.edges('[relation = "parent_of"]').forEach((e) => {
    if (!parents.has(e.target().id())) parents.set(e.target().id(), []);
    parents.get(e.target().id()).push(e.source().id());
  });
  for (const [child, ps] of parents) {
    if (ps.length >= 2) g.setEdge(couple(ps[0], ps[1]), child, { minlen: 1, weight: 2 });
    for (const p of ps.slice(ps.length >= 2 ? 2 : 0)) g.setEdge(p, child, { minlen: 2, weight: 1 });   // a lone parent skips the couple row
  }
  part.edges('[relation = "spouse_of"], [relation = "lover_of"]').forEach((e) => {
    if (e.source().id() !== e.target().id()) couple(e.source().id(), e.target().id());
  });
  dagre.layout(g);
  part.nodes().forEach((n) => { const p = g.node(n.id()); n.position({ x: p.x, y: p.y }); });
}

// Lay out each separate family on its own, largest first, then set them in rows: of a few row widths, keep the one
// that lets the whole tree be drawn largest in the window, instead of one long strip.
function layoutFamilies(cy) {
  const parts = cy.elements().components().sort((x, y) => y.nodes().length - x.nodes().length);
  parts.forEach(layoutFamily);
  const gap = 24, boxes = parts.map((p) => p.boundingBox());
  const place = (rowWidth) => {
    let x = 0, y = 0, rowHeight = 0, w = 0;
    const at = boxes.map((b) => {
      if (x > 0 && x + b.w > rowWidth) { x = 0; y += rowHeight + gap; rowHeight = 0; }
      const spot = [x, y];
      x += b.w + gap; w = Math.max(w, x - gap); rowHeight = Math.max(rowHeight, b.h);
      return spot;
    });
    return { at, scale: Math.min(cy.width() / w, Math.max(cy.height(), 1) / (y + rowHeight)) };
  };
  const widest = Math.max(...boxes.map((b) => b.w)), total = boxes.reduce((s, b) => s + b.w + gap, 0);
  let best = null;
  for (let i = 0; i <= 20; i++) {
    const p = place(widest + (total - widest) * i / 20);
    if (!best || p.scale > best.scale) best = p;
  }
  parts.forEach((p, i) => p.nodes().shift({ x: best.at[i][0] - boxes[i].x1, y: best.at[i][1] - boxes[i].y1 }));
}

function drawTree(container, info, old, data, fontSize, full = data, keep = null) {
  if (old) old.destroy();
  container.replaceChildren();
  placeholder(info, "Click a person for details.");
  if (typeof cytoscape === "undefined") {
    container.append(el("p", { className: "tree-empty", textContent: "The tree needs the Cytoscape library, which loads from the internet." }));
    return null;
  }
  if (!data || !data.people.length) {
    container.append(el("p", { className: "tree-empty", textContent:
      data ? "No family links yet. They appear as you read." : "No family tree for this book yet." }));
    return null;
  }
  const edgeColor = "#8b8a85";
  const cy = cytoscape({
    container, minZoom: 0.2, maxZoom: 2.5, wheelSensitivity: 0.3,
    elements: [
      ...data.people.map((p) => ({ data: { id: p.id, label: p.name + (data.collapsedCounts?.get(p.id) ? `\n\u25b8 ${data.collapsedCounts.get(p.id)}` : ""),
                                           color: kindColor(p.kind) },
                                   // Someone the book never names (a relative known only from tradition) is drawn in grey.
                                   classes: [data.current.includes(p.id) ? "current" : "", p.in_book === false ? "tradition" : ""].join(" ").trim() })),
      ...data.relationships.map((r, i) => ({ data: { id: "e" + i, source: r.a, target: r.b, relation: r.relation, origin: r.source } })),
    ],
    style: [
      { selector: "node", style: { label: "data(label)", "background-color": "data(color)", color: "#fff", "text-valign": "center",
          "text-halign": "center", "text-wrap": "wrap", "text-max-width": 80, "font-size": fontSize, shape: "round-rectangle",
          width: "label", height: fontSize * 3, padding: "6px" } },
      { selector: "node.current", style: { "border-width": 3, "border-color": "#e39a6b" } },
      { selector: "node:selected", style: { "border-width": 3, "border-color": "#fff" } },
      { selector: "edge", style: { width: 2, "line-color": edgeColor, "target-arrow-color": edgeColor, "curve-style": "bezier",
          "target-arrow-shape": "triangle", "arrow-scale": 0.9 } },
      { selector: 'edge[relation = "parent_of"]', style: { "curve-style": "taxi", "taxi-direction": "downward", "taxi-turn": "50%" } },
      { selector: 'edge[relation != "parent_of"]', style: { "target-arrow-shape": "none", "line-style": "dotted", "line-color": "#b0709b" } },
      { selector: 'edge[origin = "outside"]', style: { "line-style": "dashed", opacity: 0.75 } },
      { selector: "node.tradition", style: { "background-color": "#8f8a80", opacity: 0.8 } },
    ],
  });
  layoutFamilies(cy);
  cy.fit(undefined, 8);
  cy.on("tap", "node", (e) => {
    const id = e.target.id();
    if (info.dataset.person === id) {      // a second click on the same person closes the details
      e.target.unselect();
      placeholder(info, "Click a person for details.");
    } else {
      showPerson(info, id, full);      // details use the whole tree, not just the part on screen
    }
  });
  cy.on("tap", (e) => { if (e.target === cy) placeholder(info, "Click a person for details."); });
  if (keep && cy.getElementById(keep).nonempty()) {      // keep the details open after a redraw
    cy.getElementById(keep).select();
    showPerson(info, keep, full);
  }
  return cy;
}

// A person's details. `inTree` is false in the profile window, which has no tree to fold children in.
function showPerson(info, id, data, inTree = true) {
  const byId = new Map(data.people.map((p) => [p.id, p]));
  const p = byId.get(id);
  if (!p) return;                  // not in this book's tree as far as the reader has got
  const related = (relation, own, other) => data.relationships
    .filter((r) => r.relation === relation && r[own] === id).map((r) => byId.get(r[other])?.name).filter(Boolean);
  const rows = [];
  const add = (label, text) => { if (text) rows.push(el("p", { className: "note" }, el("span", { className: "label", textContent: label + ":" }), " " + text)); };
  add(KIND_SHOWN.has(p.kind) ? "Domain" : "Who", p.about);
  // A god's domain says more than the passing note of one section ("loved Amphissa"), so gods have no "In the book".
  add("In the book", p.in_book && !KIND_SHOWN.has(p.kind) && p.description !== p.about ? p.description : "");
  // Proper names before cult titles: "Ambulian Zeus" and the like (titles built on the name itself) go last. The
  // names other books give the same being ("Jove", "Jupiter") are put in front once the server answers.
  const word = plainName(p.name);
  const aliases = otherNames(p.name, p.aliases);
  const titled = (n) => plainName(n).split(/\s+/).includes(word);
  const calledRow = el("p", { className: "note called" }, el("span", { className: "label", textContent: "Also called:" }));
  calledRow.dataset.names = JSON.stringify([...aliases.filter((n) => !titled(n)), ...aliases.filter(titled)]);
  fillCalled(calledRow, p.name);
  rows.push(calledRow);
  // The relatives: this book's first, then those only other books tell of, each being once (Athene here and Athena
  // there are one). Each name opens that person's profile. A link the book itself does not state (it is known from
  // tradition: Wikidata, the outside-the-book step, the ancient sources or another book) is greyed, with where it comes from on hover.
  const told = (r) => r.source === "text";
  const whence = (r) => r.from_books ? (r.note || "From other books") : r.note === "Wikidata" ? "From Wikidata"
    : (r.note || "").startsWith("Ancient source") ? r.note : "From tradition outside this book" + (r.note ? `: ${r.note}` : "");
  const linked = (relation, own, other, links = data.relationships, people = byId) => links
    .filter((r) => r.relation === relation && r[own] === id && people.has(r[other]))
    .sort((x, y) => told(y) - told(x) || Boolean(x.from_books) - Boolean(y.from_books))
    .map((r) => ({ person: people.get(r[other]), link: r }));
  const both = (relation, links, people) => [...linked(relation, "a", "b", links, people), ...linked(relation, "b", "a", links, people)];
  const once = (list) => {
    const seen = new Set();
    return list.filter(({ person }) => {
      const keys = [plainName(person.name), ...(person.wikidata || [])];
      if (keys.some((k) => seen.has(k))) return false;
      keys.forEach((k) => seen.add(k));
      return true;
    });
  };
  let traditional = false;
  const addPeople = (label, list) => {
    if (!list.length) return;
    rows.push(el("p", { className: "note" }, el("span", { className: "label", textContent: label + ":" }),
      ...list.flatMap(({ person, link }, k) => {
        const grey = !told(link);
        traditional ||= grey;
        const button = el("button", { type: "button", className: "link person-link" + (grey ? " tradition" : ""), textContent: person.name,
          title: grey ? `${whence(link)}. Not stated in this book.` : "Stated in this book. Who is this?" });
        button.addEventListener("click", () => openProfile(person.id));
        return [k ? ", " : " ", button];
      })));
  };
  addPeople("Parents", once(linked("parent_of", "b", "a")));
  addPeople("Children", once(linked("parent_of", "a", "b")));
  addPeople("Spouse", once(both("spouse_of")));
  addPeople("Siblings", once(both("sibling_of")));
  const lovers = state.loverData || { people: [], relationships: [] };
  addPeople("Lovers", once(both("lover_of", lovers.relationships, new Map(lovers.people.map((x) => [x.id, x])))));
  if (traditional) {
    rows.push(el("p", { className: "note tradition-key", textContent: "Names in grey are known from tradition (other books, the ancient sources or Wikidata) but not stated in this book." }));
  }
  // Someone this book never names (a relative from another book or from outside the books): said plainly. Whether
  // any book on the site names them is known once the server answers (loadElsewhere).
  if (!p.in_book) {
    rows.push(el("p", { className: "note unreferenced", textContent: "Not referenced in this book." }));
  }
  const page = state.about && (p.wikidata || []).map((q) => state.about.characters[q]).find((c) => c && c.url);
  const pic = page && picture(page.url);
  if (pic) rows.unshift(pic);
  if (page && page.lead) {
    rows.push(el("p", { className: "note" }, el("span", { className: "label", textContent: "Wikipedia:" }), " " + page.lead + " ",
      el("a", { href: page.url, target: "_blank", rel: "noopener", textContent: "Read more" })));
  } else if (page) {
    rows.push(el("p", { className: "note" }, el("a", { href: page.url, target: "_blank", rel: "noopener", textContent: "Wikipedia" })));
  }
  const kids = related("parent_of", "a", "b").length;
  if (kids >= 1 && inTree) {
    const noun = kids === 1 ? "child" : "children";
    const button = el("button", { type: "button", className: "tree-toggle", textContent:
      isCollapsed(id) ? `Show ${kids} ${noun}` : `Hide ${kids} ${noun}` });
    button.addEventListener("click", () => toggleChildren(id));
    rows.push(button);
  }
  info.className = "tree-info";
  info.dataset.person = id;
  info.dataset.place = "";
  info.dataset.item = "";
  info.dataset.render = ++renders;
  // The name, then how it is said ("yoo-LISS-eez"), where a pronunciation has been made for it.
  info.replaceChildren(el("strong", { textContent: p.name }),
    ...(p.say ? [" ", el("span", { className: "say", textContent: p.say, title: "How the name is usually said in English" })] : []), ...rows);
  loadReferences(info, id, [p.name, ...p.aliases]);
  loadElsewhere(info, "person", id, p.name);
}

// After a reference link: scroll to the scene the link was about and mark its first sentence briefly. Without a
// scene, go to the first sentence of the section that names the character.
function showFound() {
  const find = state.find;
  state.find = null;
  if (!find || !state.current || find.section !== state.current.id) return;
  const spans = els.text.querySelectorAll(".s");
  const scene = find.scene === null || find.scene === undefined ? null : scenes()[find.scene];
  if (scene) setScene(find.scene);
  // A link to one sentence (where a family link was read) opens at that sentence; otherwise at the first that names
  // the character.
  const hit = scene ? els.text.querySelector(`.s[data-i="${scene.start_sentence}"]`)
    : (find.sentence != null && els.text.querySelector(`.s[data-i="${find.sentence}"]`))
      || [...(spans.length ? spans : els.text.querySelectorAll("p"))].find((node) => find.names.some((n) => node.textContent.includes(n)));
  if (!hit) return;
  state.sceneLock = Date.now() + 1500;        // the jump itself must not move the scene card to another scene
  const folded = hit.closest("details");
  if (folded) folded.open = true;
  hit.scrollIntoView({ block: scene ? "start" : "center" });
  hit.classList.add("found");
  setTimeout(() => hit.classList.remove("found"), 4000);
}

// A character's key moments in this book, every scene they are in (folded), then their key moments in the other
// books of the library. Each one is a link that opens the section at that scene.
async function loadReferences(info, id, names) {
  let refs;
  const render = info.dataset.render;
  try { refs = await api(`books/${state.slug}/people/${encodeURIComponent(id)}/references`); } catch { return; }
  const all = refs.scenes || [];
  if (info.dataset.render !== render || info.dataset.person !== id
      || (!refs.here.length && !refs.elsewhere.length && !(refs.elsewhere_scenes || []).length && !all.length)) return;     // closed, or nothing to show
  const item = (slug, m, label) => {
    const a = el("a", { href: `#${slug}/${m.section}`, textContent: label });
    a.addEventListener("click", () => {
      // In another book the character goes by that book's name for them.
      state.find = { section: m.section, scene: m.scene, names: slug === state.slug ? names : [m.name] };
      if (els.treeDialog.open) els.treeDialog.close();
      if ($("person-dialog").open) $("person-dialog").close();
      if (slug === state.slug && m.section === state.current.id) showFound();     // already on that section: no reload
    });
    return el("li", {}, a, m.what ? ": " + m.what : "");
  };
  if (refs.here.length) {
    info.append(el("h4", { textContent: "Key moments:" }),
      el("ul", { className: "moments" }, ...refs.here.map((m) => item(state.slug, m, m.title))));
  }
  if (all.length) {
    const sections = new Set(all.map((m) => m.section)).size;
    info.append(el("details", { className: "book-refs" },
      el("summary", { textContent: `All scenes \u00b7 ${all.length} in ${sections} section${sections > 1 ? "s" : ""}` }),
      el("ul", { className: "moments" }, ...all.map((m) => item(state.slug, m, `${m.title} \u203a ${m.scene_title}`)))));
  }
  // One foldable group per book: its key moments, then every scene the character is in there.
  const books = new Map();
  const group = (m) => {
    if (!books.has(m.book)) books.set(m.book, { first: m, moments: [], scenes: [] });
    return books.get(m.book);
  };
  for (const m of refs.elsewhere) group(m).moments.push(m);
  for (const m of refs.elsewhere_scenes || []) group(m).scenes.push(m);
  if (books.size) info.append(el("h4", { textContent: "In other books:" }));
  for (const [slug, { first, moments, scenes }] of books) {
    const as = first.name !== names[0] ? ` (as ${first.name})` : "";
    const count = [moments.length ? `${moments.length} key moment${moments.length > 1 ? "s" : ""}` : "",
      scenes.length ? `${scenes.length} scene${scenes.length > 1 ? "s" : ""}` : ""].filter(Boolean).join(", ");
    info.append(el("details", { className: "book-refs" },
      el("summary", { textContent: `${first.book_title}${as} \u00b7 ${count}` }),
      ...(moments.length ? [el("ul", { className: "moments" }, ...moments.map((m) => item(slug, m, `${m.title}${as}`)))] : []),
      ...(scenes.length ? [el("p", { className: "scenes-head", textContent: "All scenes:" }),
        el("ul", { className: "moments" }, ...scenes.map((m) => item(slug, m, `${m.title} \u203a ${m.scene_title}`)))] : [])));
  }
}

// ---------- Search ----------

// Find a character, a place or a thing by any of its names: this book's first, then the other books'. A result opens the
// profile; one in another book opens that book first.
async function runSearch() {
  const q = $("search").value.trim(), list = $("search-results");
  if (q.length < 2) { list.hidden = true; return; }
  let found = [];
  try { found = await api(`search?q=${encodeURIComponent(q)}&book=${encodeURIComponent(state.slug || "")}`); } catch { /* the server is unreachable */ }
  if ($("search").value.trim() !== q) return;                     // the reader has typed on meanwhile
  // One row per being, place or thing: it opens in this book if it is here, and says how many books it is in.
  list.replaceChildren(...(found.length ? found.slice(0, 30).map((f) => {
    const books = new Set(f.books.map((b) => b.book)).size;
    const where = [f.book !== state.slug ? f.book_title : "", books > 1 ? `in ${books} books` : ""].filter(Boolean).join(", ");
    const item = el("li", { tabIndex: -1 }, el("strong", { textContent: f.name }), f.as ? ` (${f.as})` : "",
      f.type !== "person" ? el("span", { className: "tag", textContent: f.type === "item" ? "thing" : f.type }) : "",
      where ? el("span", { className: "where", textContent: " · " + where }) : "",
      f.about ? el("span", { className: "what", textContent: f.about }) : "");
    item.addEventListener("click", () => openResult(f));
    item.addEventListener("keydown", (e) => { if (e.key === "Enter") openResult(f); });
    return item;
  }) : [el("li", { className: "none", textContent: "Nothing by that name." })]));
  list.hidden = false;
}

function closeSearch() { $("search-results").hidden = true; }

function openResult(f) {
  closeSearch();
  $("search").value = "";
  if (f.type === "place") {
    if (f.book === state.slug) return openPlace(f.id);
    state.pendingPlace = { book: f.book, id: f.id };     // loadPlaces opens it once that book's places are in
  } else if (f.type === "item") {
    if (f.book === state.slug) return openItem(f.id);
    state.pendingItem = { book: f.book, id: f.id };      // loadItems opens it once that book's things are in
  } else {
    if (f.book === state.slug && state.treeData) return openProfile(f.id);
    state.pendingProfile = { book: f.book, id: f.id };   // loadTree opens it once that book's tree is in
  }
  if (f.book !== state.slug) {
    els.select.value = f.book;
    location.hash = "";
    openBook(f.book, null);
  }
}

let searchTimer = 0;
$("search").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(runSearch, 150); });
$("search").addEventListener("keydown", (e) => {
  const first = $("search-results").querySelector("li:not(.none)");
  if (e.key === "Escape") closeSearch();
  if (e.key === "ArrowDown" && first && !$("search-results").hidden) { e.preventDefault(); first.focus(); }
  if (e.key === "Enter" && first) first.click();
});
$("search-results").addEventListener("keydown", (e) => {
  const at = document.activeElement;
  if (e.key === "ArrowDown" && at.nextElementSibling) { e.preventDefault(); at.nextElementSibling.focus(); }
  if (e.key === "ArrowUp") { e.preventDefault(); (at.previousElementSibling || $("search")).focus(); }
  if (e.key === "Escape") { closeSearch(); $("search").focus(); }
});
document.addEventListener("click", (e) => { if (!e.target.closest(".search")) closeSearch(); });

// ---------- Events ----------

els.select.addEventListener("change", () => { location.hash = ""; openBook(els.select.value, null); });
els.text.addEventListener("click", (e) => {
  const sentence = e.target.closest(".s.has-note");
  if (sentence) selectSentence(Number(sentence.dataset.i));
});
els.text.addEventListener("keydown", (e) => {
  const sentence = e.target.closest(".s.has-note");
  if (sentence && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); selectSentence(Number(sentence.dataset.i)); }
});
function setAutoCollapse(on) {
  state.autoCollapse = on;
  store.set("autocollapse", on);
  els.autoCollapse.checked = els.autoCollapseBig.checked = on;
  if (state.treeData) drawBoth();
}
for (const box of [els.autoCollapse, els.autoCollapseBig]) box.addEventListener("change", () => setAutoCollapse(box.checked));
els.expand.addEventListener("click", () => { els.treeDialog.showModal(); drawBoth(); });
els.depth.addEventListener("input", () => setDepth(Number(els.depth.value)));
els.autoCollapse.checked = els.autoCollapseBig.checked = state.autoCollapse;
els.depthBig.addEventListener("input", () => setDepth(Number(els.depthBig.value)));
setDepth(state.depth, false);
// Drag the edges of the contents and notes columns to resize them. The widths are remembered; double-click an
// edge to reset it, or focus it and use the arrow keys (hold Shift for bigger steps).
const COLUMN = { toc: { start: 260, min: 160, max: 520 }, side: { start: 340, min: 260, max: 760 } };
const widths = { toc: COLUMN.toc.start, side: COLUMN.side.start, ...(store.get("widths") || {}) };

function setWidth(which, px) {
  const { min, max } = COLUMN[which];
  widths[which] = Math.round(Math.min(max, Math.max(min, px)));
  els.layout.style.setProperty(which === "toc" ? "--toc-w" : "--side-w", widths[which] + "px");
  store.set("widths", widths);
  if (state.cy) { state.cy.resize(); state.cy.fit(undefined, 8); }
  if (state.map) state.map.invalidateSize();
}

for (const bar of document.querySelectorAll(".resizer")) {
  const which = bar.dataset.side;
  bar.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    bar.setPointerCapture(e.pointerId);
    bar.classList.add("dragging");
    document.body.classList.add("resizing");
    const move = (ev) => {
      const box = els.layout.getBoundingClientRect();
      setWidth(which, which === "toc" ? ev.clientX - box.left : box.right - ev.clientX);
    };
    const stop = () => {
      bar.classList.remove("dragging");
      document.body.classList.remove("resizing");
      bar.removeEventListener("pointermove", move);
      bar.removeEventListener("pointerup", stop);
      bar.removeEventListener("pointercancel", stop);
    };
    bar.addEventListener("pointermove", move);
    bar.addEventListener("pointerup", stop);
    bar.addEventListener("pointercancel", stop);
  });
  bar.addEventListener("dblclick", () => setWidth(which, COLUMN[which].start));
  bar.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    e.stopPropagation();       // the arrow keys also turn pages; here they only resize
    const step = (e.shiftKey ? 40 : 10) * (e.key === "ArrowRight" ? 1 : -1);
    setWidth(which, widths[which] + (which === "toc" ? step : -step));   // the notes column grows to the left
  });
}
setWidth("toc", widths.toc);
setWidth("side", widths.side);

// The side panes can be folded away. Which ones are open is remembered.
const paneOpen = store.get("panes") || {};
for (const pane of document.querySelectorAll("details.pane")) {
  if (paneOpen[pane.id] === false) pane.open = false;
  pane.addEventListener("toggle", () => {
    paneOpen[pane.id] = pane.open;
    store.set("panes", paneOpen);
    if (pane === els.treePane && pane.open && state.treeData) drawBoth();
    if (pane === els.mapPane && pane.open && state.current) drawMap();
  });
}
els.treeClose.addEventListener("click", () => els.treeDialog.close());
$("person-close").addEventListener("click", () => $("person-dialog").close());
$("person-dialog").addEventListener("click", (e) => { if (e.target === e.currentTarget) e.currentTarget.close(); });   // a click outside the window closes it
els.treeDialog.addEventListener("close", () => { if (state.cyBig) { state.cyBig.destroy(); state.cyBig = null; } });
window.addEventListener("resize", () => { if (state.cy) { state.cy.resize(); state.cy.fit(undefined, 8); } if (state.map) state.map.invalidateSize(); });
// Follow the reader down the page: the scene card and the map show the scene on screen.
let sceneTick = false;
const onReaderScroll = () => {
  if (sceneTick) return;
  sceneTick = true;
  requestAnimationFrame(() => { sceneTick = false; trackScene(); });
};
document.querySelector(".reader").addEventListener("scroll", onReaderScroll, { passive: true });
window.addEventListener("scroll", onReaderScroll, { passive: true });
// On a phone the contents are a drawer and the notes a sheet over the text: one of them open at a time, or none.
const phone = matchMedia("(max-width: 900px)");
function openPanel(which) {
  els.layout.classList.toggle("toc-open", which === "toc");
  els.layout.classList.toggle("side-open", which === "side");
  $("scrim").hidden = !which;
  if (which === "side") {                     // the map and tree were drawn while hidden: fit them now
    if (state.cy) { state.cy.resize(); state.cy.fit(undefined, 8); }
    if (state.current) drawMap();
  }
}
$("scrim").addEventListener("click", () => openPanel(null));
for (const btn of document.querySelectorAll(".panel-close")) btn.addEventListener("click", () => openPanel(null));
els.toc.addEventListener("click", (e) => { if (phone.matches && e.target.closest("a")) openPanel(null); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && phone.matches) openPanel(null); });
phone.addEventListener("change", () => openPanel(null));
document.getElementById("side-toggle").addEventListener("click", (e) => {
  if (phone.matches) return openPanel(els.layout.classList.contains("side-open") ? null : "side");
  const hidden = els.layout.classList.toggle("side-hidden");
  e.currentTarget.setAttribute("aria-expanded", String(!hidden));
  if (!hidden && state.cy) { state.cy.resize(); state.cy.fit(undefined, 8); }
  if (!hidden && state.current) drawMap();
});
$("license-open").addEventListener("click", () => els.licenseDialog.showModal());
$("license-close").addEventListener("click", () => els.licenseDialog.close());
els.tocToggle.addEventListener("click", () => {
  if (phone.matches) return openPanel(els.layout.classList.contains("toc-open") ? null : "toc");
  const hidden = els.layout.classList.toggle("toc-hidden");
  els.tocToggle.setAttribute("aria-expanded", String(!hidden));
});
for (const btn of [els.prev, els.next]) btn.addEventListener("click", () => goTo(btn.dataset.id));
window.addEventListener("hashchange", () => {
  const [slug, id] = location.hash.slice(1).split("/");
  if (slug === state.slug && id) showSection(id);
  else if (id && [...els.select.options].some((o) => o.value === slug)) {      // a link into another book
    els.select.value = slug;
    openBook(slug, id);
  }
});
document.addEventListener("keydown", (e) => {
  if (e.target.closest("select, input, textarea") || e.altKey || e.ctrlKey || e.metaKey) return;
  if (e.key === "ArrowLeft" && !els.prev.disabled) goTo(els.prev.dataset.id);
  if (e.key === "ArrowRight" && !els.next.disabled) goTo(els.next.dataset.id);
});

loadBooks().catch((e) => showError(e.message));
