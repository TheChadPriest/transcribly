"""Citation formatting for online videos and recordings.

Templates follow the published examples for online video in APA 7th, MLA 9th, Chicago 17th
(notes-bibliography and author-date) and Harvard (Cite Them Right). Missing data is never
invented: absent fields are omitted or replaced with the style's own placeholder (e.g. "n.d.").
"""

from __future__ import annotations

import datetime as dt
import re

from ..models import Citation, CitationMetadata, CitationRun, CitationSet

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]
MLA_MONTHS = ["Jan.", "Feb.", "Mar.", "Apr.", "May", "June", "July", "Aug.", "Sept.", "Oct.", "Nov.", "Dec."]
PARTICLES = {"van", "von", "de", "der", "da", "di", "du", "la", "le", "del", "den", "ter", "bin", "al"}


class _Runs:
    def __init__(self) -> None:
        self.runs: list[CitationRun] = []

    def add(self, text: str, italic: bool = False) -> "_Runs":
        if not text:
            return self
        if self.runs and self.runs[-1].italic == italic:
            self.runs[-1] = CitationRun(text=self.runs[-1].text + text, italic=italic)
        else:
            self.runs.append(CitationRun(text=text, italic=italic))
        return self

    def it(self, text: str) -> "_Runs":
        return self.add(text, True)

    @property
    def text(self) -> str:
        return "".join(r.text for r in self.runs)


# --- parsing helpers --------------------------------------------------------

def parse_date(value: str | None) -> tuple[int | None, int | None, int | None]:
    if not value:
        return None, None, None
    m = re.fullmatch(r"\s*(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?\s*", value)
    if not m:
        return None, None, None
    y = int(m[1])
    mo = int(m[2]) if m[2] and 1 <= int(m[2]) <= 12 else None
    d = int(m[3]) if m[3] and mo and 1 <= int(m[3]) <= 31 else None
    return y, mo, d


def split_name(full: str) -> tuple[str, list[str], str]:
    """'Jane Q. van Smith Jr.' -> ('van Smith', ['Jane', 'Q.'], 'Jr.')"""
    full = full.strip()
    if "," in full:  # already "Last, First"
        last, _, rest = full.partition(",")
        return last.strip(), rest.split(), ""
    parts = full.split()
    suffix = ""
    if len(parts) > 2 and parts[-1].rstrip(".").lower() in ("jr", "sr", "ii", "iii", "iv"):
        suffix = parts.pop()
    if len(parts) == 1:
        return parts[0], [], suffix
    i = len(parts) - 1
    while i > 1 and parts[i - 1].lower() in PARTICLES:
        i -= 1
    return " ".join(parts[i:]), parts[:i], suffix


def _initials(given: list[str], spaced: bool = True) -> str:
    out = []
    for g in given:
        sub = [p for p in g.split("-") if p]
        out.append("-".join(f"{p[0].upper()}." for p in sub))
    return (" " if spaced else "").join(out)


def apa_name(name: str, is_person: bool) -> str:
    if not is_person:
        return name
    last, given, suffix = split_name(name)
    s = f"{last}, {_initials(given)}" if given else last
    return f"{s}, {suffix}" if suffix else s


def inverted_name(name: str, is_person: bool) -> str:
    if not is_person:
        return name
    last, given, suffix = split_name(name)
    s = f"{last}, {' '.join(given)}" if given else last
    return f"{s}, {suffix}" if suffix else s


def harvard_name(name: str, is_person: bool) -> str:
    if not is_person:
        return name
    last, given, _ = split_name(name)
    return f"{last}, {_initials(given, spaced=False)}" if given else last


def family_name(name: str, is_person: bool) -> str:
    return split_name(name)[0] if is_person else name


def _end(text: str, mark: str = ".") -> str:
    """Append terminal punctuation unless the text already ends with some."""
    text = text.rstrip()
    return text if text.endswith((".", "?", "!")) else text + mark


def _quoted(title: str, mark: str) -> str:
    """Title inside curly quotes with the following punctuation inside (US style), unless
    the title ends with its own ? or !."""
    t = title.strip()
    if t.endswith(("?", "!")):
        return f"“{t}”"
    return f"“{t}{mark}”"


_TRAILING = {"a", "an", "the", "of", "and", "or", "in", "on", "for", "to", "with", "by", "at", "from"}


def short_title(title: str, words: int = 4) -> str:
    """Up to four words of the main title, never ending on an article or preposition."""
    main = re.split(r"[:?!]|\s[-–—|]\s", title, maxsplit=1)[0].strip() or title
    toks = main.split()[:words]
    while len(toks) > 1 and toks[-1].lower().strip(",") in _TRAILING:
        toks.pop()
    return " ".join(toks)


def duration_text(seconds: float | None) -> str | None:
    if not seconds:
        return None
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def _strip_scheme(url: str) -> str:
    return re.sub(r"^https?://", "", url)


