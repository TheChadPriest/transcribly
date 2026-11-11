"use client";

import { Clock } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { forgetJob, savedJobs, saveJob, tokenFor, updateSavedTitle, type SavedJob } from "@/lib/storage";
import type { JobCreated, JobState, PublicConfig, Results } from "@/lib/types";
import { Header } from "./Header";
import { JobProgress } from "./JobProgress";
import { Workspace } from "./results/Workspace";
import { SubmitForm } from "./SubmitForm";
import { Notice, Spinner, ToastProvider, useToast } from "./ui";

type View =
  | { kind: "home" }
  | { kind: "loading" }
  | { kind: "job"; id: string; token: string; job: JobState | null }
  | { kind: "results"; id: string; token: string; results: Results; expiresAt: number | null }
  | { kind: "missing"; message: string };

function setJobParam(id: string | null) {
  const url = new URL(window.location.href);
  if (id) url.searchParams.set("job", id);
  else url.searchParams.delete("job");
  window.history.pushState({}, "", url);
}

export default function App() {
  return (
    <ToastProvider>
      <AppInner />
    </ToastProvider>
  );
}

function AppInner() {
  const [view, setView] = useState<View>({ kind: "loading" });
  const [config, setConfig] = useState<PublicConfig | null>(null);
  const [configError, setConfigError] = useState(false);
  const [now, setNow] = useState(() => Date.now() / 1000);
  const [busy, setBusy] = useState(false);
  const [recent, setRecent] = useState<SavedJob[]>([]);
  const toast = useToast();

  const openJob = useCallback((id: string) => {
    const token = tokenFor(id);
    if (!token) {
      setView({
        kind: "missing",
        message: "This result was created in a different browser, or its access key was cleared. Results are only available in the browser that started them.",
      });
      return;
    }
    setView({ kind: "job", id, token, job: null });
  }, []);

  const route = useCallback(() => {
    const id = new URL(window.location.href).searchParams.get("job");
    if (id) openJob(id);
    else setView({ kind: "home" });
    setRecent(savedJobs());
  }, [openJob]);

  useEffect(() => {
    // The page is a static export, so the URL and saved jobs can only be read after mount.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    route();
    window.addEventListener("popstate", route);
    api
      .config()
      .then(setConfig)
      .catch(() => setConfigError(true));
    return () => window.removeEventListener("popstate", route);
  }, [route]);

  // Poll job state while a job is open. Polling slows down for long-running jobs.
  const jobId = view.kind === "job" ? view.id : null;
  useEffect(() => {
    if (view.kind !== "job") return;
    const { id, token } = view;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    let failures = 0;
    const tick = async () => {
      try {
        const job = await api.job(id, token);
        failures = 0;
        if (stopped) return;
        setNow(Date.now() / 1000);
        if (job.title) updateSavedTitle(id, job.title);
        if (job.status === "completed") {
          const results = await api.results(id, token);
          if (!stopped) setView({ kind: "results", id, token, results, expiresAt: job.expires_at });
          return;
        }
        setView((v) => (v.kind === "job" && v.id === id ? { ...v, job } : v));
        if (job.status === "failed" || job.status === "cancelled") return;
        const age = Date.now() / 1000 - job.created_at;
        timer = setTimeout(tick, age > 600 ? 4000 : 1500);
      } catch (e) {
        if (stopped) return;
        if (e instanceof ApiError && (e.status === 404 || e.status === 403)) {
          forgetJob(id);
          setView({ kind: "missing", message: e.message });
          return;
        }
        failures++;
        timer = setTimeout(tick, Math.min(30000, 2000 * 2 ** failures));
      }
    };
    tick();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId, view.kind === "job" ? view.job?.status === "failed" || view.job?.status === "cancelled" : false]);

  useEffect(() => {
    if (view.kind !== "job") return;
    const t = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(t);
  }, [view.kind]);

  const onCreated = (job: JobCreated, title: string | null) => {
    saveJob({ id: job.job_id, token: job.token, title, created: Date.now() });
    setJobParam(job.job_id);
    setView({ kind: "job", id: job.job_id, token: job.token, job: null });
  };

  const goHome = () => {
    setJobParam(null);
    setView({ kind: "home" });
    setRecent(savedJobs());
  };

  const act = async (fn: () => Promise<JobState>) => {
    setBusy(true);
    try {
      const job = await fn();
      setView((v) => (v.kind === "job" ? { ...v, job } : v));
    } catch (e) {
      toast(e instanceof ApiError ? e.message : "Something went wrong", "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <Header config={config} onHome={goHome} />
      <main id="main">
        {view.kind === "loading" && (
          <div className="flex justify-center py-24 text-muted">
            <Spinner size={20} />
          </div>
        )}

        {view.kind === "home" && (
          <div className="mx-auto w-full max-w-[640px] px-4 pb-24 pt-14 sm:pt-20">
            <h1 className="text-center text-[30px] font-semibold leading-tight tracking-tight text-text sm:text-[38px]">
              Turn videos into study-ready notes.
            </h1>
            <p className="mx-auto mt-3 max-w-[52ch] text-center text-[15px] leading-relaxed text-muted sm:text-base">
              Paste a lecture, documentary or interview link — or upload a recording — and get a full transcript, a
              summary, key points, a topic outline and a ready-to-use citation.
            </p>
            <div className="mt-9">
              {configError && (
                <div className="mb-4">
                  <Notice tone="error">
                    The transcription server isn&apos;t responding right now, so new jobs can&apos;t be started. Please try
                    again in a few minutes.
                  </Notice>
                </div>
              )}
              <SubmitForm config={config} onCreated={onCreated} />
            </div>
            {recent.length > 0 && (
              <section className="mt-12" aria-labelledby="recent-title">
                <h2 id="recent-title" className="mb-2 flex items-center gap-1.5 text-[12px] font-semibold uppercase tracking-wider text-faint">
                  <Clock size={13} aria-hidden /> Recent in this browser
                </h2>
                <ul className="divide-y divide-border rounded-lg border border-border bg-surface">
                  {recent.slice(0, 6).map((j) => (
                    <li key={j.id}>
                      <button
                        type="button"
                        onClick={() => {
                          setJobParam(j.id);
                          openJob(j.id);
                        }}
                        className="flex w-full items-center justify-between gap-3 px-4 py-2.5 text-left text-sm hover:bg-surface-2"
                      >
                        <span className="truncate text-text">{j.title ?? "Untitled"}</span>
                        <span className="shrink-0 text-[12px] text-faint">{new Date(j.created).toLocaleDateString()}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </div>
        )}

        {view.kind === "job" && (
          <div className="px-4 pb-24 pt-14">
            {view.job ? (
              <JobProgress
                job={view.job}
                now={now}
                busy={busy}
                onCancel={() => act(() => api.cancel(view.id, view.token))}
                onRetry={() => act(() => api.retry(view.id, view.token))}
                onReset={goHome}
              />
            ) : (
              <div className="flex justify-center py-16 text-muted" role="status" aria-label="Loading job">
                <Spinner size={20} />
              </div>
            )}
          </div>
        )}

        {view.kind === "results" && (
          <Workspace
            key={view.id}
            initial={view.results}
            token={view.token}
            expiresAt={view.expiresAt}
            aiNotes={config?.ai_notes}
            onWriteNotes={async () => {
              try {
                await api.regenerateNotes(view.id, view.token);
                setView({ kind: "job", id: view.id, token: view.token, job: null });
              } catch (e) {
                toast(e instanceof ApiError ? e.message : "Couldn't start writing AI notes", "error");
              }
            }}
            onDeleted={() => {
              forgetJob(view.id);
              goHome();
            }}
          />
        )}

        {view.kind === "missing" && (
          <div className="mx-auto max-w-[560px] px-4 pt-20 text-center">
            <h1 className="text-xl font-semibold text-text">Result not available</h1>
            <p className="mt-2 text-[15px] text-muted">{view.message}</p>
            <button type="button" onClick={goHome} className="mt-6 text-sm font-medium text-accent hover:underline">
              Start a new transcript
            </button>
          </div>
        )}
      </main>
    </>
  );
}
