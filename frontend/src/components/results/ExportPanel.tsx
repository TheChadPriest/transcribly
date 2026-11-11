"use client";

import { Download, FileText, Trash2 } from "lucide-react";
import { useState } from "react";
import { api, ApiError } from "@/lib/api";
import { Button, CopyButton, Notice, SectionHeader, Spinner, useToast } from "../ui";

const FORMATS = [
  { id: "pdf", label: "PDF", hint: "For reading and printing" },
  { id: "docx", label: "Word (DOCX)", hint: "Editable in Word or Google Docs" },
  { id: "md", label: "Markdown", hint: "For Notion, Obsidian and AI tools" },
  { id: "txt", label: "Plain text", hint: "Works everywhere" },
] as const;

const STYLE_NAMES: Record<string, string> = {
  apa: "APA 7",
  mla: "MLA 9",
  chicago_notes: "Chicago notes-bibliography",
  chicago_author_date: "Chicago author-date",
  harvard: "Harvard",
};

export function ExportPanel({
  jobId,
  token,
  style,
  expiresAt,
  onDeleted,
}: {
  jobId: string;
  token: string;
  style: string;
  expiresAt: number | null;
  onDeleted: () => void;
}) {
  const [timestamps, setTimestamps] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const toast = useToast();

  const download = async (format: (typeof FORMATS)[number]["id"]) => {
    setBusy(format);
    setError(null);
    try {
      const { blob, filename } = await api.exportFile(jobId, token, format, { style, timestamps });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10_000);
      toast(`${filename} downloaded`);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The export could not be generated.");
    } finally {
      setBusy(null);
    }
  };

  const fetchText = async (format: "txt" | "md") => {
    const { blob } = await api.exportFile(jobId, token, format, { style, timestamps });
    return blob.text();
  };

  return (
    <div className="space-y-8">
      <section aria-labelledby="export-title">
        <SectionHeader title="Complete study document" id="export-title" />
        <p className="mb-4 max-w-[72ch] text-[14px] leading-relaxed text-muted">
          One document with the source details, executive summary, key takeaways, concepts, conclusions, topic outline,
          study questions, the full transcript and a {STYLE_NAMES[style] ?? style} citation. Change the citation style in
          the Citation tab.
        </p>
        <label className="mb-4 flex items-center gap-2 text-[13px] text-muted">
          <input type="checkbox" checked={timestamps} onChange={(e) => setTimestamps(e.target.checked)} className="size-3.5 accent-[var(--accent)]" />
          Include timestamps
        </label>
        <div className="mb-5 flex flex-wrap gap-2">
          <CopyButton label="Study document" getText={() => fetchText("txt")} size="md" variant="primary">
            Copy everything
          </CopyButton>
          <CopyButton label="Markdown" getText={() => fetchText("md")} size="md">
            Copy as Markdown
          </CopyButton>
        </div>
        <ul className="grid gap-2 sm:grid-cols-2">
          {FORMATS.map((f) => (
            <li key={f.id}>
              <button
                type="button"
                onClick={() => download(f.id)}
                disabled={busy !== null}
                className="flex w-full items-center gap-3 rounded-lg border border-border bg-surface px-4 py-3 text-left transition-colors hover:border-border-strong hover:bg-surface-2 disabled:opacity-60"
              >
                {busy === f.id ? <Spinner size={18} /> : <FileText size={18} className="text-faint" aria-hidden />}
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-medium text-text">{f.label}</span>
                  <span className="block text-[12px] text-faint">{f.hint}</span>
                </span>
                <Download size={16} className="text-faint" aria-hidden />
              </button>
            </li>
          ))}
        </ul>
        {error && (
          <div className="mt-3" role="alert">
            <Notice tone="error">{error}</Notice>
          </div>
        )}
      </section>

      <section aria-labelledby="delete-title" className="border-t border-border pt-6">
        <SectionHeader title="Delete this result" id="delete-title" />
        <p className="mb-3 text-[13px] text-muted">
          {expiresAt
            ? `This result is deleted automatically on ${new Date(expiresAt * 1000).toLocaleString()}.`
            : "This result is kept until you delete it."}{" "}
          You can delete it now; this can&apos;t be undone.
        </p>
        {confirmDelete ? (
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="danger"
              size="sm"
              onClick={async () => {
                try {
                  await api.remove(jobId, token);
                  toast("Result deleted");
                  onDeleted();
                } catch (e) {
                  toast(e instanceof ApiError ? e.message : "Couldn't delete", "error");
                }
              }}
            >
              <Trash2 size={14} aria-hidden /> Yes, delete permanently
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setConfirmDelete(false)}>
              Keep it
            </Button>
          </div>
        ) : (
          <Button variant="danger" size="sm" onClick={() => setConfirmDelete(true)}>
            <Trash2 size={14} aria-hidden /> Delete result
          </Button>
        )}
      </section>
    </div>
  );
}
