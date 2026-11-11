import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { api, ApiError } from "@/lib/api";
import { makeResults } from "@/test/fixtures";
import type { JobState, PublicConfig } from "@/lib/types";
import App from "./App";
import { ThemeToggle } from "./Header";
import { JobProgress } from "./JobProgress";
import { CitationPanel } from "./results/CitationPanel";
import { Workspace } from "./results/Workspace";
import { SubmitForm } from "./SubmitForm";
import { ToastProvider } from "./ui";

const config: PublicConfig = {
  max_upload_mb: 10, max_duration_seconds: 3600, default_model: "small", models: ["base", "small"],
  supported_sources: ["YouTube", "Vimeo"], retention_hours: 72, keep_media: false, ai_notes: { available: false, label: null, provider: null },
};

function mockClipboard() {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
  return writeText;
}

describe("SubmitForm", () => {
  it("validates the link and submits a URL job", async () => {
    vi.spyOn(api, "checkSource").mockResolvedValue({ kind: "youtube", label: "YouTube", url: "x" });
    const create = vi.spyOn(api, "createJob").mockResolvedValue({ job_id: "j", token: "t", status: "queued" });
    const onCreated = vi.fn();
    render(<SubmitForm config={config} onCreated={onCreated} />);
    const input = screen.getByLabelText("Video link");
    await userEvent.type(input, "https://youtu.be/abcdefghijk");
    expect(await screen.findByText("YouTube")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Generate transcript" }));
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith({ job_id: "j", token: "t", status: "queued" }, null));
    expect(create.mock.calls[0][0]).toEqual({ url: "https://youtu.be/abcdefghijk" });
    expect(create.mock.calls[0][2]).toMatch(/^[a-z0-9]+$/i); // idempotency key
  });

  it("shows unsupported links and server errors", async () => {
    vi.spyOn(api, "checkSource").mockRejectedValue(new ApiError("unsupported_source", "This video source is not supported yet.", 400));
    render(<SubmitForm config={config} onCreated={vi.fn()} />);
    await userEvent.type(screen.getByLabelText("Video link"), "https://example.com/page");
    expect(await screen.findByText("Not supported")).toBeInTheDocument();
    expect(screen.getByText("This video source is not supported yet.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Generate transcript" })).toBeDisabled();
  });

  it("accepts a file, rejects oversized files, and uploads", async () => {
    const create = vi.spyOn(api, "createJob").mockResolvedValue({ job_id: "j", token: "t", status: "queued" });
    const onCreated = vi.fn();
    const { container } = render(<SubmitForm config={config} onCreated={onCreated} />);
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    const big = new File(["x"], "huge.mp4", { type: "video/mp4" });
    Object.defineProperty(big, "size", { value: 50 * 1024 * 1024 });
    fireEvent.change(input, { target: { files: [big] } });
    expect(await screen.findByRole("alert")).toHaveTextContent("larger than this server accepts");

    const file = new File(["data"], "Lecture 3.mp3", { type: "audio/mpeg" });
    fireEvent.change(input, { target: { files: [file] } });
    expect(screen.getByText("Lecture 3.mp3")).toBeInTheDocument();
    expect(screen.getByLabelText("Video link")).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Generate transcript" }));
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(expect.anything(), "Lecture 3"));
    expect(create.mock.calls[0][0]).toEqual({ file });
  });

  it("supports drag and drop", async () => {
    render(<SubmitForm config={config} onCreated={vi.fn()} />);
    const zone = screen.getByText(/Drop an audio or video file here/).closest("div")!;
    const file = new File(["data"], "talk.wav", { type: "audio/wav" });
    fireEvent.drop(zone, { dataTransfer: { files: [file] } });
    expect(screen.getByText("talk.wav")).toBeInTheDocument();
  });
});

describe("ThemeToggle", () => {
  it("switches and persists the theme", async () => {
    document.documentElement.dataset.theme = "light";
    render(<ThemeToggle />);
    await userEvent.click(screen.getByRole("button", { name: "Switch to dark theme" }));
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(localStorage.getItem("transcribly-theme")).toBe("dark");
    expect(screen.getByRole("button", { name: "Switch to light theme" })).toBeInTheDocument();
  });
});

const baseJob: JobState = {
  job_id: "j", status: "running", stage: "transcribing", stage_label: "Transcribing", detail: "Transcribed 00:01:00 of 00:10:00",
  progress: 0.1, stages: [
    { stage: "validating", label: "Validating source", started_at: 1, finished_at: 2 },
    { stage: "transcribing", label: "Transcribing", started_at: 2, finished_at: null },
  ], source_type: "upload", title: "Lecture", duration: 600, error: null, warnings: [], created_at: 1, updated_at: 2, expires_at: null,
};