def _medium(meta: CitationMetadata) -> str:
    return "Unpublished recording" if meta.source_type == "uploaded_file" else "Video"


def _chicago_medium(meta: CitationMetadata) -> str:
    if meta.platform:
        return f"{meta.platform} video"
    return "Video recording" if meta.source_type != "uploaded_file" else "Unpublished video recording"


# --- styles -------------------------------------------------------------------

def apa(meta: CitationMetadata) -> Citation:
    y, mo, d = parse_date(meta.published)
    date = "n.d." if y is None else f"{y}, {MONTHS[mo - 1]} {d}" if mo and d else f"{y}, {MONTHS[mo - 1]}" if mo else f"{y}"
    title = (meta.title or "Untitled video").strip()
    r = _Runs()
    author = meta.creator or meta.channel
    if author:
        name = apa_name(author, meta.creator_is_person and bool(meta.creator))
        if meta.creator and meta.creator_is_person and meta.channel and meta.channel != meta.creator:
            r.add(f"{name} [{meta.channel}]. ")
        else:
            r.add(_end(name) + " ")
        r.add(f"({date}). ")
        r.it(title).add(f" [{_medium(meta)}]. ")
    else:
        r.it(title).add(f" [{_medium(meta)}]. ").add(f"({date}). ")
    publisher = meta.platform or meta.publisher
    if publisher:
        r.add(_end(publisher) + " ")
    if meta.url and meta.source_type != "uploaded_file":
        r.add(meta.url)
    year = "n.d." if y is None else str(y)
    if author:
        cite_name = family_name(author, meta.creator_is_person and bool(meta.creator))
        in_text = f"({cite_name}, {year})"
    else:
        in_text = f"({short_title(title)}, {year})"
    return Citation(style="apa", label="APA 7th edition", reference=_trim(r), reference_text=_trim_text(r),
                    in_text=in_text)


def mla(meta: CitationMetadata) -> Citation:
    y, mo, d = parse_date(meta.published)
    date = None if y is None else f"{d} {MLA_MONTHS[mo - 1]} {y}" if mo and d else f"{MLA_MONTHS[mo - 1]} {y}" if mo else str(y)
    title = (meta.title or "Untitled video").strip()
    person = meta.creator_is_person and bool(meta.creator)
    r = _Runs()
    if person:
        r.add(_end(inverted_name(meta.creator or "", True)) + " ")
    elements: list[tuple[str, bool]] = []
    container = meta.platform
    uploader = meta.channel or (meta.creator if not person else None)
    if container:
        elements.append((container, True))
        if uploader and (not person or uploader != meta.creator):
            elements.append((f"uploaded by {uploader}", False))
    elif meta.publisher:
        elements.append((meta.publisher, False))
    if date:
        elements.append((date, False))
    if meta.url and meta.source_type != "uploaded_file":
        elements.append((_strip_scheme(meta.url), False))
    r.add(_quoted(title, "."))
    if elements:
        r.add(" ")
        for i, (text, italic) in enumerate(elements):
            r.add(text, italic)
            r.add(", " if i < len(elements) - 1 else ".")
    if meta.source_type == "uploaded_file":
        r.add(" Video recording.")
    if date is None and meta.accessed and meta.url:
        ay, am, ad = parse_date(meta.accessed)
        if ay and am and ad:
            r.add(f" Accessed {ad} {MLA_MONTHS[am - 1]} {ay}.")
    in_text = f"({family_name(meta.creator or '', True)})" if person else f"(“{short_title(title)}”)"
    return Citation(style="mla", label="MLA 9th edition", reference=_trim(r), reference_text=_trim_text(r),
                    in_text=in_text)


def _chicago_date(y, mo, d) -> str | None:
    if y is None:
        return None
    if mo and d:
        return f"{MONTHS[mo - 1]} {d}, {y}"
    if mo:
        return f"{MONTHS[mo - 1]} {y}"
    return str(y)


def chicago_notes(meta: CitationMetadata) -> Citation:
    y, mo, d = parse_date(meta.published)
    date = _chicago_date(y, mo, d)
    title = (meta.title or "Untitled video").strip()
    person = meta.creator_is_person and bool(meta.creator)
    dur = duration_text(meta.duration_seconds)
    medium = _chicago_medium(meta) + (f", {dur}" if dur else "")
    poster = meta.channel if (meta.channel and meta.channel != meta.creator) else None
    url = meta.url if meta.url and meta.source_type != "uploaded_file" else None

    b = _Runs()
    if meta.creator:
        b.add(_end(inverted_name(meta.creator, person)) + " ")
    b.add(_quoted(title, ".") + " ")
    b.add(f"{medium}. ")
    if poster and date:
        b.add(f"Posted by {poster}, {date}. ")
    elif poster:
        b.add(f"Posted by {poster}. ")
    elif date:
        b.add(f"{date}. ")
    if url:
        b.add(_end(url))

    n = _Runs()
    if meta.creator:
        n.add(f"{meta.creator}, ")
    n.add(_quoted(title, ",") + " ")
    n.add(medium)
    if poster:
        n.add(f", posted by {poster}")
    if date:
        n.add(f", {date}")
    if url:
        n.add(f", {url}")
    n.add(".")
    short = f"{family_name(meta.creator, person)}, {_quoted(short_title(title), '.')}" if meta.creator else _quoted(short_title(title), ".")
    return Citation(style="chicago_notes", label="Chicago 17th ed. (notes and bibliography)",
                    reference=_trim(b), reference_text=_trim_text(b), note=n.text,
                    note_label="Footnote", in_text=f"Short note: {short}")


