"use client";

import { ChevronDown, ChevronRight, ChevronUp, ExternalLink, Search } from "lucide-react";
import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { formatTimestamp, paragraphText, parseTimestamp, segmentMap, timestampUrl, transcriptText } from "@/lib/format";
import type { KeyPoint, Paragraph, Results, Segment } from "@/lib/types";
import { Button, CopyButton, Notice } from "../ui";

export interface FocusRequest {
  segment: number;
  /** every line of the referenced passage, shown with a highlight until the next navigation */
  segments?: number[];
  nonce: number;
}

interface Section {
  id: string;
  title: string | null;
  start: number;
  end: number;
  paragraphs: Paragraph[];
}

function buildSections(r: Results): Section[] {
  const paras = r.transcript.paragraphs.length
    ? r.transcript.paragraphs
    : r.transcript.segments.map((s, i) => ({ index: i, start: s.start, end: s.end, segment_indices: [s.index] }));
  const topics = r.study?.topics ?? [];
  if (topics.length < 2) {
    return [{ id: "all", title: null, start: paras[0]?.start ?? 0, end: paras.at(-1)?.end ?? 0, paragraphs: paras }];
  }
  const sections: Section[] = topics.map((t) => ({ id: t.id, title: t.title, start: t.start, end: t.end, paragraphs: [] }));
  for (const p of paras) {
    let idx = 0;
    for (let i = 0; i < topics.length; i++) if (p.start >= topics[i].start - 0.01) idx = i;
    sections[idx].paragraphs.push(p);
  }
  return sections.filter((s) => s.paragraphs.length);
}

