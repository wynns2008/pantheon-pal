"""Split a section's text into paragraphs and numbered sentences.

The same split is used by the annotation pass (the model gets numbered sentences and answers in those
numbers) and by the web reader (it shows the same sentences), so a note always lands on the right sentence.
"""
import re
from dataclasses import dataclass

# A sentence ends at . ! or ? (plus any closing quotes/brackets and footnote markers like [3]) followed by
# whitespace and the start of the next sentence. Names and abbreviations listed here never end a sentence.
_END_RE = re.compile(r"""([.!?]["'”’)\]}]*(?:\[\d+\])*)\s+(?=["'“‘({\[]?[A-Z])""")
_ABBREVIATIONS = {"mr", "mrs", "dr", "st", "viz", "etc", "ver", "vol", "cf", "vs", "no", "ch", "b.c", "a.d", "i.e", "e.g"}
_MARKER_RE = re.compile(r"\[(\d+|[A-Z])\]")
_TAG_RE = re.compile(r"^\[[IVXLC]+\.\s*[\d\-–, ]+\]$")


@dataclass
class Sentence:
    i: int                # index within the section, 0-based
    p: int                # paragraph index within the section, 0-based
    text: str
    footnotes: list[str]  # footnote labels cited in this sentence, e.g. ["3"]


def _split_paragraph(paragraph: str) -> list[str]:
    parts, start = [], 0
    for m in _END_RE.finditer(paragraph):
        before = paragraph[start:m.start(1) + 1].rstrip(".!?\"'”’)]}").split()
        word = before[-1].lower().strip("([{\"'“‘") if before else ""
        if m.group(1).startswith(".") and word in _ABBREVIATIONS:
            continue
        parts.append(paragraph[start:m.end(1)])
        start = m.end()
    parts.append(paragraph[start:])
    parts = [p.strip() for p in parts if p.strip()]
    # Interjections like "Lo!" or "Alas!" are not worth a sentence of their own: join them to the next one.
    merged: list[str] = []
    for part in parts:
        if merged and len(merged[-1].split()) <= 2:
            merged[-1] = f"{merged[-1]} {part}"
        else:
            merged.append(part)
    return merged


def split_sentences(text: str) -> list[Sentence]:
    """Paragraphs are separated by blank lines; wrapped lines inside a paragraph are joined."""
    out: list[Sentence] = []
    paragraphs = [" ".join(l.strip() for l in block.split("\n") if l.strip())
                  for block in re.split(r"\n\s*\n", text)]
    # Skip empty paragraphs and bare line-range tags such as "[V. 462-563]".
    paragraphs = [x for x in paragraphs if x and not _TAG_RE.match(x)]
    for p, paragraph in enumerate(paragraphs):
        for sentence in _split_paragraph(paragraph):
            out.append(Sentence(len(out), p, sentence, _MARKER_RE.findall(sentence)))
    return out
