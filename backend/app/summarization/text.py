"""Sentence splitting (with timestamps) and lightweight keyword statistics."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from ..models import Segment

STOPWORDS = set("""
a about above actually after again against ah all almost also although always am an and another any anyone anything
are aren't around as at back be became because become been before being below between both but by can can't cannot
could couldn't did didn't do does doesn't doing don't done down during each either else enough etc even ever every
everyone everything few first for from further get gets getting go goes going gonna gone got gotta had hadn't has
hasn't have haven't having he he'd he'll he's her here here's hers herself him himself his how how's however i i'd
i'll i'm i've if in into is isn't it it's its itself just kind know let let's like lot lots made make makes many may
maybe me might mine more most much must my myself need new no nor not now of off oh ok okay on once one ones only or
other others our ours ourselves out over own pretty put quite rather re really right said same say says see seem
seems she she'd she'll she's should shouldn't since so some something sort still such sure take than that that's the
their theirs them themselves then there there's these they they'd they'll they're they've thing things think this
those though through thus to too under until up upon us use used very via want wanna was wasn't way we we'd we'll
we're we've well were weren't what what's whatever when when's where where's whether which while who who's whom whose
why why's will with within without won't would wouldn't yeah yes yet you you'd you'll you're you've your yours
yourself yourselves um uh hmm mm also basically literally gonna going thank thanks welcome today video channel
subscribe guys everybody okay alright yep nope look looking talk talking talked tell told mean means meant two three
""".split())

_SENT_END = re.compile(r"(?<=[.!?。！？])[\"')\]]*\s+(?=[\"'(\[]?[A-Z0-9À-ɏЀ-ӿ֐-ۿ぀-鿿])")
_WORD = re.compile(r"[^\W\d_][\w'’-]*", re.UNICODE)
_CLAUSE = re.compile(r"[,.;:!?()\[\]\"“”…—–]+|\s-\s")
ABBREV = ("Mr.", "Mrs.", "Ms.", "Dr.", "Prof.", "St.", "vs.", "e.g.", "i.e.", "etc.", "U.S.", "Jr.", "Sr.", "No.")


@dataclass
class Sentence:
    index: int
    text: str
    start: float
    end: float
    segment_indices: list[int]


def split_sentences(segments: list[Segment], max_words: int = 45) -> list[Sentence]:
    """Rebuild sentences across segment boundaries while remembering which segments each covers.

    Unpunctuated transcripts (common with auto-captions) are split into ~max_words pieces at
    segment boundaries, so every 'sentence' is still verbatim transcript text."""
    sentences: list[Sentence] = []
    punctuated = has_punctuation(segments)

    def emit(text: str, segs: list[Segment]) -> None:
        text = text.strip()
        if text and segs:
            sentences.append(Sentence(len(sentences), text, segs[0].start, segs[-1].end,
                                      sorted({s.index for s in segs})))

    text = ""
    segs: list[Segment] = []
    for seg in segments:
        pieces = _split_text(seg.text)
        for j, piece in enumerate(pieces):
            text = f"{text} {piece}".strip()
            if not segs or segs[-1] is not seg:
                segs.append(seg)
            words = len(text.split())
            mid_segment_end = j < len(pieces) - 1
            if (mid_segment_end or _ends_sentence(piece) or (not punctuated and words >= max_words)
                    or words >= max_words * 3):
                emit(text, segs)
                text, segs = "", []
    emit(text, segs)
    return sentences


def has_punctuation(segments: list[Segment]) -> bool:
    sample = " ".join(s.text for s in segments[:300])
    words = max(1, len(sample.split()))
    return sample.count(".") + sample.count("?") + sample.count("!") > words / 60


def _ends_sentence(text: str) -> bool:
    t = text.rstrip("\"')] ")
    return t.endswith((".", "!", "?", "。", "！", "？")) and not t.endswith(ABBREV)


def _split_text(text: str) -> list[str]:
    parts = _SENT_END.split(text)
    merged: list[str] = []
    for p in parts:
        if merged and merged[-1].endswith(ABBREV):
            merged[-1] = f"{merged[-1]} {p}"
        else:
            merged.append(p)
    return [p for p in (m.strip() for m in merged) if p]


def tokens(text: str) -> list[str]:
    return [w.lower().strip("'’-") for w in _WORD.findall(text)]


def content_tokens(text: str) -> list[str]:
    return [w for w in tokens(text) if len(w) > 2 and w not in STOPWORDS]


def keyphrases(texts: list[str], top: int = 30, min_count: int = 2) -> list[tuple[str, float, int]]:
    """Rank unigrams and bigrams/trigrams of content words by frequency * spread.

    Returns (phrase, score, count). Phrases are lowercase; display casing is recovered later."""
    counts: Counter[str] = Counter()
    doc_freq: Counter[str] = Counter()
    for t in texts:
        seen: set[str] = set()
        # n-grams never cross punctuation ("rain, snow" is not the phrase "rain snow")
        runs = [tokens(piece) for piece in _CLAUSE.split(t)]
        for toks, n in ((r, n) for r in runs for n in (1, 2, 3)):
            for i in range(len(toks) - n + 1):
                gram = toks[i:i + n]
                if gram[0] in STOPWORDS or gram[-1] in STOPWORDS or any(len(g) < 3 for g in (gram[0], gram[-1])):
                    continue
                if n > 1 and any(g in STOPWORDS for g in gram[1:-1]) and n == 3 and gram[1] not in ("of", "and"):
                    continue
                phrase = " ".join(gram)
                counts[phrase] += 1
                seen.add(phrase)
        for p in seen:
            doc_freq[p] += 1
    scored: list[tuple[str, float, int]] = []
    for phrase, c in counts.items():
        if c < min_count:
            continue
        n = phrase.count(" ") + 1
        score = c * (1.6 if n == 2 else 2.0 if n == 3 else 1.0) * math.log1p(doc_freq[phrase])
        scored.append((phrase, score, c))
    scored.sort(key=lambda x: -x[1])
    # drop unigrams/bigrams subsumed by a stronger longer phrase
    out: list[tuple[str, float, int]] = []
    for p, sc, c in scored:
        if any(p != q and f" {p} " in f" {q} " and c <= qc * 1.3 for q, _, qc in out):
            continue
        out.append((p, sc, c))
        if len(out) >= top:
            break
    return out


def display_case(phrase: str, corpus: str) -> str:
    """Recover the most common original casing of a phrase from the transcript."""
    pattern = re.compile(r"\b" + r"\s+".join(map(re.escape, phrase.split())) + r"\b", re.IGNORECASE)
    found = Counter(m.group(0) for m in pattern.finditer(corpus))
    if not found:
        return phrase
    best = found.most_common(1)[0][0]
    if best.islower():
        return best[0].upper() + best[1:]
    return best
