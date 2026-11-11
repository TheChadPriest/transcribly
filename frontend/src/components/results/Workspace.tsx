"use client";

import { ChevronDown, ExternalLink } from "lucide-react";
import { useCallback, useRef, useState } from "react";
import { formatDuration, sidebarText, sourceLabel } from "@/lib/format";
import type { CitationMetadata, CitationSet, KeyPoint, Results } from "@/lib/types";
import { CopyButton, Notice } from "../ui";
import { CitationPanel } from "./CitationPanel";
import { ExportPanel } from "./ExportPanel";
import { KeyPointsPanel, OverviewPanel, Sidebar, TopicsPanel, type GoTo } from "./Panels";
import { TranscriptView, type FocusRequest } from "./TranscriptView";

const TABS = [
  ["overview", "Overview"],
  ["transcript", "Full transcript"],
  ["keypoints", "Key points"],
  ["topics", "Topics"],
  ["citation", "Citation"],
  ["export", "Export"],
] as const;
type Tab = (typeof TABS)[number][0];

export function Workspace({
  initial,
  token,
  expiresAt,
  onDeleted,
  aiNotes,
  onWriteNotes,
}: {
  initial: Results;
  token: string;
  expiresAt: number | null;
  onDeleted: () => void;
  aiNotes?: { available: boolean; label: string | null };
  onWriteNotes?: () => Promise<void>;
}) {
  const [writing, setWriting] = useState(false);
  const ai = aiNotes && onWriteNotes
    ? {
        available: aiNotes.available,
        label: aiNotes.label,
        busy: writing,
        onWrite: async () => {
          setWriting(true);
          try {
            await onWriteNotes();
          } finally {
            setWriting(false);
          }
        },
      }
    : undefined;
  const [results, setResults] = useState(initial);
  const [tab, setTab] = useState<Tab>("overview");
  const [highlights, setHighlights] = useState(true);
  const [focus, setFocus] = useState<FocusRequest | null>(null);
  const [style, setStyle] = useState("apa");
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const { metadata, transcript } = results;

  const goTo: GoTo = useCallback(
    (seconds, segment, segments) => {
      let target = segment;
      if (target === undefined && seconds != null) {
        target = transcript.segments[0]?.index;
        for (const s of transcript.segments) if (s.start <= seconds + 0.01) target = s.index;
      }
      if (target === undefined) return;
      setTab("transcript");
      setFocus({ segment: target, segments: segments ?? [target], nonce: Date.now() });
    },
    [transcript.segments],
  );

  const focusKeyPoint = (k: KeyPoint) => {
    setTab("keypoints");
    requestAnimationFrame(() => document.getElementById(`kp-${k.id}`)?.scrollIntoView({ block: "center", behavior: "smooth" }));
  };

  const onKey = (e: React.KeyboardEvent, i: number) => {
    let next = i;
    if (e.key === "ArrowRight") next = (i + 1) % TABS.length;
    else if (e.key === "ArrowLeft") next = (i - 1 + TABS.length) % TABS.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = TABS.length - 1;
    else return;
    e.preventDefault();
    setTab(TABS[next][0]);
    tabRefs.current[next]?.focus();
  };

  const meta: string[] = [];
  if (metadata.creator) meta.push(metadata.creator);
  if (metadata.platform) meta.push(metadata.platform);
  const dur = formatDuration(transcript.duration);
  if (dur) meta.push(dur);
  meta.push(sourceLabel(transcript.source));
  if (transcript.language) meta.push(transcript.language.toUpperCase());

  return (
    <div className="mx-auto w-full max-w-[1240px] px-4 pb-24 sm:px-6">
      <div className="border-b border-border pb-5 pt-6">
        <h1 className="text-[22px] font-semibold leading-snug tracking-tight text-text sm:text-[26px]">
          {metadata.title ?? "Untitled recording"}
        </h1>
        <p className="mt-1.5 flex flex-wrap items-center gap-x-2 text-[13px] text-faint">
          {meta.map((m, i) => (
            <span key={i} className="flex items-center gap-2">
              {i > 0 && <span aria-hidden>·</span>}
              {m}
            </span>
          ))}
          {metadata.url && metadata.source_type !== "uploaded_file" && (
            <a href={metadata.url} target="_blank" rel="noopener noreferrer" className="ml-1 inline-flex items-center gap-1 text-accent hover:underline">
              Source <ExternalLink size={12} aria-hidden />
            </a>
          )}
        </p>
        {transcript.source === "auto_captions" && (
          <div className="mt-3 max-w-[72ch]">
            <Notice tone="warn">
              This transcript uses the platform&apos;s automatically generated captions. They are not verified and may
              contain errors or missing punctuation.
            </Notice>
          </div>
        )}
        {results.warnings.length > 0 && (
          <div className="mt-3 max-w-[72ch] space-y-2">
            {results.warnings.map((w, i) => (
              <Notice key={i} tone="warn">
                {w}
              </Notice>
            ))}
          </div>
        )}
      </div>

      <div className="mt-6 grid gap-8 lg:grid-cols-[minmax(0,1fr)_300px]">
        <div className="min-w-0">
          {/* mobile / tablet: study notes collapse above the tabs */}
          <details className="group mb-5 rounded-lg border border-border bg-surface lg:hidden">
            <summary className="flex cursor-pointer list-none items-center justify-between px-4 py-3 text-sm font-medium text-text">
              Study notes: overview, key points, topics
              <ChevronDown size={16} className="text-faint transition-transform group-open:rotate-180" aria-hidden />
            </summary>
            <div className="border-t border-border px-3 py-4">
              <Sidebar results={results} goTo={goTo} />
            </div>
          </details>

          <div role="tablist" aria-label="Result sections" className="no-scrollbar mb-6 flex gap-1 overflow-x-auto overflow-y-hidden border-b border-border">
            {TABS.map(([id, label], i) => (
              <button
                key={id}
                ref={(el) => {
                  tabRefs.current[i] = el;
                }}
                role="tab"
                id={`tab-${id}`}
                aria-selected={tab === id}
                aria-controls={`panel-${id}`}
                tabIndex={tab === id ? 0 : -1}
                onClick={() => setTab(id)}
                onKeyDown={(e) => onKey(e, i)}
                className={`-mb-px shrink-0 border-b-2 px-3 py-2.5 text-[14px] font-medium transition-colors ${
                  tab === id ? "border-accent text-text" : "border-transparent text-muted hover:text-text"
                }`}
              >
                {label}
              </button>
            ))}
          </div>

          <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`} tabIndex={-1}>
            {tab === "overview" && <OverviewPanel results={results} goTo={goTo} ai={ai} />}
            {tab === "transcript" && (
              <TranscriptView
                results={results}
                highlights={highlights}
                setHighlights={setHighlights}
                focus={focus}
                onFocusKeyPoint={focusKeyPoint}
              />
            )}
            {tab === "keypoints" && <KeyPointsPanel results={results} goTo={goTo} ai={ai} />}
            {tab === "topics" && <TopicsPanel results={results} goTo={goTo} />}
            {tab === "citation" && (
              <CitationPanel
                jobId={results.job_id}
                token={token}
                metadata={metadata}
                citations={results.citations}
                style={style}
                setStyle={setStyle}
                onSaved={(m: CitationMetadata, c: CitationSet) => setResults((r) => ({ ...r, metadata: m, citations: c }))}
              />
            )}
            {tab === "export" && (
              <ExportPanel jobId={results.job_id} token={token} style={style} expiresAt={expiresAt} onDeleted={onDeleted} />
            )}
          </div>
        </div>

        <aside className="hidden lg:block" aria-label="Study notes">
          <div className="sticky top-20 max-h-[calc(100dvh-6rem)] overflow-y-auto rounded-lg border border-border bg-surface px-3 py-4">
            <Sidebar results={results} goTo={goTo} />
            {results.study && (
              <div className="mt-5 border-t border-border pt-3">
                <CopyButton label="Study notes" getText={() => sidebarText(results)} variant="ghost">
                  Copy study notes
                </CopyButton>
              </div>
            )}
          </div>
        </aside>
      </div>
    </div>
  );
}