function escapeRegExp(s: string) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export function TranscriptView({
  results,
  highlights,
  setHighlights,
  focus,
  onFocusKeyPoint,
}: {
  results: Results;
  highlights: boolean;
  setHighlights: (v: boolean) => void;
  focus: FocusRequest | null;
  onFocusKeyPoint: (k: KeyPoint) => void;
}) {
  const { transcript, metadata } = results;
  const [showTimes, setShowTimes] = useState(true);
  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");
  const [current, setCurrent] = useState(0);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [jump, setJump] = useState("");
  const [jumpError, setJumpError] = useState<string | null>(null);
  const container = useRef<HTMLDivElement>(null);

  const byIndex = useMemo(() => segmentMap(transcript.segments), [transcript.segments]);
  const sections = useMemo(() => buildSections(results), [results]);
  const sectionOfSegment = useMemo(() => {
    const m = new Map<number, string>();
    for (const s of sections) for (const p of s.paragraphs) for (const i of p.segment_indices) m.set(i, s.id);
    return m;
  }, [sections]);

  // segment index -> key point it supports (for highlighting)
  const support = useMemo(() => {
    const m = new Map<number, KeyPoint>();
    for (const k of results.study?.key_points ?? []) for (const i of k.support?.segment_indices ?? []) m.set(i, k);
    return m;
  }, [results.study]);

  useEffect(() => {
    const t = setTimeout(() => {
      setDebounced(query.trim());
      setCurrent(0);
    }, 150);
    return () => clearTimeout(t);
  }, [query]);

  const regex = useMemo(() => (debounced.length >= 2 ? new RegExp(escapeRegExp(debounced), "gi") : null), [debounced]);

  // Ordered list of matches: [segmentIndex, occurrence]
  const matches = useMemo(() => {
    if (!regex) return [] as [number, number][];
    const out: [number, number][] = [];
    for (const s of sections)
      for (const p of s.paragraphs)
        for (const i of p.segment_indices) {
          const text = byIndex.get(i)?.text ?? "";
          const n = text.match(regex)?.length ?? 0;
          for (let k = 0; k < n; k++) out.push([i, k]);
        }
    return out;
  }, [regex, sections, byIndex]);

  const scrollToId = (id: string, flash = false) => {
    requestAnimationFrame(() => {
      const el = document.getElementById(id);
      if (!el) return;
      el.scrollIntoView({ block: "center", behavior: "smooth" });
      if (flash) {
        el.classList.remove("flash");
        void el.offsetWidth;
        el.classList.add("flash");
      }
    });
  };

  // A collapsed section opens while it holds the current search match or a navigation target.
  const [jumpTarget, setJumpTarget] = useState<number | null>(null);
  const forcedOpen = new Set<string>();
  for (const seg of [matches[current]?.[0], focus?.segment, jumpTarget]) {
    const sid = seg != null ? sectionOfSegment.get(seg) : undefined;
    if (sid) forcedOpen.add(sid);
  }

  useEffect(() => {
    if (!matches.length) return;
    const [seg, k] = matches[current] ?? matches[0];
    scrollToId(`match-${seg}-${k}`);
  }, [current, matches]);

  useEffect(() => {
    if (focus) scrollToId(`seg-${focus.segment}`, true);
  }, [focus]);
  const focusSet = useMemo(() => new Set(focus?.segments ?? []), [focus]);

  const goToTime = () => {
    const t = parseTimestamp(jump);
    if (t === null) {
      setJumpError("Use a time like 1:05 or 01:02:30.");
      return;
    }
    setJumpError(null);
    let target: Segment | undefined = transcript.segments[0];
    for (const s of transcript.segments) if (s.start <= t + 0.01) target = s;
    if (target) {
      setJumpTarget(target.index);
      scrollToId(`seg-${target.index}`, true);
    }
  };

  const active = matches[current];
  const renderText = (seg: Segment) => {
    if (!regex) return seg.text;
    const parts: React.ReactNode[] = [];
    let last = 0;
    let k = 0;
    for (const m of seg.text.matchAll(regex)) {
      const at = m.index ?? 0;
      parts.push(seg.text.slice(last, at));
      const isCurrent = active?.[0] === seg.index && active?.[1] === k;
      parts.push(
        <mark key={at} id={`match-${seg.index}-${k++}`} className={isCurrent ? "current" : ""}>
          {m[0]}
        </mark>,
      );
      last = at + m[0].length;
    }
    parts.push(seg.text.slice(last));
    return parts;
  };

  const lowCount = transcript.segments.filter((s) => s.low_confidence).length;

  return (
    <div ref={container}>
      <div className="sticky top-14 z-20 -mx-1 mb-4 border-b border-border bg-bg/95 px-1 pb-3 pt-1 backdrop-blur">
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex h-9 min-w-[220px] flex-1 items-center gap-2 rounded-md border border-border bg-surface px-2.5 focus-within:border-accent">
            <Search size={15} className="shrink-0 text-faint" aria-hidden />
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && matches.length) {
                  e.preventDefault();
                  setCurrent((c) => (e.shiftKey ? (c - 1 + matches.length) % matches.length : (c + 1) % matches.length));
                }
              }}
              placeholder="Search transcript"
              aria-label="Search transcript"
              className="h-full min-w-0 flex-1 bg-transparent text-sm text-text placeholder:text-faint focus:outline-none"
            />
            {debounced.length >= 2 && (
              <span className="shrink-0 text-[12px] tabular-nums text-faint" aria-live="polite">
                {matches.length ? `${current + 1} of ${matches.length}` : "No matches"}
              </span>
            )}
            <button
              type="button"
              aria-label="Previous match"
              disabled={!matches.length}
              onClick={() => setCurrent((c) => (c - 1 + matches.length) % matches.length)}
              className="rounded p-0.5 text-faint hover:text-text disabled:opacity-40"
            >
              <ChevronUp size={16} aria-hidden />
            </button>
            <button
              type="button"
              aria-label="Next match"
              disabled={!matches.length}
              onClick={() => setCurrent((c) => (c + 1) % matches.length)}
              className="rounded p-0.5 text-faint hover:text-text disabled:opacity-40"
            >
              <ChevronDown size={16} aria-hidden />
            </button>
          </div>
          <form
            className="flex h-9 items-center gap-1 rounded-md border border-border bg-surface pl-2.5 focus-within:border-accent"
            onSubmit={(e) => {
              e.preventDefault();
              goToTime();
            }}
          >
            <input
              value={jump}
              onChange={(e) => setJump(e.target.value)}
              placeholder="00:00:00"
              aria-label="Jump to time (for example 1:05 or 01:02:30)"
              aria-invalid={!!jumpError}
              className="h-full w-[76px] bg-transparent font-mono text-[13px] text-text placeholder:text-faint focus:outline-none"
            />
            <Button type="submit" variant="ghost" size="sm" className="h-[34px]">
              Go
            </Button>
          </form>
        </div>
        {jumpError && <p className="mt-1 text-[12px] text-danger">{jumpError}</p>}
        <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-2">
          <label className="flex items-center gap-1.5 text-[13px] text-muted">
            <input type="checkbox" checked={showTimes} onChange={(e) => setShowTimes(e.target.checked)} className="size-3.5 accent-[var(--accent)]" />
            Timestamps
          </label>
          {support.size > 0 && (
            <label className="flex items-center gap-1.5 text-[13px] text-muted">
              <input type="checkbox" checked={highlights} onChange={(e) => setHighlights(e.target.checked)} className="size-3.5 accent-[var(--accent)]" />
              Highlight key points
            </label>
          )}
          <div className="ml-auto flex flex-wrap gap-1.5">
            <CopyButton label="Transcript" getText={() => transcriptText(transcript.segments, transcript.paragraphs, false)}>
              Copy text
            </CopyButton>
            <CopyButton label="Transcript with timestamps" getText={() => transcriptText(transcript.segments, transcript.paragraphs, true)}>
              Copy with timestamps
            </CopyButton>
          </div>
        </div>
      </div>

      {lowCount > 0 && (
        <div className="mb-4">
          <Notice>
            Passages with a <span className="seg-low">dotted underline</span> were recognised with low confidence ({lowCount} of{" "}
            {transcript.segments.length} segments). Check them against the recording.
          </Notice>
        </div>
      )}

      <div className="space-y-6">
        {sections.map((section) => {
          const isCollapsed = collapsed.has(section.id) && !forcedOpen.has(section.id);
          const sectionText = () =>
            section.paragraphs
              .map((p) => (showTimes ? `[${formatTimestamp(p.start)}] ` : "") + paragraphText(p, byIndex))
              .join("\n\n");
          return (
            <section key={section.id} aria-label={section.title ?? "Transcript"}>
              {section.title && (
                <div className="mb-2 flex items-center gap-2 border-b border-border pb-1.5">
                  <button
                    type="button"
                    onClick={() =>
                      setCollapsed((c) => {
                        const n = new Set(c);
                        if (n.has(section.id)) n.delete(section.id);
                        else n.add(section.id);
                        return n;
                      })
                    }
                    aria-expanded={!isCollapsed}
                    className="flex min-w-0 flex-1 items-center gap-1.5 rounded text-left"
                  >
                    {isCollapsed ? <ChevronRight size={16} className="shrink-0 text-faint" aria-hidden /> : <ChevronDown size={16} className="shrink-0 text-faint" aria-hidden />}
                    <span className="truncate text-[13px] font-semibold uppercase tracking-wide text-muted">{section.title}</span>
                    <span className="shrink-0 font-mono text-[12px] text-faint">
                      {formatTimestamp(section.start)}–{formatTimestamp(section.end)}
                    </span>
                  </button>
                  <CopyButton label="Section" getText={sectionText} iconOnly variant="ghost" />
                </div>
              )}
              {!isCollapsed && (
                <div className="space-y-4">
                  {section.paragraphs.map((p) => {
                    const link = timestampUrl(metadata, p.start);
                    return (
                      <div key={p.index} id={`para-${p.index}`} className="group grid grid-cols-1 gap-1 sm:grid-cols-[76px_1fr] sm:gap-3">
                        {showTimes && (
                          <div className="pt-[3px] font-mono text-[12px] text-faint">
                            {link ? (
                              <a href={link} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 hover:text-accent" title="Open the video at this time">
                                {formatTimestamp(p.start)}
                                <ExternalLink size={11} className="opacity-0 group-hover:opacity-100" aria-hidden />
                              </a>
                            ) : (
                              formatTimestamp(p.start)
                            )}
                          </div>
                        )}
                        <p className={`max-w-[72ch] text-[16px] leading-[1.75] text-text ${showTimes ? "" : "sm:col-span-2"}`}>
                          {p.segment_indices.map((i, n) => {
                            const seg = byIndex.get(i);
                            if (!seg) return null;
                            const kp = highlights ? support.get(i) : undefined;
                            const cls = [
                              kp ? "seg-hl cursor-pointer" : "",
                              focusSet.has(i) ? "seg-focus" : "",
                              seg.low_confidence ? "seg-low" : "",
                            ].join(" ").trim();
                            return (
                              <Fragment key={i}>
                                {n > 0 && " "}
                                <span
                                  id={`seg-${i}`}
                                  data-start={seg.start}
                                  className={cls || undefined}
                                  title={
                                    kp
                                      ? `Supports key point: ${kp.heading}`
                                      : seg.low_confidence
                                        ? "Recognised with low confidence"
                                        : undefined
                                  }
                                  onClick={kp ? () => onFocusKeyPoint(kp) : undefined}
                                >
                                  {renderText(seg)}
                                  {seg.low_confidence && <span className="sr-only"> (low confidence)</span>}
                                </span>
                              </Fragment>
                            );
                          })}
                        </p>
                      </div>
                    );
                  })}
                </div>
              )}
            </section>
          );
        })}
      </div>
    </div>
  );
}
