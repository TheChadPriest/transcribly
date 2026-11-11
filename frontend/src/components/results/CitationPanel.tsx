"use client";

import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { citationHtml } from "@/lib/format";
import type { Citation, CitationMetadata, CitationSet, SourceType } from "@/lib/types";
import { CopyButton, Notice, SectionHeader, Spinner } from "../ui";

export const STYLE_ORDER = ["apa", "mla", "chicago_notes", "chicago_author_date", "harvard"] as const;
const STYLE_SHORT: Record<string, string> = {
  apa: "APA 7",
  mla: "MLA 9",
  chicago_notes: "Chicago N-B",
  chicago_author_date: "Chicago A-D",
  harvard: "Harvard",
};

const SOURCE_TYPES: [SourceType, string][] = [
  ["youtube_video", "YouTube video"],
  ["online_video", "Online video"],
  ["lecture", "Recorded lecture"],
  ["documentary", "Online documentary"],
  ["interview", "Online interview"],
  ["uploaded_file", "Uploaded file (no public source)"],
];

function Reference({ c }: { c: Citation }) {
  return (
    <p className="pl-8 -indent-8 text-[15px] leading-relaxed text-text">
      {c.reference.map((r, i) => (r.italic ? <i key={i}>{r.text}</i> : <span key={i}>{r.text}</span>))}
    </p>
  );
}

function Field({
  label,
  value,
  onChange,
  placeholder,
  hint,
  type = "text",
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  hint?: string;
  type?: string;
}) {
  return (
    <label className="grid gap-1 text-[13px] text-muted">
      {label}
      <input
        type={type}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        className="h-9 min-w-0 rounded-md border border-border bg-surface px-2.5 text-sm text-text placeholder:text-faint focus:border-accent focus:outline-none"
      />
      {hint && <span className="text-[12px] text-faint">{hint}</span>}
    </label>
  );
}