describe("JobProgress", () => {
  it("shows real stages and measured progress", () => {
    render(<JobProgress job={baseJob} now={10} onCancel={vi.fn()} onRetry={vi.fn()} onReset={vi.fn()} busy={false} />);
    expect(screen.getByText("Validating source")).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "10");
    expect(screen.getByText("Transcribed 00:01:00 of 00:10:00")).toBeInTheDocument();
  });
  it("hides the bar when progress is unknown", () => {
    render(<JobProgress job={{ ...baseJob, progress: null }} now={10} onCancel={vi.fn()} onRetry={vi.fn()} onReset={vi.fn()} busy={false} />);
    expect(screen.queryByRole("progressbar")).toBeNull();
  });
  it("offers retry only for retryable failures", () => {
    const failed = { ...baseJob, status: "failed" as const, error: { code: "download_failed", message: "Could not download." } };
    const { rerender } = render(<JobProgress job={failed} now={10} onCancel={vi.fn()} onRetry={vi.fn()} onReset={vi.fn()} busy={false} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Could not download.");
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    rerender(<JobProgress job={{ ...failed, error: { code: "no_audio", message: "No audio." } }} now={10} onCancel={vi.fn()} onRetry={vi.fn()} onReset={vi.fn()} busy={false} />);
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  });
});

function renderWorkspace() {
  return render(
    <ToastProvider>
      <Workspace initial={makeResults()} token="tok" expiresAt={null} onDeleted={vi.fn()} />
    </ToastProvider>,
  );
}

