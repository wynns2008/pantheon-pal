"""Pictures for a book from Wikipedia and Wikimedia Commons (free, no Claude). For every Wikipedia article that
annotate.about found (the work, its author, and each character the Wikidata check matched), for each place
annotate.places found and each thing annotate.items found, the article's lead image,
but only one Wikipedia marks as free (PageImages' "free" choice). Commons supplies a small copy, the artist and the
licence, which the reader shows as a credit line under the picture.

    python -m annotate.images <name>          # writes data/<name>/output/images.json; run annotate.about first

The picture is the article's, so for a character it is sometimes a scene with others in it (a vase of Telemachus
leaving Penelope heads the article on Eumaeus).
"""
import argparse
import json
import re
import sys
import urllib.parse

from annotate.about import WIKIPEDIA, fetch          # same pace and user agent as annotate.about
from annotate.run import ROOT

COMMONS = "https://commons.wikimedia.org/w/api.php"
WIDTH = 320                    # pixels; the reader shows pictures at most 240 wide, so they stay sharp
TAGS = re.compile(r"<[^>]+>")


def title_of(url: str) -> str:
    return urllib.parse.unquote(url.rsplit("/", 1)[1]).replace("_", " ")


def plain(html: str) -> str:
    """Commons metadata is HTML; some fields repeat themselves once the tags are gone ("Unknown artistUnknown artist")."""
    text = " ".join(TAGS.sub("", html).split())
    half = len(text) // 2
    return text[:half] if len(text) % 2 == 0 and text[:half] == text[half:] else text


def lead_images(titles: list[str]) -> dict[str, str]:
    """{article title: file name of its lead image}, free images only."""
    found = {}
    for start in range(0, len(titles), 50):
        answer = fetch(WIKIPEDIA + "?" + urllib.parse.urlencode({
            "action": "query", "prop": "pageimages", "piprop": "name", "pilicense": "free", "redirects": "1",
            "titles": "|".join(titles[start:start + 50]), "format": "json", "formatversion": "2"}))["query"]
        renamed = {r["to"]: r["from"] for r in answer.get("normalized", []) + answer.get("redirects", [])}
        for page in answer.get("pages", []):
            title = page["title"]
            while title not in titles and title in renamed:
                title = renamed[title]
            if page.get("pageimage"):
                found[title] = page["pageimage"].replace("_", " ")
    return found


def file_info(files: list[str]) -> dict[str, dict]:
    """{file name: {thumb, page, artist, license, license_url}} from Commons."""
    info = {}
    for start in range(0, len(files), 50):
        answer = fetch(COMMONS + "?" + urllib.parse.urlencode({
            "action": "query", "prop": "imageinfo", "iiprop": "url|extmetadata", "iiurlwidth": str(WIDTH),
            "titles": "|".join("File:" + f for f in files[start:start + 50]), "format": "json", "formatversion": "2"}))["query"]
        renamed = {r["to"]: r["from"] for r in answer.get("normalized", [])}
        for page in answer.get("pages", []):
            if not page.get("imageinfo"):
                continue
            ii = page["imageinfo"][0]
            meta = ii.get("extmetadata", {})
            name = renamed.get(page["title"], page["title"]).split(":", 1)[1].replace("_", " ")
            info[name] = {"thumb": ii.get("thumburl") or ii["url"], "page": ii["descriptionurl"],
                          "artist": plain(meta.get("Artist", {}).get("value", ""))[:120],
                          "license": plain(meta.get("LicenseShortName", {}).get("value", "")),
                          "license_url": plain(meta.get("LicenseUrl", {}).get("value", ""))}
    return info


def pictures(slug: str) -> dict[str, dict]:
    """{wikipedia url: picture} for every article in the book's about.json that has a free lead image."""
    file = ROOT / "data" / slug / "output" / "about.json"
    if not file.exists():
        sys.exit(f"No about.json for {slug}: run python -m annotate.about {slug} --yes first.")
    about = json.loads(file.read_text(encoding="utf-8"))
    urls = [w.get("url") for w in about.get("works", {}).values()] + [(about.get("author") or {}).get("url")]
    urls += [c.get("url") for c in about.get("characters", {}).values()]
    places = ROOT / "data" / slug / "output" / "places.json"       # python -m annotate.places
    if places.exists():
        urls += [p["wikipedia"]["url"] for p in json.loads(places.read_text(encoding="utf-8"))["places"] if p["wikipedia"]]
    items = ROOT / "data" / slug / "output" / "items.json"         # python -m annotate.items
    if items.exists():
        urls += [t["wikipedia"]["url"] for t in json.loads(items.read_text(encoding="utf-8"))["items"] if t["wikipedia"]]
    by_title = {title_of(u): u for u in urls if u}
    leads = lead_images(sorted(by_title))
    info = file_info(sorted(set(leads.values())))
    return {by_title[t]: info[f] for t, f in leads.items() if f in info}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name")
    args = ap.parse_args()
    found = pictures(args.name)
    out = ROOT / "data" / args.name / "output" / "images.json"
    out.write_text(json.dumps({"source": "Wikimedia Commons", "pictures": found}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Saved {out.relative_to(ROOT)}: {len(found)} articles with a free picture.")


if __name__ == "__main__":
    main()
