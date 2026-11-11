import type {
  CitationMetadata,
  CitationSet,
  JobCreated,
  JobOptions,
  JobState,
  PublicConfig,
  Results,
} from "./types";

// Same origin by default (single-container deployment); override for a separately hosted API.
export const API_BASE = (process.env.NEXT_PUBLIC_API_BASE ?? "").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(
    public code: string,
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

const NETWORK_MESSAGE =
  "Couldn't reach the Transcribly server. Check your connection, or try again in a moment.";

async function request<T>(path: string, init: RequestInit = {}, token?: string): Promise<T> {
  const headers = new Headers(init.headers);
  if (token) headers.set("X-Job-Token", token);
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, { ...init, headers });
  } catch {
    throw new ApiError("network", NETWORK_MESSAGE, 0);
  }
  if (!res.ok) {
    let body: { error?: { code?: string; message?: string } } = {};
    try {
      body = await res.json();
    } catch {
      /* non-JSON error */
    }
    throw new ApiError(
      body.error?.code ?? "http_" + res.status,
      body.error?.message ?? "Something went wrong on our side. Please try again.",
      res.status,
    );
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  config: () => request<PublicConfig>("/api/config"),

  checkSource: (url: string) =>
    request<{ kind: string; label: string; url: string }>("/api/sources/check", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    }),

  createJob: (input: { url?: string; file?: File }, options: JobOptions, idempotencyKey: string) => {
    const form = new FormData();
    if (input.file) form.append("file", input.file, input.file.name);
    else if (input.url) form.append("url", input.url);
    if (options.language) form.append("language", options.language);
    if (options.model) form.append("model", options.model);
    form.append("prefer_captions", String(options.prefer_captions));
    return request<JobCreated>("/api/jobs", {
      method: "POST",
      body: form,
      headers: { "Idempotency-Key": idempotencyKey },
    });
  },

  job: (id: string, token: string) => request<JobState>(`/api/jobs/${id}`, {}, token),
  results: (id: string, token: string) => request<Results>(`/api/jobs/${id}/results`, {}, token),
  cancel: (id: string, token: string) =>
    request<JobState>(`/api/jobs/${id}/cancel`, { method: "POST" }, token),
  retry: (id: string, token: string) =>
    request<JobState>(`/api/jobs/${id}/retry`, { method: "POST" }, token),
  regenerateNotes: (id: string, token: string) =>
    request<JobState>(`/api/jobs/${id}/notes`, { method: "POST" }, token),
  remove: (id: string, token: string) =>
    request<void>(`/api/jobs/${id}`, { method: "DELETE" }, token),

  updateMetadata: (id: string, token: string, meta: CitationMetadata) =>
    request<CitationSet>(
      `/api/jobs/${id}/metadata`,
      { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(meta) },
      token,
    ),

  /** Downloads an export as a Blob (the token travels in a header, never in the URL). */
  async exportFile(
    id: string,
    token: string,
    format: "txt" | "md" | "pdf" | "docx",
    params: { style: string; timestamps: boolean },
  ): Promise<{ blob: Blob; filename: string }> {
    const q = new URLSearchParams({ style: params.style, timestamps: String(params.timestamps) });
    let res: Response;
    try {
      res = await fetch(`${API_BASE}/api/jobs/${id}/export/${format}?${q}`, {
        headers: { "X-Job-Token": token },
      });
    } catch {
      throw new ApiError("network", NETWORK_MESSAGE, 0);
    }
    if (!res.ok) {
      let message = "The export could not be generated. You can still copy the content from the page.";
      try {
        message = (await res.json()).error?.message ?? message;
      } catch {
        /* ignore */
      }
      throw new ApiError("export_failed", message, res.status);
    }
    const disposition = res.headers.get("content-disposition") ?? "";
    const filename = /filename="([^"]+)"/.exec(disposition)?.[1] ?? `transcript.${format}`;
    return { blob: await res.blob(), filename };
  },
};
