"""One command for a whole book: get the text, parse it, annotate it with Claude, build the family tree.

    python -m annotate.book <name> --gutenberg 348    download Project Gutenberg eBook #348 into books/<name>/ first
    python -m annotate.book <name> --gutenberg 348 --parse-only   download it, parse it and check the parse; spends nothing
    python -m annotate.book <name>                    parse it and show the cost; spends nothing
    python -m annotate.book <name> --yes              ... then annotate it and build the tree, with outside-the-book links
    python -m annotate.book <name> --yes --no-outside ... without the outside-the-book links (a few cents cheaper)
    python -m annotate.book <name> --yes --no-review  ... without Claude's review of the finished tree
    python -m annotate.book <name> --yes --no-moments ... without picking each character's key moments
    python -m annotate.book <name> --yes --no-pronounce ... without the pronunciations of the characters' names
    python -m annotate.book <name> --yes --no-pictures ... without the pictures from Wikimedia Commons
    python -m annotate.book <name> --yes --no-places  ... without the places and their profiles
    python -m annotate.book <name> --yes --wikidata   ... and check the tree against Wikidata (a few cents)
    python -m annotate.book <name> --yes --wikidata --tradition "Greek and Roman mythology"   with a hint for that check

If the text is not at books/<name>/source.txt, give its eBook number with --gutenberg (the number in
gutenberg.org/ebooks/<number>). Sections that are already annotated are skipped, so after a failure or an
interruption the same command carries on where it stopped. Nothing is downloaded or spent without --yes.
With --wikidata a last step (annotate.verify) matches the characters to Wikidata and corrects the tree.
The parse is checked (annotate.check) before anything is spent, and the finished book is checked at the end.
"""
import argparse
import json
import subprocess
import sys
import urllib.request
from argparse import Namespace

from annotate.check import check
from annotate.merge import ROOT, merge
from annotate.run import cmd_estimate, cmd_run, pending
from litparse.__main__ import cmd_add


def build_tree(slug: str) -> None:
    tree = merge(slug)
    (ROOT / "data" / slug / "output" / "tree.json").write_text(json.dumps(tree, ensure_ascii=False, indent=1), encoding="utf-8")
    r = tree["report"]
    print(f"Family tree: {r['people']} people, {r['relationships']} relationships")


