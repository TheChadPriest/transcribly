// Per-browser conveniences. Everything is wrapped: storage may be blocked or unavailable.

import type { JobOptions } from "./types";

const JOBS_KEY = "transcribly-jobs";
const OPTIONS_KEY = "transcribly-options";
export const THEME_KEY = "transcribly-theme";

export interface SavedJob {
  id: string;
  token: string;
  title: string | null;
  created: number;
}

function read<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function write(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage unavailable */
  }
}

export function savedJobs(): SavedJob[] {
  return read<SavedJob[]>(JOBS_KEY, []);
}

export function saveJob(job: SavedJob): void {
  const rest = savedJobs().filter((j) => j.id !== job.id);
  write(JOBS_KEY, [job, ...rest].slice(0, 20));
}

export function updateSavedTitle(id: string, title: string | null): void {
  write(
    JOBS_KEY,
    savedJobs().map((j) => (j.id === id ? { ...j, title } : j)),
  );
}

export function forgetJob(id: string): void {
  write(
    JOBS_KEY,
    savedJobs().filter((j) => j.id !== id),
  );
}

export function tokenFor(id: string): string | null {
  return savedJobs().find((j) => j.id === id)?.token ?? null;
}

export const DEFAULT_OPTIONS: JobOptions = { language: "", model: "", prefer_captions: true };

export function loadOptions(): JobOptions {
  return { ...DEFAULT_OPTIONS, ...read<Partial<JobOptions>>(OPTIONS_KEY, {}) };
}

export function saveOptions(o: JobOptions): void {
  write(OPTIONS_KEY, o);
}
