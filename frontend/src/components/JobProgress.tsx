"use client";

import { Check, CircleAlert, CircleSlash } from "lucide-react";
import type { JobState } from "@/lib/types";
import { formatDuration } from "@/lib/format";
import { Button, Notice, Spinner } from "./ui";

const RETRYABLE = new Set(["model_unavailable", "download_failed", "transcription_failed", "rate_limited", "interrupted", "internal"]);

function elapsed(from: number, to: number) {
  const s = Math.max(0, Math.round(to - from));
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
}

export function JobProgress({
  job,
  now,
  onCancel,
  onRetry,
  onReset,
  busy,
}: {
  job: JobState;
  now: number;
  onCancel: () => void;
  onRetry: () => void;
  onReset: () => void;
  busy: boolean;
}) {
  const active = job.status === "queued" || job.status === "running";
  const title = job.title ?? "Your media";
  const duration = formatDuration(job.duration);
  return (
    <section aria-labelledby="progress-title" className="mx-auto w-full max-w-[640px] rounded-lg border border-border bg-surface p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 id="progress-title" className="truncate text-[15px] font-semibold text-text">
            {title}
          </h2>
          <p className="text-[13px] text-faint">
            {duration ? `${duration} · ` : ""}
            {job.status === "queued" && "Waiting for a free worker"}
            {job.status === "running" && (job.stages.length ? `Working for ${elapsed(job.stages[0].started_at, now)}` : "Starting")}
            {job.status === "failed" && "Processing stopped"}
            {job.status === "cancelled" && "Cancelled"}
          </p>
        </div>
        {active && (
          <Button size="sm" onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
        )}
      </div>

      <ol className="mt-4 space-y-2" aria-label="Processing steps">
        {job.stages.map((s, i) => {
          const done = s.finished_at !== null || !active || i < job.stages.length - 1;
          const failedHere = !active && job.status !== "completed" && i === job.stages.length - 1;
          return (
            <li key={`${s.stage}-${i}`} className="flex items-center gap-2.5 text-sm">
              <span className="flex size-5 shrink-0 items-center justify-center" aria-hidden>
                {failedHere ? (
                  job.status === "cancelled" ? <CircleSlash size={16} className="text-faint" /> : <CircleAlert size={16} className="text-danger" />
                ) : done ? (
                  <Check size={16} className="text-success" />
                ) : (
                  <Spinner size={15} />
                )}
              </span>
              <span className={done && !failedHere ? "text-muted" : "font-medium text-text"}>{s.label}</span>
              <span className="sr-only">{failedHere ? "(stopped)" : done ? "(done)" : "(in progress)"}</span>
            </li>
          );
        })}
        {active && job.stages.length === 0 && (
          <li className="flex items-center gap-2.5 text-sm font-medium text-text">
            <span className="flex size-5 items-center justify-center" aria-hidden>
              <Spinner size={15} />
            </span>
            {job.status === "queued" ? "Waiting in queue" : "Starting"}
          </li>
        )}
      </ol>

      {active && (
        <div className="mt-4" aria-live="polite">
          {job.detail && <p className="text-[13px] text-muted">{job.detail}</p>}
          {job.progress !== null && (
            <div
              className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-surface-2"
              role="progressbar"
              aria-label={job.stage_label ?? "Progress"}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.round(job.progress * 100)}
            >
              <div className="h-full rounded-full bg-accent transition-[width] duration-500" style={{ width: `${Math.max(2, job.progress * 100)}%` }} />
            </div>
          )}
          <p className="mt-3 text-[12px] text-faint">
            You can leave this page open in the background. Closing it won&apos;t stop the job — reopen this address in the same browser to come back to it.
          </p>
        </div>
      )}

      {job.status === "failed" && job.error && (
        <div className="mt-4 space-y-3" role="alert">
          <Notice tone="error">{job.error.message}</Notice>
          <div className="flex flex-wrap gap-2">
            {RETRYABLE.has(job.error.code) && (
              <Button variant="primary" size="sm" onClick={onRetry} disabled={busy}>
                Retry
              </Button>
            )}
            <Button size="sm" onClick={onReset}>
              Start over
            </Button>
          </div>
        </div>
      )}
      {job.status === "cancelled" && (
        <div className="mt-4 flex flex-wrap gap-2">
          <Button size="sm" onClick={onRetry} disabled={busy}>
            Restart this job
          </Button>
          <Button size="sm" onClick={onReset}>
            Start over
          </Button>
        </div>
      )}
    </section>
  );
}
