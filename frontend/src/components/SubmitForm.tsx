"use client";

import { ChevronDown, FileAudio, Link2, Upload, X } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { loadOptions, saveOptions } from "@/lib/storage";
import type { JobCreated, JobOptions, PublicConfig } from "@/lib/types";
import { Button, Notice, Spinner } from "./ui";

const LANGUAGES: [string, string][] = [
  ["", "Detect automatically"],
  ["en", "English"],
  ["es", "Spanish"],
  ["fr", "French"],
  ["de", "German"],
  ["it", "Italian"],
  ["pt", "Portuguese"],
  ["nl", "Dutch"],
  ["pl", "Polish"],
  ["tr", "Turkish"],
  ["ru", "Russian"],
  ["uk", "Ukrainian"],
  ["ar", "Arabic"],
  ["hi", "Hindi"],
  ["zh", "Chinese"],
  ["ja", "Japanese"],
  ["ko", "Korean"],
];

const MODEL_LABELS: Record<string, string> = {
  tiny: "Fastest (lowest accuracy)",
  base: "Fast",
  small: "Balanced",
  medium: "Most accurate (slow)",
  "large-v3": "Highest accuracy (very slow)",
};

type SourceCheck = { state: "idle" | "checking" | "ok" | "bad"; label?: string; message?: string };