def download(slug: str, number: int) -> None:
    """Save Project Gutenberg eBook #number, unmodified, as books/<slug>/source.txt."""
    url = f"https://www.gutenberg.org/cache/epub/{number}/pg{number}.txt"
    request = urllib.request.Request(url, headers={"User-Agent": "PantheonPal (personal reading tool)"})
    try:
        data = urllib.request.urlopen(request, timeout=60).read()
    except OSError as e:
        sys.exit(f"Could not download {url}: {e}")
    if b"*** START OF" not in data:
        sys.exit(f"{url} is not a Project Gutenberg text (no START marker); nothing saved.")
    target = ROOT / "books" / slug / "source.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    print(f"Saved {len(data) / 1024:,.0f} KB to {target.relative_to(ROOT)}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug", help="book folder name, e.g. hesiod")
    ap.add_argument("--gutenberg", type=int, metavar="NUMBER", help="download this Project Gutenberg eBook number first")
    ap.add_argument("--yes", action="store_true", help="confirm that you want to download and spend money")
    ap.add_argument("--no-outside", action="store_true", help="skip the links from outside the book")
    ap.add_argument("--no-review", action="store_true", help="skip Claude's review of the finished family tree")
    ap.add_argument("--no-moments", action="store_true", help="skip picking each character's key moments")
    ap.add_argument("--no-pronounce", action="store_true", help="skip the pronunciations of the characters' names")
    ap.add_argument("--no-pictures", action="store_true", help="skip the pictures from Wikimedia Commons")
    ap.add_argument("--no-places", action="store_true", help="skip the places and their profiles")
    ap.add_argument("--wikidata", action="store_true", help="also check the family tree against Wikidata")
    ap.add_argument("--tradition", default="", help='a hint for the Wikidata check, e.g. "Greek and Roman mythology"')
    ap.add_argument("--workers", type=int, default=4, help="sections annotated at the same time")
    ap.add_argument("--parse-only", action="store_true", help="download if needed, parse and check the parse, then stop; spends nothing")
    ap.add_argument("--ignore-checks", action="store_true", help="annotate even if the check of the parse found a fault")
    args = ap.parse_args()
    slug = args.slug
    source = ROOT / "books" / slug / "source.txt"

    print("1/10 Getting the text")
    if source.exists():
        print(f"Already there: {source.relative_to(ROOT)}")
    elif args.gutenberg is None:
        print(f"No text at {source.relative_to(ROOT)}; the parser will look for books/{slug}.txt")
    elif not (args.yes or args.parse_only):
        return print(f"Would download https://www.gutenberg.org/ebooks/{args.gutenberg} to {source.relative_to(ROOT)}."
                     f"\nTo do it: python -m annotate.book {slug} --gutenberg {args.gutenberg} --yes")
    else:
        download(slug, args.gutenberg)

    print("\n2/10 Parsing")
    cmd_add(Namespace(name=slug))
    print("\nChecking the parse (free)")
    _, failures = check(slug, parse_only=True)
    if args.parse_only:
        return print(f"\nStopped after the parse (--parse-only). List the sections with: python -m litparse show {slug}")
    todo = pending(slug, None, None)
    if failures and todo and not args.ignore_checks:
        return print(f"\nThe parse has a fault that annotation would only build on, so nothing was spent. Fix the parser, or go on "
                     f"anyway with: python -m annotate.book {slug} --yes --ignore-checks")
    print(f"\n3/10 Annotating: {len(todo)} sections to do")
    if todo:
        cmd_estimate(Namespace(slug=slug, limit=None, only=None))
        outside = (ROOT / "data" / slug / "curated" / "outside.json").exists()
        if not args.no_outside and not outside:
            print("Plus about 5 cents for the links from outside the book (--no-outside to skip).")
        if not args.no_review:
            print("Plus about 5 cents for Claude's review of the finished tree (--no-review to skip).")
        if args.wikidata:
            print("Plus a few cents for matching the characters to Wikidata.")
        if not args.no_moments:
            print("Plus 10 to 40 cents for picking each character's key moments (--no-moments to skip).")
        if not args.no_pronounce:
            print("Plus a few cents for how each character's name is said (--no-pronounce to skip).")
        if not args.no_places:
            print("Plus about 5 to 20 cents for the places (--no-places to skip).")
        if not args.yes:
            return print(f"\nNothing spent. To annotate and build the tree: python -m annotate.book {slug} --yes")
        cmd_run(Namespace(slug=slug, limit=None, only=None, workers=args.workers, yes=True))
        if pending(slug, None, None):
            sys.exit(f"Some sections failed. Run the same command again to retry only those: python -m annotate.book {slug} --yes")
    print("\n4/10 Building the family tree")
    build_tree(slug)
    print("\n5/10 Links from outside the book")
    if args.no_outside:
        print("Skipped (--no-outside).")
    elif (ROOT / "data" / slug / "curated" / "outside.json").exists():
        print("Already there (data/<name>/curated/outside.json); it may hold hand edits, so it is kept.")
    elif not args.yes:
        print("Spends about 5 cents. Add --yes to run it.")
    else:
        subprocess.run([sys.executable, "-m", "annotate.outside", slug, "--yes"], cwd=ROOT, check=True)
        build_tree(slug)
    print("\n6/10 Reviewing the family tree (duplicates and wrong merges)")
    if args.no_review:
        print("Skipped (--no-review).")
    elif (ROOT / "data" / slug / "output" / "review.json").exists() and not todo:
        print("Already reviewed (output/review.json); delete that file to review again.")
        build_tree(slug)
    elif not args.yes:
        print("Spends about 5 cents. Add --yes to run it.")
    elif subprocess.run([sys.executable, "-m", "annotate.review", slug, "--yes"], cwd=ROOT).returncode == 0:
        build_tree(slug)
    else:
        print("The review did not finish; the tree was left as it was. Run the same command again to retry.")
    print("\n7/10 Checking the family tree against Wikidata")
    if not args.wikidata:
        print("Not asked for (add --wikidata to run it).")
    elif not args.yes:
        print("Spends a few cents. Add --yes to run it.")
    else:
        command = [sys.executable, "-m", "annotate.verify", slug, "--yes"] + (["--tradition", args.tradition] if args.tradition else [])
        if subprocess.run(command, cwd=ROOT).returncode == 0:
            build_tree(slug)
        else:       # no internet, or Wikidata is down: the tree from step 4 or 5 stands
            print("The Wikidata check did not finish; the tree was left as it was. Run the same command again to retry.")
    print("\n8/10 Key moments of each character")
    if args.no_moments:
        print("Skipped (--no-moments).")
    elif (ROOT / "data" / slug / "output" / "moments.json").exists() and not todo:
        print("Already there (output/moments.json); delete that file to pick them again.")
    elif not args.yes:
        print("Spends 10 to 40 cents. Add --yes to run it.")
    elif subprocess.run([sys.executable, "-m", "annotate.moments", slug, "--yes"], cwd=ROOT).returncode != 0:
        print("That did not finish. Run the same command again to retry.")
    print("\n9/10 How each character's name is said")
    if args.no_pronounce:
        print("Skipped (--no-pronounce).")
    elif not args.yes:
        print("Spends a few cents, and only on names no earlier book has. Add --yes to run it.")
    elif subprocess.run([sys.executable, "-m", "annotate.pronounce", slug, "--yes"], cwd=ROOT).returncode != 0:
        print("That did not finish. Run the same command again to retry.")
    print("\n10/10 The library index (characters shared between books; free), Wikipedia's summaries, themes and history,"
          " the places, and pictures from Wikimedia Commons (free)")
    subprocess.run([sys.executable, "-m", "annotate.library"], cwd=ROOT)
    output = ROOT / "data" / slug / "output"
    if args.yes and not (output / "about.json").exists():
        subprocess.run([sys.executable, "-m", "annotate.about", slug, "--yes"], cwd=ROOT)
    if args.no_places:
        print("Places skipped (--no-places).")
    elif args.yes and not (output / "places.json").exists():
        if subprocess.run([sys.executable, "-m", "annotate.places", slug, "--yes"], cwd=ROOT).returncode != 0:
            print("The places did not finish. Run the same command again to retry.")
    if args.no_pictures:
        print("Pictures skipped (--no-pictures).")
    elif args.yes and (output / "about.json").exists():
        subprocess.run([sys.executable, "-m", "annotate.images", slug], cwd=ROOT)
    print("\nChecking the finished book (free)")
    check(slug)
    print(f"\nDone. Start the reader with: uvicorn server.app:app   (book: {slug})")


if __name__ == "__main__":
    main()