export function CitationPanel({
  jobId,
  token,
  metadata,
  citations,
  style,
  setStyle,
  onSaved,
}: {
  jobId: string;
  token: string;
  metadata: CitationMetadata;
  citations: CitationSet;
  style: string;
  setStyle: (s: string) => void;
  onSaved: (meta: CitationMetadata, set: CitationSet) => void;
}) {
  const [meta, setMeta] = useState(metadata);
  const [set, setSet] = useState(citations);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const first = useRef(true);

  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    const t = setTimeout(async () => {
      setSaving(true);
      try {
        const next = await api.updateMetadata(jobId, token, meta);
        setSet(next);
        setError(null);
        onSaved(meta, next);
      } catch (e) {
        setError(e instanceof ApiError ? e.message : "Couldn't update the citation.");
      } finally {
        setSaving(false);
      }
    }, 450);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [meta]);

  const set_ = (patch: Partial<CitationMetadata>) => setMeta((m) => ({ ...m, ...patch }));
  const ordered = STYLE_ORDER.map((s) => set.citations.find((c) => c.style === s)).filter(Boolean) as Citation[];
  const selected = ordered.find((c) => c.style === style) ?? ordered[0];

  return (
    <div className="space-y-8">
      <section aria-labelledby="cite-title">
        <SectionHeader title="Citation" id="cite-title">
          {saving && (
            <span className="flex items-center gap-1.5 text-[12px] text-faint" aria-live="polite">
              <Spinner size={12} /> Updating
            </span>
          )}
        </SectionHeader>
        <div role="radiogroup" aria-label="Citation style" className="mb-4 flex flex-wrap gap-1 rounded-lg border border-border bg-surface-2 p-1">
          {ordered.map((c) => (
            <button
              key={c.style}
              type="button"
              role="radio"
              aria-checked={c.style === selected?.style}
              onClick={() => setStyle(c.style)}
              className={`rounded-md px-3 py-1.5 text-[13px] font-medium transition-colors ${
                c.style === selected?.style ? "bg-surface text-text shadow-sm" : "text-muted hover:text-text"
              }`}
            >
              {STYLE_SHORT[c.style] ?? c.label}
            </button>
          ))}
        </div>
        {selected && (
          <div className="rounded-lg border border-border bg-surface p-4">
            <div className="mb-2 flex items-center justify-between gap-2">
              <span className="text-[12px] font-semibold uppercase tracking-wider text-faint">{selected.label}</span>
              <CopyButton label="Citation" getText={() => selected.reference_text} getHtml={() => citationHtml(selected)}>
                Copy citation
              </CopyButton>
            </div>
            <Reference c={selected} />
            {selected.note && (
              <div className="mt-3 border-t border-border pt-3 text-[14px] text-muted">
                <span className="font-medium text-text">{selected.note_label}:</span> {selected.note}
                <span className="ml-2 inline-block align-middle">
                  <CopyButton label="Footnote" getText={() => selected.note ?? ""} iconOnly variant="ghost" />
                </span>
              </div>
            )}
            {selected.in_text && (
              <div className="mt-2 flex items-center gap-2 text-[14px] text-muted">
                <span>
                  <span className="font-medium text-text">In-text:</span> {selected.in_text}
                </span>
                <CopyButton label="In-text citation" getText={() => selected.in_text ?? ""} iconOnly variant="ghost" />
              </div>
            )}
            {selected.style === "harvard" && (
              <label className="mt-3 flex items-center gap-2 text-[13px] text-muted">
                <input
                  type="checkbox"
                  checked={meta.harvard_include_medium}
                  onChange={(e) => set_({ harvard_include_medium: e.target.checked })}
                  className="size-3.5 accent-[var(--accent)]"
                />
                Include “[Video]” and platform (some institutions require this)
              </label>
            )}
          </div>
        )}
        <div className="mt-3 space-y-2">
          {set.warnings.map((w, i) => (
            <Notice key={i} tone={i < set.warnings.length - 1 ? "warn" : "info"}>
              {w}
            </Notice>
          ))}
          {selected?.style === "harvard" && (
            <Notice>Harvard has no single official version; this follows Cite Them Right. Your institution may differ.</Notice>
          )}
        </div>
        {error && (
          <div className="mt-2" role="alert">
            <Notice tone="error">{error}</Notice>
          </div>
        )}
      </section>

      <section aria-labelledby="meta-title">
        <SectionHeader title="Source details" id="meta-title" />
        <p className="mb-3 text-[13px] text-faint">
          Edit anything that&apos;s missing or wrong — citations update automatically. Fields left empty are never guessed.
        </p>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="sm:col-span-2">
            <Field label="Title" value={meta.title ?? ""} onChange={(v) => set_({ title: v || null })} />
          </div>
          <div className="grid gap-1.5">
            <Field
              label="Creator (person or organization)"
              value={meta.creator ?? ""}
              onChange={(v) => set_({ creator: v || null })}
              placeholder="e.g. Jane Smith or MIT OpenCourseWare"
            />
            <label className="flex items-center gap-2 text-[13px] text-muted">
              <input
                type="checkbox"
                checked={meta.creator_is_person}
                onChange={(e) => set_({ creator_is_person: e.target.checked })}
                className="size-3.5 accent-[var(--accent)]"
              />
              The creator is a person
            </label>
          </div>
          <Field
            label="Channel / uploader"
            value={meta.channel ?? ""}
            onChange={(v) => set_({ channel: v || null })}
            hint="Only if different from the creator"
          />
          <Field
            label="Published"
            value={meta.published ?? ""}
            onChange={(v) => set_({ published: v || null })}
            placeholder="YYYY-MM-DD, YYYY-MM or YYYY"
          />
          <Field label="Platform" value={meta.platform ?? ""} onChange={(v) => set_({ platform: v || null })} placeholder="e.g. YouTube" />
          <div className="sm:col-span-2">
            <Field label="URL" type="url" value={meta.url ?? ""} onChange={(v) => set_({ url: v || null })} />
          </div>
          <Field label="Accessed" value={meta.accessed ?? ""} onChange={(v) => set_({ accessed: v || null })} placeholder="YYYY-MM-DD" />
          <label className="grid gap-1 text-[13px] text-muted">
            Source type
            <select
              value={meta.source_type}
              onChange={(e) => set_({ source_type: e.target.value as SourceType })}
              className="h-9 rounded-md border border-border bg-surface px-2 text-sm text-text"
            >
              {SOURCE_TYPES.map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </select>
          </label>
        </div>
      </section>

      <section aria-labelledby="all-styles-title">
        <SectionHeader title="All styles" id="all-styles-title" />
        <ul className="divide-y divide-border rounded-lg border border-border bg-surface">
          {ordered.map((c) => (
            <li key={c.style} className="px-4 py-3">
              <div className="mb-1 flex items-center justify-between gap-2">
                <span className="text-[12px] font-semibold uppercase tracking-wider text-faint">{c.label}</span>
                <CopyButton label={`${STYLE_SHORT[c.style]} citation`} getText={() => c.reference_text} getHtml={() => citationHtml(c)} iconOnly variant="ghost" />
              </div>
              <Reference c={c} />
            </li>
          ))}
        </ul>
        <p className="mt-3 text-[12px] text-faint">
          Generated references are a starting point, not a verified citation. Check unusual source types against your
          institution&apos;s style guide.
        </p>
      </section>
    </div>
  );
}