describe("Workspace", () => {
  it("renders overview, sidebar and tabs with keyboard navigation", async () => {
    renderWorkspace();
    expect(screen.getByRole("heading", { level: 1, name: "The Water Cycle" })).toBeInTheDocument();
    expect(screen.getByText("Executive summary")).toBeInTheDocument();
    const tabs = screen.getAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual(["Overview", "Full transcript", "Key points", "Topics", "Citation", "Export"]);
    tabs[0].focus();
    await userEvent.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "Full transcript" })).toHaveAttribute("aria-selected", "true");
  });

  it("navigates from a key point to the transcript passage", async () => {
    renderWorkspace();
    const sidebar = screen.getByRole("complementary", { name: "Study notes" });
    await userEvent.click(within(sidebar).getByRole("button", { name: /^Evaporation\s*00:00:10/ }));
    expect(screen.getByRole("tab", { name: "Full transcript" })).toHaveAttribute("aria-selected", "true");
    const seg = document.getElementById("seg-1")!;
    expect(seg).toHaveClass("seg-hl");
    expect(document.getElementById("seg-3")).toHaveClass("seg-low");
  });

  it("searches, toggles timestamps and highlights, and copies the full transcript", async () => {
    const writeText = mockClipboard();
    renderWorkspace();
    await userEvent.click(screen.getByRole("tab", { name: "Full transcript" }));
    await userEvent.type(screen.getByLabelText("Search transcript"), "water");
    expect(await screen.findByText("1 of 5")).toBeInTheDocument();
    expect(document.querySelectorAll("mark")).toHaveLength(5);
    await userEvent.keyboard("{Enter}");
    expect(screen.getByText("2 of 5")).toBeInTheDocument();

    expect(screen.getAllByText("00:00:00").length).toBeGreaterThan(0);
    await userEvent.click(screen.getByLabelText("Timestamps"));
    expect(document.querySelector("#para-0 .font-mono")).toBeNull();

    await userEvent.click(screen.getByLabelText("Highlight key points"));
    expect(document.getElementById("seg-1")).not.toHaveClass("seg-hl");

    await userEvent.click(screen.getByRole("button", { name: "Copy with timestamps" }));
    await waitFor(() => expect(writeText).toHaveBeenCalled());
    const copied = writeText.mock.calls[0][0] as string;
    expect(copied).toContain("[00:00:00] Welcome to this lecture");
    expect(copied).toContain("In conclusion, the water cycle is a continuous loop.");
    expect(await screen.findByText("Transcript with timestamps copied")).toBeInTheDocument();
  });

  it("jumps to a typed timestamp and rejects bad input", async () => {
    renderWorkspace();
    await userEvent.click(screen.getByRole("tab", { name: "Full transcript" }));
    const box = screen.getByLabelText(/Jump to time/);
    await userEvent.type(box, "abc{Enter}");
    expect(screen.getByText(/Use a time like/)).toBeInTheDocument();
    await userEvent.clear(box);
    await userEvent.type(box, "0:31{Enter}");
    expect(screen.queryByText(/Use a time like/)).toBeNull();
    await waitFor(() => expect(Element.prototype.scrollIntoView).toHaveBeenCalled());
  });

  it("copies a single key point and the topic outline", async () => {
    const writeText = mockClipboard();
    renderWorkspace();
    await userEvent.click(screen.getByRole("tab", { name: "Key points" }));
    await userEvent.click(screen.getAllByRole("button", { name: "Copy key point" })[0]);
    await waitFor(() => expect(writeText).toHaveBeenLastCalledWith(expect.stringMatching(/^Evaporation \[00:00:10\]: Evaporation is/)));
    await userEvent.click(screen.getByRole("tab", { name: "Topics" }));
    await userEvent.click(screen.getByRole("button", { name: /Copy outline/ }));
    await waitFor(() => expect(writeText).toHaveBeenLastCalledWith(expect.stringContaining("2. Precipitation (00:00:30–00:00:48)")));
  });

  it("downloads exports and reports failures", async () => {
    const exportFile = vi.spyOn(api, "exportFile")
      .mockResolvedValueOnce({ blob: new Blob(["%PDF"]), filename: "x.pdf" })
      .mockRejectedValueOnce(new ApiError("export_failed", "The export could not be generated.", 500));
    URL.createObjectURL = vi.fn(() => "blob:x");
    URL.revokeObjectURL = vi.fn();
    renderWorkspace();
    await userEvent.click(screen.getByRole("tab", { name: "Export" }));
    await userEvent.click(screen.getByRole("button", { name: /PDF/ }));
    await waitFor(() => expect(exportFile).toHaveBeenCalledWith("job_abcdefghijklmnopqrstuv", "tok", "pdf", { style: "apa", timestamps: true }));
    await userEvent.click(screen.getByRole("button", { name: /Word/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The export could not be generated.");
  });

  it("falls back to a selectable text box when the clipboard is blocked", async () => {
    Object.defineProperty(navigator, "clipboard", { value: { writeText: vi.fn().mockRejectedValue(new Error("denied")) }, configurable: true });
    document.execCommand = vi.fn().mockReturnValue(false);
    renderWorkspace();
    await userEvent.click(screen.getAllByRole("button", { name: "Copy" })[0]);
    const dialog = await screen.findByRole("dialog", { name: "Copy manually" });
    expect(within(dialog).getByRole("textbox")).toHaveValue(makeResults().study!.executive_summary);
  });
});

describe("CitationPanel", () => {
  it("switches styles and saves edited metadata", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const r = makeResults();
    const update = vi.spyOn(api, "updateMetadata").mockResolvedValue(r.citations);
    const setStyle = vi.fn();
    const { rerender } = render(
      <ToastProvider>
        <CitationPanel jobId="j" token="t" metadata={r.metadata} citations={r.citations} style="apa" setStyle={setStyle} onSaved={vi.fn()} />
      </ToastProvider>,
    );
    expect(screen.getAllByText("APA 7th edition").length).toBeGreaterThan(0);
    await userEvent.click(screen.getByRole("radio", { name: "MLA 9" }));
    expect(setStyle).toHaveBeenCalledWith("mla");
    rerender(
      <ToastProvider>
        <CitationPanel jobId="j" token="t" metadata={r.metadata} citations={r.citations} style="mla" setStyle={setStyle} onSaved={vi.fn()} />
      </ToastProvider>,
    );
    expect(screen.getByRole("radio", { name: "MLA 9" })).toHaveAttribute("aria-checked", "true");
    const creator = screen.getByLabelText("Creator (person or organization)");
    await userEvent.clear(creator);
    await userEvent.type(creator, "Jane Doe");
    await userEvent.click(screen.getByLabelText("The creator is a person"));
    await act(async () => {
      vi.advanceTimersByTime(600);
    });
    await waitFor(() => expect(update).toHaveBeenCalled());
    expect(update.mock.lastCall![2]).toMatchObject({ creator: "Jane Doe", creator_is_person: true });
    vi.useRealTimers();
  });
});

describe("App", () => {
  it("renders the home page", async () => {
    vi.spyOn(api, "config").mockResolvedValue(config);
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Turn videos into study-ready notes." })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Generate transcript" })).toBeInTheDocument();
  });

  it("explains when a job link has no saved access key", async () => {
    vi.spyOn(api, "config").mockResolvedValue(config);
    window.history.pushState({}, "", "/?job=unknownjobidxxxxxxxxxxx");
    render(<App />);
    expect(await screen.findByText("Result not available")).toBeInTheDocument();
    window.history.pushState({}, "", "/");
  });

  it("shows a clear message when the server is down", async () => {
    vi.spyOn(api, "config").mockRejectedValue(new ApiError("network", "down", 0));
    render(<App />);
    expect(await screen.findByText(/isn.t responding right now/)).toBeInTheDocument();
  });
});

