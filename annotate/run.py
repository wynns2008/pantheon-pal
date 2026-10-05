"""Run the annotation pass with Claude: estimate the cost, then annotate.

    python -m annotate.run estimate <slug> [--limit N] [--only SECTION_ID]
    python -m annotate.run run      <slug> [--limit N] [--only SECTION_ID] [--workers N] --yes

`estimate` only counts tokens (free). `run` spends money and needs --yes. It calls the API directly, a few
sections at a time. Each section takes two passes (see annotate.schema): an outline of the whole section, then
the sentence notes scene by scene. One file is saved per section, and sections that already have a file are
skipped, so a failed run can simply be repeated.
"""
import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from annotate.schema import (CHUNK_MAX, MODEL, NOTES_PROMPT, SCHEMA_VERSION, SYSTEM_PROMPT, add_notes, chunks,
                             clean_result, notes_params, request_params, user_message)
from litparse.sentences import split_sentences

ROOT = Path(__file__).resolve().parent.parent

# Dollars per million tokens for Claude Sonnet 5.5; the Batch API charges half. Check these against the
# current price list before relying on them.
PRICE_IN, PRICE_OUT, BATCH_DISCOUNT = 2.00, 10.00, 0.5
# Output per section, measured on Odyssey book 9 (169 sentences: about 3,000 for the outline, 4,800 for the notes).
EXPECTED_OUTLINE_TOKENS, EXPECTED_NOTE_TOKENS_PER_SENTENCE = 3000, 28


def annotations_dir(slug: str) -> Path:
    return ROOT / "data" / slug / "output" / "annotations"


def body_sections(slug: str) -> list[dict]:
    path = ROOT / "data" / slug / "output" / "sections.json"
    if not path.exists():
        sys.exit(f"Not parsed yet. Run: python -m litparse add {slug}")
    book = json.loads(path.read_text(encoding="utf-8"))
    return [{**s, "work": book.get("title")} for s in book["sections"] if s["category"] == "body"]


def pending(slug: str, limit: int | None, only: str | None) -> list[dict]:
    done = {p.stem for p in annotations_dir(slug).glob("*.json") if p.stem != "batch"}
    todo = [s for s in body_sections(slug) if s["id"] not in done and only in (None, s["id"])]
    return todo[:limit] if limit else todo


def get_client():
    """Anthropic client using the key from .env. The key itself is never printed."""
    from dotenv import load_dotenv
    import anthropic
    load_dotenv(ROOT / ".env")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is empty. Paste your key after the = in the .env file in the project folder.")
    return anthropic.Anthropic()


def cost(tokens_in: int, tokens_out: int, batch: bool = True) -> float:
    return (tokens_in * PRICE_IN + tokens_out * PRICE_OUT) / 1e6 * (BATCH_DISCOUNT if batch else 1)