def chicago_author_date(meta: CitationMetadata) -> Citation:
    y, mo, d = parse_date(meta.published)
    date = _chicago_date(y, mo, d)
    title = (meta.title or "Untitled video").strip()
    person = meta.creator_is_person and bool(meta.creator)
    dur = duration_text(meta.duration_seconds)
    year = str(y) if y else "n.d."
    poster = meta.channel if (meta.channel and meta.channel != meta.creator) else None
    r = _Runs()
    if meta.creator:
        r.add(_end(inverted_name(meta.creator, person)) + f" {year}. ")
        r.add(_quoted(title, ".") + " ")
    else:
        r.add(_quoted(title, ".") + f" {year}. ")
    r.add(_chicago_medium(meta) + (f", {dur}" if dur else "") + ". ")
    if poster and date and (mo or d):
        r.add(f"Posted by {poster}, {date}. ")
    elif poster:
        r.add(f"Posted by {poster}. ")
    elif date and (mo or d):
        r.add(f"{date}. ")
    if meta.url and meta.source_type != "uploaded_file":
        r.add(_end(meta.url))
    who = family_name(meta.creator, person) if meta.creator else _quoted(short_title(title), "")
    return Citation(style="chicago_author_date", label="Chicago 17th ed. (author-date)",
                    reference=_trim(r), reference_text=_trim_text(r), in_text=f"({who} {year})")


def harvard(meta: CitationMetadata) -> Citation:
    y, _, _ = parse_date(meta.published)
    year = str(y) if y else "no date"
    title = (meta.title or "Untitled video").strip()
    author = meta.creator or meta.channel
    person = meta.creator_is_person and bool(meta.creator)
    r = _Runs()
    if author:
        r.add(f"{harvard_name(author, person)} ({year}) ")
        r.it(title)
    else:
        r.it(title).add(f" ({year})")
    if meta.harvard_include_medium:
        r.add(f" [{_medium(meta)}]")
        if meta.platform:
            r.add(f". {meta.platform}")
    r.add(". ")
    if meta.url and meta.source_type != "uploaded_file":
        r.add(f"Available at: {meta.url}")
        ay, am, ad = parse_date(meta.accessed)
        if ay and am and ad:
            r.add(f" (Accessed: {ad} {MONTHS[am - 1]} {ay})")
        r.add(".")
    who = family_name(author, person) if author else short_title(title)
    return Citation(style="harvard", label="Harvard (Cite Them Right)", reference=_trim(r),
                    reference_text=_trim_text(r), in_text=f"({who}, {year})")


def _trim(r: _Runs) -> list[CitationRun]:
    runs = list(r.runs)
    if runs:
        runs[-1] = CitationRun(text=runs[-1].text.rstrip(), italic=runs[-1].italic)
    return [x for x in runs if x.text]


def _trim_text(r: _Runs) -> str:
    return r.text.strip()


STYLES = {
    "apa": apa,
    "mla": mla,
    "chicago_notes": chicago_notes,
    "chicago_author_date": chicago_author_date,
    "harvard": harvard,
}


def warnings_for(meta: CitationMetadata) -> list[str]:
    w = []
    if not meta.creator and not meta.channel:
        w.append("No creator could be identified, so citations start with the title. Add the creator if you know it.")
    if not meta.published:
        w.append("No publication date was found, so “n.d.”/“no date” is used. Add the date if you know it.")
    if not meta.title:
        w.append("No title was found. Enter the video's title.")
    if meta.source_type == "uploaded_file":
        w.append("This is an uploaded file with no public source. If the recording was published somewhere, "
                 "add its creator, date, platform and URL.")
    if meta.creator and not meta.creator_is_person:
        w.append("The creator is treated as an organization or channel name. If it is a person, mark it as a person "
                 "so names are inverted and abbreviated correctly.")
    w.append("APA asks for video titles in sentence case and some institutions modify these styles. "
             "Check your required style guide before submitting.")
    return w


def format_all(meta: CitationMetadata) -> CitationSet:
    if not meta.accessed:
        meta = meta.model_copy(update={"accessed": dt.date.today().isoformat()})
    return CitationSet(citations=[fn(meta) for fn in STYLES.values()], warnings=warnings_for(meta))
