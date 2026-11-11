"use client";

import { ExternalLink, Quote, Sparkles } from "lucide-react";
import { useState } from "react";
import { formatTimestamp, keyPointsText, timestampUrl, topicOutlineText } from "@/lib/format";
import type { CitationMetadata, Highlight, KeyPoint, Results, StudyMaterials } from "@/lib/types";
import { Button, CopyButton, Notice, SectionHeader, Spinner } from "../ui";

/** Navigate to the transcript. `segments` are highlighted together (a whole referenced passage). */
export type GoTo = (seconds: number | null, segment?: number, segments?: number[]) => void;

export function TimeLink({ seconds, goTo, segment }: { seconds: number | null; goTo: GoTo; segment?: number }) {
  if (seconds == null) return null;
  return (
    <button
      type="button"
      onClick={() => goTo(seconds, segment)}
      className="shrink-0 whitespace-nowrap rounded font-mono text-[12px] text-accent hover:underline"
      title="Show this passage in the transcript"
    >
      {formatTimestamp(seconds)}
    </button>
  );
}

/** Small timestamp links to the transcript passages a statement is based on. */
export function RefLinks({ refs, goTo, label }: { refs: Highlight[] | undefined; goTo: GoTo; label?: string }) {
  if (!refs?.length) return null;
  return (
    <span className="inline-flex flex-wrap items-center gap-1 align-baseline">
      {label && <span className="text-[12px] text-faint">{label}</span>}
      {refs.map((h, i) => (
        <button
          key={i}
          type="button"
          onClick={() => goTo(h.start, h.segment_indices[0], h.segment_indices)}
          className="whitespace-nowrap rounded border border-border bg-surface-2 px-1.5 py-px font-mono text-[11px] text-accent hover:border-accent"
          title={`Show the transcript passage at ${formatTimestamp(h.start)}`}
          aria-label={`Show the transcript passage at ${formatTimestamp(h.start)}`}
        >
          {formatTimestamp(h.start)}
        </button>
      ))}
    </span>
  );
}

/** Collapsible verbatim transcript excerpts for the cited passages. */
function SourceQuotes({ refs, goTo }: { refs: Highlight[] | undefined; goTo: GoTo }) {
  const [open, setOpen] = useState(false);
  if (!refs?.length) return null;
  return (
    <div className="mt-2">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="inline-flex items-center gap-1 text-[12px] text-muted hover:text-text"
      >
        <Quote size={12} aria-hidden /> {open ? "Hide what the video says" : "Show what the video says"}
      </button>
      {open && (
        <div className="mt-2 space-y-2">
          {refs.map((h, i) => (
            <blockquote key={i} className="border-l-2 border-border pl-3 text-[13px] leading-relaxed text-muted">
              <span className="mr-1.5">
                <TimeLink seconds={h.start} goTo={() => goTo(h.start, h.segment_indices[0], h.segment_indices)} />
              </span>
              “{h.excerpt}”
            </blockquote>
          ))}
        </div>
      )}
    </div>
  );
}

function OpenAt({ meta, seconds }: { meta: CitationMetadata; seconds: number | null }) {
  const url = timestampUrl(meta, seconds);
  if (!url) return null;
  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex items-center gap-1 text-[12px] text-faint hover:text-accent"
      aria-label={`Open the video at ${formatTimestamp(seconds)}`}
    >
      <ExternalLink size={12} aria-hidden /> Open video at this time
    </a>
  );
}

function SummaryUnavailable({ results }: { results: Results }) {
  return (
    <Notice tone="warn">
      {results.summary_error ??
        "The transcript is available, but automatic summarization could not be completed. You can still copy the transcript."}
    </Notice>
  );
}

export interface AiControls {
  available: boolean;
  label: string | null;
  busy: boolean;
  onWrite: () => void;
}

