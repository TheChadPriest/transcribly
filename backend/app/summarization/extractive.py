"""Extractive study-material generator.

Everything it outputs is either verbatim transcript sentences (with their timestamps) or
phrases that occur in the transcript. It never writes new claims, so it cannot hallucinate
content - at the cost of reading less smoothly than an abstractive model.
"""

from __future__ import annotations

import math
import re
from collections import Counter

import numpy as np

from ..models import (
    Concept,
    Conclusion,
    Highlight,
    KeyPoint,
    StudyMaterials,
    Topic,
    Transcript,
)
from ..transcription.segments import format_timestamp
from .text import (
    STOPWORDS,
    Sentence,
    content_tokens,
    display_case,
    keyphrases,
    split_sentences,
)

NOISE = re.compile(
    r"\b(subscribe|thanks? (you )?for watching|patreon|sponsor(ed)?|link (is )?in the description|"
    r"see you (next time|in the next)|smash (that|the) like|hit the bell|leave a comment)\b",
    re.IGNORECASE,
)
CONCLUSION_CUES = re.compile(
    r"\b(in conclusion|to conclude|to sum (it )?up|in summary|to summari[sz]e|overall|ultimately|"
    r"the (key|main|big|important) (point|takeaway|lesson|idea)|the bottom line|this (shows|means|suggests|demonstrates)|"
    r"therefore|we (found|conclude|argue|showed)|what (this|that) tells us|the lesson here|in the end)\b",
    re.IGNORECASE,
)
DEFINITION_TEMPLATES = (
    r"\b{t}\b,?\s+(is|are|was|were)\s+(a|an|the|basically|essentially|simply|defined as|known as|what|when|how)\b",
    r"\b{t}\b\s+(refers to|means|describes|is defined as|can be defined as|stands for)\b",
    r"\b(called|known as|termed|referred to as)\s+(an?\s+|the\s+)?['\"“]?{t}\b",
    r"\b(define|definition of)\s+{t}\b",
)


def _quality(s: Sentence) -> float:
    n = len(s.text.split())
    if n < 6 or NOISE.search(s.text):
        return 0.0
    if n > 70:
        return 0.6
    return min(1.0, n / 12)


def _tfidf(sentences: list[Sentence], max_vocab: int = 6000) -> np.ndarray:
    docs = [content_tokens(s.text) for s in sentences]
    df: Counter[str] = Counter()
    for d in docs:
        df.update(set(d))
    vocab = [w for w, _ in df.most_common(max_vocab)]
    index = {w: i for i, w in enumerate(vocab)}
    n = len(docs)
    x = np.zeros((n, max(1, len(vocab))), dtype=np.float32)
    for r, d in enumerate(docs):
        for w, c in Counter(d).items():
            if w in index:
                x[r, index[w]] = (1 + math.log(c)) * math.log((1 + n) / (1 + df[w]) + 1)
    norms = np.linalg.norm(x, axis=1, keepdims=True)
    norms[norms == 0] = 1
    return x / norms


def _textrank(sim: np.ndarray, iters: int = 40, d: float = 0.85) -> np.ndarray:
    n = sim.shape[0]
    if n == 0:
        return np.zeros(0)
    w = sim.copy()
    np.fill_diagonal(w, 0)
    w[w < 0.05] = 0
    rows = w.sum(axis=1, keepdims=True)
    rows[rows == 0] = 1
    w = w / rows
    r = np.full(n, 1.0 / n)
    for _ in range(iters):
        r = (1 - d) / n + d * (w.T @ r)
    return r / r.max() if r.max() > 0 else r