def cmd_estimate(args, client=None) -> None:
    todo = pending(args.slug, args.limit, args.only)
    if not todo:
        return print("Nothing to do: every body section already has annotations.")
    client = client or get_client()

    def count(system: str, content: str) -> int:
        return client.messages.count_tokens(model=MODEL, system=system,
                                            messages=[{"role": "user", "content": content}]).input_tokens

    notes_overhead = count(NOTES_PROMPT, "x")      # what each extra notes request repeats
    tokens_in = tokens_out = requests = 0
    for s in todo:
        sentences = split_sentences(s["text"])
        msg = user_message(s["title"], s["reference"], sentences, s["work"])
        parts = max(1, -(-len(sentences) // (CHUNK_MAX * 2 // 3)))    # scenes are not known yet: assume 40 sentences each
        tokens_in += count(SYSTEM_PROMPT, msg) + count(NOTES_PROMPT, msg) + (parts - 1) * notes_overhead
        tokens_out += EXPECTED_OUTLINE_TOKENS + EXPECTED_NOTE_TOKENS_PER_SENTENCE * len(sentences)
        requests += 1 + parts
    print(f"{len(todo)} sections with {MODEL}, about {requests} requests (an outline per section, then notes scene by scene)")
    print(f"  input tokens (counted):   {tokens_in:,}")
    print(f"  output tokens (assumed):  {tokens_out:,}  ({EXPECTED_OUTLINE_TOKENS:,} per section "
          f"+ {EXPECTED_NOTE_TOKENS_PER_SENTENCE} per sentence)")
    print(f"  estimated cost:           ${cost(tokens_in, tokens_out, batch=False):.2f}")
    flags = (f" --limit {args.limit}" if args.limit else "") + (f" --only {args.only}" if args.only else "") + " --yes"
    print(f"To run it now: python -m annotate.run run {args.slug}{flags}")


def ask(client, params: dict) -> tuple[dict | None, tuple[int, int], str | None]:
    """One request. Returns (the JSON it answered with, (tokens in, tokens out), error)."""
    r = client.messages.create(**params)
    u = r.usage
    usage = (u.input_tokens + (getattr(u, "cache_read_input_tokens", 0) or 0)
             + (getattr(u, "cache_creation_input_tokens", 0) or 0), u.output_tokens)
    if r.stop_reason != "end_turn":
        return None, usage, f"stopped: {r.stop_reason}"
    try:
        return json.loads(next((b.text for b in r.content if b.type == "text"), "")), usage, None
    except json.JSONDecodeError as e:
        return None, usage, f"unreadable result: {e}"


def annotate_one(client, section: dict) -> tuple[dict | None, tuple[int, int], str | None]:
    """Annotate one section: the outline, then the notes for each chunk. Returns (result, (tokens in, out), error)."""
    sentences = split_sentences(section["text"])
    args = (section["title"], section["reference"], sentences, section["work"])
    raw, (tokens_in, tokens_out), error = ask(client, request_params(*args))
    if error:
        return None, (tokens_in, tokens_out), f"outline {error}"
    try:
        result = clean_result(raw, len(sentences))
        for chunk in chunks(result["scenes"], len(sentences)):
            notes, usage, error = ask(client, notes_params(*args, chunk))
            tokens_in, tokens_out = tokens_in + usage[0], tokens_out + usage[1]
            if error:
                return None, (tokens_in, tokens_out), f"notes for sentences {chunk['start']}-{chunk['end']} {error}"
            add_notes(result, notes, chunk)
    except (KeyError, TypeError, AttributeError) as e:
        return None, (tokens_in, tokens_out), f"unreadable result: {type(e).__name__}: {e}"
    return result, (tokens_in, tokens_out), None


def cmd_run(args, client=None) -> None:
    if not args.yes:
        sys.exit("This spends money on the Claude API. Run `estimate` first, then repeat with --yes.")
    todo = pending(args.slug, args.limit, args.only)
    if not todo:
        return print("Nothing to do: every body section already has annotations.")
    client = client or get_client()
    out = annotations_dir(args.slug)
    out.mkdir(parents=True, exist_ok=True)
    saved, failed, tokens_in, tokens_out = 0, [], 0, 0
    print(f"Annotating {len(todo)} sections, {args.workers} at a time. Each finished section is saved at once, "
          f"so you can stop and resume.")
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(annotate_one, client, s): s for s in todo}
        for n, future in enumerate(as_completed(futures), 1):
            s = futures[future]
            try:
                result, usage, error = future.result()
            except Exception as e:      # API errors that survived the SDK's own retries
                failed.append((s["id"], f"{type(e).__name__}: {e}"))
                continue
            tokens_in += usage[0]
            tokens_out += usage[1]
            if error:
                failed.append((s["id"], error))
                continue
            (out / f"{s['id']}.json").write_text(json.dumps(
                {"section_id": s["id"], "model": MODEL, "schema": SCHEMA_VERSION,
                 "usage": {"input": usage[0], "output": usage[1]}, "result": result},
                ensure_ascii=False, indent=1), encoding="utf-8")
            saved += 1
            print(f"  [{n}/{len(todo)}] {s['title']}", flush=True)
    print(f"Saved {saved} sections; {len(failed)} failed. Tokens: {tokens_in:,} in, {tokens_out:,} out "
          f"(about ${cost(tokens_in, tokens_out, batch=False):.2f} at standard prices).")
    for sid, why in failed:
        print(f"  failed {sid}: {why}")
    if failed:
        print(f"Run `run {args.slug} --yes` again to retry only the failed sections.")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="python -m annotate.run", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, fn in (("estimate", cmd_estimate), ("run", cmd_run)):
        p = sub.add_parser(name)
        p.add_argument("slug", help="book folder name, e.g. metamorphoses")
        p.add_argument("--limit", type=int, help="only the first N pending sections")
        p.add_argument("--only", help="only this section id, e.g. metamorphoses-body-2")
        if name == "run":
            p.add_argument("--yes", action="store_true", help="confirm that you want to spend money")
            p.add_argument("--workers", type=int, default=4, help="sections annotated at the same time")
        p.set_defaults(run=fn)
    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