/** Explains who wrote the notes, and offers AI notes when they're available but not used yet. */
export function NotesSource({ study, ai }: { study: StudyMaterials; ai?: AiControls }) {
  const isAi = study.generator === "ai";
  return (
    <div className="space-y-2">
      {study.notice && <Notice tone="warn">{study.notice}</Notice>}
      <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border bg-surface-2 px-3 py-2">
        <p className="flex items-start gap-2 text-[13px] leading-relaxed text-muted">
          <Sparkles size={15} className={`mt-0.5 shrink-0 ${isAi ? "text-accent" : "text-faint"}`} aria-hidden />
          {isAi ? (
            <span>
              Written by AI after reading the full transcript ({study.generated_by}). The timestamps next to each point
              link to the passages it&apos;s based on — check anything important there.
            </span>
          ) : (
            <span>
              These notes are assembled from sentences in the transcript (no AI model was used).
              {ai && !ai.available && " AI-written notes are not available on this server right now."}
            </span>
          )}
        </p>
        {ai?.available && !isAi && (
          <Button size="sm" variant="primary" onClick={ai.onWrite} disabled={ai.busy}>
            {ai.busy ? <Spinner size={14} /> : <Sparkles size={14} aria-hidden />} Write AI study notes
          </Button>
        )}
      </div>
    </div>
  );
}

