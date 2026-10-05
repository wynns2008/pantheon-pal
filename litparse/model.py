"""Generic data model for parsed books. Nothing here is specific to any one book."""
from dataclasses import dataclass, field

# What a section is, not where it sits in the book's hierarchy (that is `path`).
CATEGORIES = (
    "boilerplate",   # Gutenberg header/licence
    "front_matter",  # title page, introduction, table of contents, synopsis
    "body",          # the story/poem text itself
    "commentary",    # translator's notes or explanations that follow the body
    "appendix",      # appendices, genealogies, indexes
    "back_matter",   # errata, transcriber's notes, publisher ads
)


@dataclass
class Footnote:
    label: str   # "1", "A", ...
    text: str


@dataclass
class Section:
    id: str                    # stable, e.g. "aeneid-body-3"
    book_slug: str             # "aeneid"
    category: str              # one of CATEGORIES
    title: str                 # "Book 2" / "Chapter 5: The Council of Elrond"
    path: list[str]            # hierarchy, any depth: ["Volume 1", "Book 2", "Chapter 5"]
    line_start: int            # 1-indexed in source.txt, inclusive
    line_end: int
    text: str
    reference: str | None = None            # e.g. "I.5-31" when the book gives one
    footnotes: list[Footnote] = field(default_factory=list)


@dataclass
class ParsedBook:
    slug: str
    title: str | None
    sections: list[Section]
    report: dict               # what auto-detection found, printed after each parse