def _segment_topics(x: np.ndarray, sentences: list[Sentence], duration: float) -> list[tuple[int, int]]:
    """TextTiling-style segmentation: cut where lexical similarity between neighbouring
    windows dips most. Returns [start, end) sentence index ranges."""
    n = len(sentences)
    minutes = duration / 60
    max_topics = int(max(1, min(20, round(math.sqrt(minutes) * 1.6))))
    if n < 12 or max_topics == 1:
        return [(0, n)]
    k = int(min(12, max(3, n // 30)))
    gaps = []
    for g in range(k, n - k + 1):
        left = x[g - k:g].sum(axis=0)
        right = x[g:g + k].sum(axis=0)
        denom = (np.linalg.norm(left) * np.linalg.norm(right)) or 1.0
        gaps.append((g, float(left @ right / denom)))
    if not gaps:
        return [(0, n)]
    sims = np.array([s for _, s in gaps])
    depths = []
    for i, (g, s) in enumerate(gaps):
        # local peaks within a bounded neighbourhood (classic TextTiling uses hill climbing)
        lo, hi = max(0, i - 3 * k), min(len(sims), i + 3 * k + 1)
        lpeak, rpeak = sims[lo:i + 1].max(), sims[i:hi].max()
        depths.append((g, (lpeak - s) + (rpeak - s)))
    dvals = np.array([d for _, d in depths])
    threshold = dvals.mean() - dvals.std() / 2 if len(dvals) > 1 else 0
    min_len_s = max(30.0, duration / 25)
    min_sents = max(4, k)
    chosen: list[int] = []
    for g, dv in sorted(depths, key=lambda t: -t[1]):
        if dv < threshold or len(chosen) >= max_topics - 1:
            break
        cand = sorted(chosen + [g])
        bounds = [0] + cand + [n]
        ok = all(
            b - a >= min_sents and sentences[b - 1].end - sentences[a].start >= min_len_s
            for a, b in zip(bounds[:-1], bounds[1:])
        )
        if ok:
            chosen.append(g)
    bounds = [0] + sorted(chosen) + [n]
    return list(zip(bounds[:-1], bounds[1:]))


def _highlight(sents: list[Sentence]) -> Highlight:
    seg_ids = sorted({i for s in sents for i in s.segment_indices})
    return Highlight(segment_indices=seg_ids, start=sents[0].start, end=sents[-1].end,
                     excerpt=" ".join(s.text for s in sents))


def _phrase_in(phrase: str, text: str) -> bool:
    return re.search(r"\b" + r"\s+".join(map(re.escape, phrase.split())) + r"\b", text, re.IGNORECASE) is not None


class ExtractiveSummarizer:
    name = "extractive"

    def summarize(self, transcript: Transcript, title: str | None = None) -> StudyMaterials:
        sentences = split_sentences(transcript.segments)
        corpus = " ".join(s.text for s in sentences)
        total_words = len(corpus.split())
        duration = transcript.duration or (transcript.segments[-1].end if transcript.segments else 0)
        limitations = self._limitations(transcript)

        if total_words < 40 or len(sentences) < 3:
            return StudyMaterials(
                method=self.name,
                executive_summary=corpus,
                overview=corpus,
                key_points=[], concepts=[], conclusions=[],
                topics=[Topic(id="t1", title=title or "Full recording", summary=corpus, start=0,
                              end=duration, passages=[_highlight(sentences)] if sentences else [])],
                study_questions=[],
                limitations=limitations + ["The transcript is too short to build a meaningful summary, so the full text is shown instead."],
            )

        x = _tfidf(sentences)
        sim = x @ x.T
        quality = np.array([_quality(s) for s in sentences])
        rank = _textrank(sim) * quality
        phrases = keyphrases([s.text for s in sentences], top=40)

        topic_ranges = _segment_topics(x, sentences, duration)
        topic_of = np.zeros(len(sentences), dtype=int)
        for t, (a, b) in enumerate(topic_ranges):
            topic_of[a:b] = t

        topics = self._topics(sentences, topic_ranges, rank, corpus, title)
        concepts = self._concepts(sentences, rank, phrases, corpus)
        key_points = self._key_points(sentences, sim, rank, topic_of, phrases, corpus, total_words, concepts)
        executive, overview = self._executive(sentences, sim, rank, topic_ranges, total_words)
        conclusions = self._conclusions(sentences, rank)
        questions = self._questions(concepts, topics, conclusions)

        return StudyMaterials(
            method=self.name,
            executive_summary=executive,
            overview=overview,
            key_points=key_points,
            concepts=concepts,
            conclusions=conclusions,
            topics=topics,
            study_questions=questions,
            limitations=limitations,
        )

    # -- pieces ----------------------------------------------------------------
    def _executive(self, sentences, sim, rank, topic_ranges, total_words) -> tuple[str, str]:
        budget = int(min(300, max(70, total_words * 0.08)))
        chosen: list[int] = []
        words = 0

        def try_add(i: int) -> bool:
            nonlocal words
            if rank[i] <= 0 or i in chosen or any(sim[i, j] > 0.45 for j in chosen):
                return False
            n = len(sentences[i].text.split())
            if words + n > budget * 1.15 and chosen:
                return False
            chosen.append(i)
            words += n
            return True

        # first, the strongest sentence of each topic (coverage), then fill by global rank
        for a, b in topic_ranges:
            if words >= budget:
                break
            best = max(range(a, b), key=lambda i: rank[i])
            try_add(best)
        for i in np.argsort(-rank):
            if words >= budget:
                break
            try_add(int(i))
        chosen.sort()
        executive = " ".join(sentences[i].text for i in chosen)
        top2 = sorted(int(i) for i in np.argsort(-rank)[:2] if rank[int(i)] > 0)
        overview = " ".join(sentences[i].text for i in top2)
        return executive, overview

    def _heading(self, text: str, phrases, corpus: str, used: set[str], concepts: list[Concept]) -> str:
        # Prefer a defined concept, then a multi-word phrase; the single most frequent word of the
        # whole video (its overall theme) makes a poor heading, so it comes last.
        theme = phrases[0][0] if phrases else None
        ordered = [c.term.lower() for c in concepts] + [p for p, _, _ in phrases if " " in p] +                   [p for p, _, _ in phrases if " " not in p and p != theme] + ([theme] if theme else [])
        for p in ordered:
            if p not in used and _phrase_in(p, text):
                used.add(p)
                return display_case(p, corpus)
        words = [w for w in re.findall(r"[\w'’-]+", text)]
        content = [w for w in words if w.lower() not in STOPWORDS and len(w) > 2][:4]
        h = " ".join(content) or " ".join(words[:5])
        return h[:1].upper() + h[1:]

    def _key_points(self, sentences, sim, rank, topic_of, phrases, corpus, total_words, concepts) -> list[KeyPoint]:
        candidates = [i for i in range(len(sentences)) if rank[i] > 0]
        target = int(min(12, max(3, round(total_words / 350))))
        target = min(target, max(1, len(candidates) // 2))
        selected: list[int] = []
        lam = 0.7
        n_topics = int(topic_of.max()) + 1
        per_topic_cap = max(1, math.ceil(target / n_topics) + 1)
        while len(selected) < target and candidates:
            best, best_score = None, -1e9
            for i in candidates:
                if sum(1 for j in selected if topic_of[j] == topic_of[i]) >= per_topic_cap:
                    continue
                redundancy = max((sim[i, j] for j in selected), default=0.0)
                score = lam * rank[i] - (1 - lam) * redundancy
                if score > best_score:
                    best, best_score = i, score
            if best is None:
                break
            selected.append(best)
            candidates.remove(best)
        selected.sort()
        used: set[str] = set()
        points = []
        for n, i in enumerate(selected):
            group = [sentences[i]]
            nxt = i + 1
            if (len(sentences[i].text.split()) < 18 and nxt < len(sentences) and topic_of[nxt] == topic_of[i]
                    and nxt not in selected and _quality(sentences[nxt]) > 0):
                group.append(sentences[nxt])
            text = " ".join(s.text for s in group)
            points.append(KeyPoint(
                id=f"k{n + 1}",
                heading=self._heading(sentences[i].text, phrases, corpus, used, concepts),
                text=text,
                timestamp=sentences[i].start,
                support=_highlight(group),
            ))
        return points

    def _topics(self, sentences, ranges, rank, corpus, title) -> list[Topic]:
        sec_texts = [" ".join(s.text for s in sentences[a:b]) for a, b in ranges]
        n_sec = len(ranges)
        overall = keyphrases(sec_texts, top=1, min_count=1)
        theme = overall[0][0] if overall else None
        topics: list[Topic] = []
        used_titles: set[str] = set()
        for t, (a, b) in enumerate(ranges):
            local = keyphrases([s.text for s in sentences[a:b]], top=25, min_count=2 if b - a > 15 else 1)
            scored = []
            for p, sc, c in local:
                spread = max(1, sum(1 for txt in sec_texts if _phrase_in(p, txt)))
                scored.append((p, sc * math.log(1 + n_sec / spread) if n_sec > 1 else sc, c))
            scored.sort(key=lambda z: -z[1])
            names = [p for p, _, _ in scored if p not in used_titles]
            title_parts = self._title_parts(names, theme if n_sec > 1 else None)
            used_titles.update(title_parts)
            if title_parts:
                ttl = " and ".join(display_case(p, corpus) for p in title_parts)
                ttl = ttl[:1].upper() + ttl[1:]
            else:
                ttl = title if n_sec == 1 and title else f"Part {t + 1}"
            idx = sorted(range(a, b), key=lambda i: -rank[i])
            best = [i for i in idx if rank[i] > 0][:3]
            summary = sentences[best[0]].text if best else sentences[a].text
            passages = [_highlight([sentences[i]]) for i in sorted(best[:2])]
            topics.append(Topic(
                id=f"t{t + 1}", title=ttl, summary=summary,
                start=sentences[a].start, end=sentences[b - 1].end,
                subtopics=[display_case(p, corpus) for p in names if p not in title_parts][:3],
                passages=passages,
            ))
        return topics

    @staticmethod
    def _title_parts(names: list[str], theme: str | None) -> list[str]:
        """Up to two distinctive, non-overlapping phrases. The video's overall theme word and
        verb-like single words (-ing/-ed/-ly) are only used when nothing better exists."""
        def weak(p: str) -> bool:
            return p == theme or (" " not in p and p.endswith(("ing", "ed", "ly")))

        ordered = [p for p in names if not weak(p)] + [p for p in names if weak(p)]
        parts: list[str] = []
        for p in ordered:
            words = set(p.split())
            if any(words & set(q.split()) for q in parts):
                continue
            parts.append(p)
            if len(parts) == 2:
                break
        return parts

    def _concepts(self, sentences, rank, phrases, corpus) -> list[Concept]:
        concepts: list[Concept] = []
        for p, _, count in phrases:
            if len(concepts) >= 10:
                break
            is_multi = " " in p
            if any(_phrase_in(p, c.term) or _phrase_in(c.term, p) for c in concepts):
                continue
            term_re = r"\s+".join(map(re.escape, p.split()))
            definition = None
            for s in sentences:
                if len(s.text.split()) > 60:
                    continue
                if any(re.search(tpl.format(t=term_re), s.text, re.IGNORECASE) for tpl in DEFINITION_TEMPLATES):
                    definition = s
                    break
            if definition is not None:
                concepts.append(Concept(term=display_case(p, corpus), explanation=definition.text,
                                        explanation_kind="definition", timestamp=definition.start, mentions=count))
            elif is_multi and count >= 3:
                mentions = [s for s in sentences if _phrase_in(p, s.text)]
                if not mentions:
                    continue
                best = max(mentions, key=lambda s: rank[s.index])
                if rank[best.index] <= 0:
                    continue
                concepts.append(Concept(term=display_case(p, corpus), explanation=best.text,
                                        explanation_kind="context", timestamp=best.start, mentions=count))
        return concepts

    def _conclusions(self, sentences, rank) -> list[Conclusion]:
        n = len(sentences)
        scored = []
        for i, s in enumerate(sentences):
            if rank[i] <= 0:
                continue
            score = rank[i]
            if CONCLUSION_CUES.search(s.text):
                score *= 2.5
            if i >= n * 0.8:
                score *= 1.6
            cue = CONCLUSION_CUES.search(s.text) is not None
            if cue or (i >= n * 0.8 and rank[i] > 0.5):
                scored.append((score, i))
        picked = sorted(i for _, i in sorted(scored, reverse=True)[:4])
        return [Conclusion(text=sentences[i].text, timestamp=sentences[i].start) for i in picked]

    def _questions(self, concepts, topics, conclusions) -> list[str]:
        qs: list[str] = []
        for c in concepts[:3]:
            qs.append(f"In your own words, explain “{c.term}” as it is used in the video "
                      f"({format_timestamp(c.timestamp)}). Why does it matter?")
        real_topics = [t for t in topics if not t.title.startswith("Part ")]
        if len(real_topics) >= 2:
            a, b = real_topics[0], real_topics[-1]
            qs.append(f"How does the discussion of {a.title} ({format_timestamp(a.start)}) connect to "
                      f"{b.title} ({format_timestamp(b.start)})?")
        if concepts:
            qs.append(f"Describe a situation outside this video where “{concepts[0].term}” would apply.")
        if conclusions:
            excerpt = conclusions[-1].text
            if len(excerpt) > 160:
                excerpt = excerpt[:157].rsplit(" ", 1)[0] + "…"
            qs.append(f"The speaker says: “{excerpt}” What evidence is offered for this, and is it convincing?")
        return qs if len(qs) >= 2 else []

    def _limitations(self, transcript: Transcript) -> list[str]:
        lim = ["This summary is extractive: it is assembled from sentences in the transcript rather than "
               "rewritten, so it reflects what was said, not independently verified facts."]
        if transcript.source == "auto_captions":
            lim.append("The transcript comes from the platform's automatically generated captions, which can "
                       "contain recognition errors and usually lack reliable punctuation.")
        elif transcript.source == "speech_recognition":
            lim.append("The transcript was produced by automatic speech recognition and may contain errors, "
                       "especially for names, technical terms, and overlapping speech.")
        segs = transcript.segments
        if segs:
            low = sum(1 for s in segs if s.low_confidence) / len(segs)
            if low > 0.1:
                lim.append(f"About {round(low * 100)}% of transcript segments were recognised with low confidence "
                           "(marked in the transcript). Check those passages against the recording.")
            if transcript.duration:
                covered = sum(s.end - s.start for s in segs) / transcript.duration
                if covered < 0.5:
                    lim.append("Speech was detected in less than half of the recording; the rest may be music, "
                               "silence, or unclear audio that was not transcribed.")
        if transcript.language and not transcript.language.startswith("en"):
            lim.append("Keyword and topic detection is tuned for English and may be less precise for this language.")
        return lim