function makeAiResults() {
  const r = makeResults();
  const seg = (i: number) => r.transcript.segments[i];
  const hl = (...idx: number[]) => ({
    segment_indices: idx, start: seg(idx[0]).start, end: seg(idx[idx.length - 1]).end,
    excerpt: idx.map((i) => seg(i).text).join(" "),
  });
  r.study = {
    ...r.study!,
    generator: "ai",
    generated_by: "Qwen 2.5 7B, an open-source model running on this server",
    summary_paragraphs: [
      { text: "The lecture walks through the water cycle and why it matters.", refs: [hl(0)] },
      { text: "Water evaporates, condenses into clouds and falls back as precipitation.", refs: [hl(1, 2), hl(3)] },
    ],
    executive_summary: "The lecture walks through the water cycle and why it matters.\n\nWater evaporates, condenses into clouds and falls back as precipitation.",
    key_points: [{ id: "k1", heading: "Evaporation", text: "The sun turns liquid water into vapor.", timestamp: 10, support: hl(1), refs: [hl(1, 2)] }],
    questions: [{ text: "What drives evaporation?", refs: [hl(1)] }],
  };
  return r;
}

describe("AI study notes", () => {
  it("labels AI notes and links each paragraph to its transcript passages", async () => {
    render(
      <ToastProvider>
        <Workspace initial={makeAiResults()} token="tok" expiresAt={null} onDeleted={vi.fn()} />
      </ToastProvider>,
    );
    expect(screen.getByText(/Written by AI after reading the full transcript/)).toBeInTheDocument();
    expect(screen.getByText("The lecture walks through the water cycle and why it matters.")).toBeInTheDocument();
    expect(screen.getByText("Where to look:")).toBeInTheDocument();
    // the second paragraph cites lines 1-2 as one passage; clicking highlights both lines
    await userEvent.click(screen.getAllByRole("button", { name: "Show the transcript passage at 00:00:10" })[0]);
    expect(screen.getByRole("tab", { name: "Full transcript" })).toHaveAttribute("aria-selected", "true");
    expect(document.getElementById("seg-1")).toHaveClass("seg-focus");
    expect(document.getElementById("seg-2")).toHaveClass("seg-focus");
    expect(document.getElementById("seg-4")).not.toHaveClass("seg-focus");
  });

  it("shows the verbatim transcript behind a key point", async () => {
    render(
      <ToastProvider>
        <Workspace initial={makeAiResults()} token="tok" expiresAt={null} onDeleted={vi.fn()} />
      </ToastProvider>,
    );
    await userEvent.click(screen.getByRole("tab", { name: "Key points" }));
    expect(screen.getAllByText("The sun turns liquid water into vapor.").length).toBeGreaterThan(0);
    await userEvent.click(screen.getByRole("button", { name: "Show what the video says" }));
    expect(screen.getByText(/“Evaporation is the process in which liquid water turns into vapor\. Condensation is when/)).toBeInTheDocument();
  });

  it("offers to write AI notes for extractive results when a model is available", async () => {
    const onWrite = vi.fn().mockResolvedValue(undefined);
    const { rerender } = render(
      <ToastProvider>
        <Workspace initial={makeResults()} token="tok" expiresAt={null} onDeleted={vi.fn()}
          aiNotes={{ available: true, label: "Qwen" }} onWriteNotes={onWrite} />
      </ToastProvider>,
    );
    expect(screen.getByText(/assembled from sentences in the transcript/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Write AI study notes" }));
    expect(onWrite).toHaveBeenCalled();
    rerender(
      <ToastProvider>
        <Workspace initial={makeResults()} token="tok" expiresAt={null} onDeleted={vi.fn()}
          aiNotes={{ available: false, label: null }} onWriteNotes={onWrite} />
      </ToastProvider>,
    );
    expect(screen.queryByRole("button", { name: "Write AI study notes" })).toBeNull();
    expect(screen.getByText(/not available on this server right now/)).toBeInTheDocument();
  });

  it("shows why AI notes fell back", () => {
    const r = makeResults();
    r.study = { ...r.study!, notice: "AI study notes couldn't be written: The local AI model (Ollama) isn't running." };
    render(
      <ToastProvider>
        <Workspace initial={r} token="tok" expiresAt={null} onDeleted={vi.fn()} />
      </ToastProvider>,
    );
    expect(screen.getByText(/isn't running/)).toBeInTheDocument();
  });
});