function formatSize(bytes: number) {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function newKey() {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID().replace(/-/g, "")
    : Math.random().toString(36).slice(2) + Date.now().toString(36);
}

export function SubmitForm({
  config,
  onCreated,
}: {
  config: PublicConfig | null;
  onCreated: (job: JobCreated, title: string | null) => void;
}) {
  const [url, setUrl] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [options, setOptions] = useState<JobOptions>({ language: "", model: "", prefer_captions: true });
  const [check, setCheck] = useState<SourceCheck>({ state: "idle" });
  const [dragging, setDragging] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const idemKey = useRef(newKey());
  const fileInput = useRef<HTMLInputElement>(null);
  const urlId = useId();
  const hintId = useId();

  // Saved options live in localStorage, which only exists after mount.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => setOptions(loadOptions()), []);

  // A new input means a new job; repeated clicks on the same input reuse the key.
  useEffect(() => {
    idemKey.current = newKey();
  }, [url, file, options]);

  useEffect(() => {
    const value = url.trim();
    if (!value) return;
    const t = setTimeout(async () => {
      try {
        const r = await api.checkSource(value);
        setCheck({ state: "ok", label: r.label });
      } catch (e) {
        setCheck({ state: "bad", message: e instanceof ApiError ? e.message : "Unsupported link" });
      }
    }, 350);
    return () => clearTimeout(t);
  }, [url]);

  const updateOptions = (patch: Partial<JobOptions>) => {
    const next = { ...options, ...patch };
    setOptions(next);
    saveOptions(next);
  };

  const pickFile = (f: File | null | undefined) => {
    if (!f) return;
    setError(null);
    if (config && config.max_upload_mb && f.size > config.max_upload_mb * 1024 * 1024) {
      setError(
        `This file is ${formatSize(f.size)}, which is larger than this server accepts (${config.max_upload_mb} MB). Try uploading just the audio track, or use a local installation.`,
      );
      return;
    }
    setFile(f);
    setUrl("");
  };

  const canSubmit = !submitting && (file !== null || (url.trim() !== "" && check.state !== "bad"));

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit) {
      if (!file && !url.trim()) setError("Paste a video link or choose a file to upload.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const job = await api.createJob(file ? { file } : { url: url.trim() }, options, idemKey.current);
      onCreated(job, file ? file.name.replace(/\.[^.]+$/, "") : null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Please try again.");
    } finally {
      setSubmitting(false);
    }
  };

  const models = config?.models ?? [];
  const defaultModel = config?.default_model;

  return (
    <form onSubmit={submit} className="w-full" noValidate aria-describedby={hintId}>
      <label htmlFor={urlId} className="sr-only">
        Video link
      </label>
      <div
        className={`flex items-center gap-2 rounded-lg border bg-surface px-3 transition-colors focus-within:border-accent ${
          check.state === "bad" ? "border-danger/60" : "border-border-strong"
        }`}
      >
        <Link2 size={18} className="shrink-0 text-faint" aria-hidden />
        <input
          id={urlId}
          type="url"
          inputMode="url"
          autoComplete="off"
          spellCheck={false}
          placeholder="Paste a YouTube or other video link"
          value={url}
          disabled={file !== null}
          onChange={(e) => {
            setUrl(e.target.value);
            setCheck({ state: e.target.value.trim() ? "checking" : "idle" });
            setError(null);
          }}
          className="h-12 min-w-0 flex-1 bg-transparent text-[15px] text-text placeholder:text-faint focus:outline-none disabled:opacity-60"
          aria-invalid={check.state === "bad"}
          aria-describedby={`${urlId}-status`}
        />
        <span id={`${urlId}-status`} aria-live="polite" className="shrink-0 text-[12px]">
          {check.state === "checking" && <span className="text-faint">Checking…</span>}
          {check.state === "ok" && (
            <span className="rounded border border-border bg-surface-2 px-1.5 py-0.5 font-medium text-muted">
              {check.label}
            </span>
          )}
          {check.state === "bad" && <span className="font-medium text-danger">Not supported</span>}
        </span>
      </div>
      {check.state === "bad" && check.message && <p className="mt-1.5 text-[13px] text-danger">{check.message}</p>}

      <div className="my-3 flex items-center gap-3 text-[12px] uppercase tracking-wider text-faint" aria-hidden>
        <span className="h-px flex-1 bg-border" />
        or
        <span className="h-px flex-1 bg-border" />
      </div>

      {file ? (
        <div className="flex items-center gap-3 rounded-lg border border-border bg-surface px-3 py-3">
          <FileAudio size={18} className="shrink-0 text-muted" aria-hidden />
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-medium text-text">{file.name}</div>
            <div className="text-[12px] text-faint">{formatSize(file.size)} · Local file</div>
          </div>
          <Button variant="ghost" size="sm" onClick={() => setFile(null)} aria-label="Remove selected file" className="w-8 px-0">
            <X size={16} aria-hidden />
          </Button>
        </div>
      ) : (
        <div
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            pickFile(e.dataTransfer.files?.[0]);
          }}
          className={`flex flex-col items-center justify-center gap-1 rounded-lg border border-dashed px-4 py-5 text-center transition-colors ${
            dragging ? "border-accent bg-accent-soft" : "border-border-strong bg-surface/60"
          }`}
        >
          <Upload size={18} className="text-faint" aria-hidden />
          <p className="text-sm text-muted">
            Drop an audio or video file here, or{" "}
            <button
              type="button"
              onClick={() => fileInput.current?.click()}
              className="font-medium text-accent underline-offset-2 hover:underline"
            >
              choose a file
            </button>
          </p>
          <p className="text-[12px] text-faint">MP4, MOV, MKV, WebM, MP3, WAV, M4A, FLAC, OGG and more</p>
          <input
            ref={fileInput}
            type="file"
            className="sr-only"
            tabIndex={-1}
            aria-label="Choose an audio or video file"
            accept="audio/*,video/*,.mkv,.flac,.ogg,.opus,.m4a,.aac,.wma"
            onChange={(e) => {
              pickFile(e.target.files?.[0]);
              e.target.value = "";
            }}
          />
        </div>
      )}

      <details className="group mt-3 rounded-lg border border-border bg-surface">
        <summary className="flex cursor-pointer list-none items-center justify-between rounded-lg px-3 py-2.5 text-sm text-muted hover:text-text">
          Options
          <ChevronDown size={16} className="transition-transform group-open:rotate-180" aria-hidden />
        </summary>
        <div className="grid gap-3 border-t border-border px-3 py-3 sm:grid-cols-2">
          <label className="grid gap-1 text-[13px] text-muted">
            Spoken language
            <select
              value={options.language}
              onChange={(e) => updateOptions({ language: e.target.value })}
              className="h-9 rounded-md border border-border bg-surface px-2 text-sm text-text"
            >
              {LANGUAGES.map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </select>
          </label>
          {models.length > 1 && (
            <label className="grid gap-1 text-[13px] text-muted">
              Speech recognition accuracy
              <select
                value={options.model}
                onChange={(e) => updateOptions({ model: e.target.value })}
                className="h-9 rounded-md border border-border bg-surface px-2 text-sm text-text"
              >
                <option value="">Server default{defaultModel ? ` (${MODEL_LABELS[defaultModel] ?? defaultModel})` : ""}</option>
                {models.map((m) => (
                  <option key={m} value={m}>
                    {MODEL_LABELS[m] ?? m} — {m}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label className="flex items-start gap-2 text-[13px] text-muted sm:col-span-2">
            <input
              type="checkbox"
              checked={options.prefer_captions}
              onChange={(e) => updateOptions({ prefer_captions: e.target.checked })}
              className="mt-0.5 size-4 accent-[var(--accent)]"
            />
            <span>
              Use the video&apos;s own captions when available
              <span className="block text-faint">
                Much faster. Turn off to transcribe the audio with speech recognition instead.
              </span>
            </span>
          </label>
        </div>
      </details>

      {error && (
        <div className="mt-3" role="alert">
          <Notice tone="error">{error}</Notice>
        </div>
      )}

      <Button type="submit" variant="primary" size="lg" className="mt-4 w-full" disabled={!canSubmit} aria-disabled={!canSubmit}>
        {submitting ? (
          <>
            <Spinner /> {file ? "Uploading…" : "Starting…"}
          </>
        ) : (
          "Generate transcript"
        )}
      </Button>
      <p id={hintId} className="mt-3 text-center text-[12px] leading-relaxed text-faint">
        {config ? `Works with ${config.supported_sources.join(", ")}, and uploads up to ${config.max_upload_mb >= 1024 ? `${(config.max_upload_mb / 1024).toFixed(0)} GB` : `${config.max_upload_mb} MB`}. ` : ""}
        Long videos can take a while to process. Only use media you&apos;re allowed to.
      </p>
    </form>
  );
}