export function OverviewPanel({ results, goTo, ai }: { results: Results; goTo: GoTo; ai?: AiControls }) {
  const s = results.study;
  if (!s) return <SummaryUnavailable results={results} />;
  const paragraphs = s.summary_paragraphs?.length ? s.summary_paragraphs : [{ text: s.executive_summary, refs: [] }];
  const questions = s.questions?.length ? s.questions : s.study_questions.map((q) => ({ text: q, refs: [] }));
  return (
    <div className="space-y-9">
      <NotesSource study={s} ai={ai} />

      <section aria-labelledby="exec-title">
        <SectionHeader title="Executive summary" id="exec-title">
          <CopyButton label="Summary" getText={() => paragraphs.map((p) => p.text).join("\n\n")} />
        </SectionHeader>
        <div className="max-w-[72ch] space-y-4">
          {paragraphs.map((p, i) => (
            <p key={i} className="text-[16px] leading-[1.75] text-text">
              {p.text} <RefLinks refs={p.refs} goTo={goTo} />
            </p>
          ))}
        </div>
      </section>

      {s.conclusions.length > 0 && (
        <section aria-labelledby="concl-title">
          <SectionHeader title="Main conclusions" id="concl-title">
            <CopyButton label="Conclusions" getText={() => s.conclusions.map((c) => `- ${c.text}`).join("\n")} />
          </SectionHeader>
          <p className="mb-3 text-[13px] text-faint">What the speaker argues or concludes — not independently verified.</p>
          <ul className="space-y-4">
            {s.conclusions.map((c, i) => (
              <li key={i} className="max-w-[72ch]">
                <p className="text-[15px] leading-relaxed">
                  {c.text}{" "}
                  {c.refs?.length ? <RefLinks refs={c.refs} goTo={goTo} /> : <TimeLink seconds={c.timestamp} goTo={goTo} />}
                </p>
                <SourceQuotes refs={c.refs} goTo={goTo} />
              </li>
            ))}
          </ul>
        </section>
      )}

      {s.concepts.length > 0 && (
        <section aria-labelledby="concepts-title">
          <SectionHeader title="Key concepts" id="concepts-title">
            <CopyButton label="Concepts" getText={() => s.concepts.map((c) => `- ${c.term}: ${c.explanation}`).join("\n")} />
          </SectionHeader>
          <dl className="divide-y divide-border rounded-lg border border-border bg-surface">
            {s.concepts.map((c) => (
              <div key={c.term} className="grid gap-1 px-4 py-3 sm:grid-cols-[180px_1fr] sm:gap-4">
                <dt className="text-[14px] font-semibold text-text">{c.term}</dt>
                <dd className="text-[14px] leading-relaxed text-muted">
                  <span className="mr-1.5 rounded border border-border px-1 py-px text-[11px] uppercase tracking-wide text-faint">
                    {c.explanation_kind === "definition" ? "Defined in video" : s.generator === "ai" ? "Explained from video" : "In context"}
                  </span>
                  {c.explanation}{" "}
                  {c.refs?.length ? <RefLinks refs={c.refs} goTo={goTo} /> : <TimeLink seconds={c.timestamp} goTo={goTo} />}
                </dd>
              </div>
            ))}
          </dl>
        </section>
      )}

      {questions.length > 0 && (
        <section aria-labelledby="questions-title">
          <SectionHeader title="Study questions" id="questions-title">
            <CopyButton label="Study questions" getText={() => questions.map((q, i) => `${i + 1}. ${q.text}`).join("\n")} />
          </SectionHeader>
          <ol className="list-decimal space-y-3 pl-5 text-[15px] leading-relaxed marker:text-faint">
            {questions.map((q, i) => (
              <li key={i}>
                {q.text}
                {q.refs.length > 0 && (
                  <div className="mt-1">
                    <RefLinks refs={q.refs} goTo={goTo} label="Where to look:" />
                  </div>
                )}
              </li>
            ))}
          </ol>
        </section>
      )}

      {s.limitations.length > 0 && (
        <section aria-labelledby="limits-title">
          <SectionHeader title="Accuracy notes" id="limits-title" />
          <ul className="list-disc space-y-1.5 pl-5 text-[13px] leading-relaxed text-muted">
            {s.limitations.map((l, i) => (
              <li key={i}>{l}</li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function kpRefs(k: KeyPoint): Highlight[] {
  return k.refs?.length ? k.refs : k.support ? [k.support] : [];
}

function allSegments(refs: Highlight[]): number[] {
  return refs.flatMap((h) => h.segment_indices);
}

export function KeyPointCard({ k, meta, goTo }: { k: KeyPoint; meta: CitationMetadata; goTo: GoTo }) {
  const refs = kpRefs(k);
  return (
    <li id={`kp-${k.id}`} className="rounded-lg border border-border bg-surface px-4 py-3">
      <div className="flex items-start justify-between gap-3">
        <h3 className="text-[15px] font-semibold text-text">{k.heading}</h3>
        <CopyButton
          label="Key point"
          iconOnly
          variant="ghost"
          getText={() => `${k.heading}${k.timestamp != null ? ` [${formatTimestamp(k.timestamp)}]` : ""}: ${k.text}`}
        />
      </div>
      <p className="mt-1 max-w-[72ch] text-[15px] leading-relaxed text-muted">{k.text}</p>
      <div className="mt-2 flex flex-wrap items-center gap-3">
        {refs.length ? <RefLinks refs={refs} goTo={goTo} label="Based on:" /> : <TimeLink seconds={k.timestamp} goTo={goTo} />}
        <OpenAt meta={meta} seconds={k.timestamp} />
      </div>
      <SourceQuotes refs={refs} goTo={goTo} />
    </li>
  );
}

export function KeyPointsPanel({ results, goTo, ai }: { results: Results; goTo: GoTo; ai?: AiControls }) {
  const s = results.study;
  if (!s) return <SummaryUnavailable results={results} />;
  if (!s.key_points.length)
    return <Notice>The recording is too short for separate key points. The full text is in the Transcript tab.</Notice>;
  return (
    <section aria-labelledby="kp-title" className="space-y-4">
      <NotesSource study={s} ai={ai} />
      <SectionHeader title={`Key takeaways (${s.key_points.length})`} id="kp-title">
        <CopyButton label="Key points" getText={() => keyPointsText(s)}>
          Copy all
        </CopyButton>
      </SectionHeader>
      <ul className="space-y-3">
        {s.key_points.map((k) => (
          <KeyPointCard key={k.id} k={k} meta={results.metadata} goTo={goTo} />
        ))}
      </ul>
    </section>
  );
}

export function TopicsPanel({ results, goTo }: { results: Results; goTo: GoTo }) {
  const s: StudyMaterials | null = results.study;
  if (!s) return <SummaryUnavailable results={results} />;
  return (
    <section aria-labelledby="topics-title">
      <SectionHeader title="Topic outline" id="topics-title">
        <CopyButton label="Topic outline" getText={() => topicOutlineText(s.topics)}>
          Copy outline
        </CopyButton>
      </SectionHeader>
      <ol className="space-y-3">
        {s.topics.map((t, i) => (
          <li key={t.id} className="rounded-lg border border-border bg-surface px-4 py-3">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h3 className="text-[15px] font-semibold text-text">
                <span className="mr-1.5 text-faint">{i + 1}.</span>
                {t.title}
              </h3>
              <span className="flex items-center gap-1 text-[12px] text-faint">
                <TimeLink seconds={t.start} goTo={goTo} />
                <span aria-hidden>–</span>
                <span className="font-mono">{formatTimestamp(t.end)}</span>
              </span>
            </div>
            {t.summary && <p className="mt-1 max-w-[72ch] text-[15px] leading-relaxed text-muted">{t.summary}</p>}
            {t.subtopics.length > 0 && <p className="mt-2 text-[13px] text-faint">Also covers: {t.subtopics.join(", ")}</p>}
            {s.generator === "ai" ? (
              <div className="mt-2">
                <RefLinks refs={t.passages} goTo={goTo} label="Key passages:" />
              </div>
            ) : (
              t.passages.length > 1 && (
                <ul className="mt-2 space-y-1 border-l-2 border-border pl-3">
                  {t.passages
                    .filter((p) => p.excerpt !== t.summary)
                    .map((p, j) => (
                      <li key={j} className="text-[13px] leading-relaxed text-muted">
                        <TimeLink seconds={p.start} goTo={goTo} segment={p.segment_indices[0]} /> {p.excerpt}
                      </li>
                    ))}
                </ul>
              )
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}

export function Sidebar({ results, goTo }: { results: Results; goTo: GoTo }) {
  const s = results.study;
  if (!s)
    return (
      <div className="text-[13px] text-muted">
        <SummaryUnavailable results={results} />
      </div>
    );
  return (
    <div className="space-y-6 text-[14px]">
      <section aria-labelledby="sb-overview">
        <div className="mb-1.5 flex items-center justify-between">
          <h2 id="sb-overview" className="text-[12px] font-semibold uppercase tracking-wider text-faint">
            Overview{s.generator === "ai" && <span className="ml-1.5 normal-case tracking-normal text-accent">· AI</span>}
          </h2>
          <CopyButton label="Summary" getText={() => s.executive_summary} iconOnly variant="ghost" />
        </div>
        <p className="leading-relaxed text-muted">{s.overview}</p>
      </section>
      {s.key_points.length > 0 && (
        <section aria-labelledby="sb-kp">
          <div className="mb-1.5 flex items-center justify-between">
            <h2 id="sb-kp" className="text-[12px] font-semibold uppercase tracking-wider text-faint">
              Key points
            </h2>
            <CopyButton label="Key points" getText={() => keyPointsText(s)} iconOnly variant="ghost" />
          </div>
          <ul className="space-y-1">
            {s.key_points.map((k) => {
              const refs = kpRefs(k);
              return (
                <li key={k.id}>
                  <button
                    type="button"
                    onClick={() => goTo(k.timestamp, refs[0]?.segment_indices[0], allSegments(refs))}
                    className="group w-full rounded-md px-2 py-1.5 text-left hover:bg-surface-2"
                  >
                    <span className="flex items-baseline justify-between gap-2">
                      <span className="font-medium text-text group-hover:text-accent">{k.heading}</span>
                      {k.timestamp != null && <span className="shrink-0 font-mono text-[11px] text-faint">{formatTimestamp(k.timestamp)}</span>}
                    </span>
                    <span className="mt-0.5 line-clamp-2 text-[13px] leading-snug text-muted">{k.text}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </section>
      )}
      {s.topics.length > 0 && (
        <section aria-labelledby="sb-topics">
          <h2 id="sb-topics" className="mb-1.5 text-[12px] font-semibold uppercase tracking-wider text-faint">
            Topics
          </h2>
          <ol className="space-y-0.5">
            {s.topics.map((t) => (
              <li key={t.id}>
                <button
                  type="button"
                  onClick={() => goTo(t.start)}
                  className="flex w-full items-baseline justify-between gap-2 rounded-md px-2 py-1 text-left hover:bg-surface-2"
                >
                  <span className="text-text">{t.title}</span>
                  <span className="shrink-0 font-mono text-[11px] text-faint">{formatTimestamp(t.start)}</span>
                </button>
              </li>
            ))}
          </ol>
        </section>
      )}
    </div>
  );
}
