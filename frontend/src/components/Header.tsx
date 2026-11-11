"use client";

import { CircleHelp, Moon, Sun, X } from "lucide-react";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { THEME_KEY } from "@/lib/storage";
import { formatTimestamp } from "@/lib/format";
import type { PublicConfig } from "@/lib/types";
import { Logo } from "./Logo";
import { Button } from "./ui";

type Theme = "light" | "dark";

const themeListeners = new Set<() => void>();
const subscribeTheme = (cb: () => void) => {
  themeListeners.add(cb);
  return () => themeListeners.delete(cb);
};

export function ThemeToggle() {
  const theme = useSyncExternalStore(
    subscribeTheme,
    () => (document.documentElement.dataset.theme as Theme) || "light",
    () => null,
  );
  const toggle = () => {
    const next: Theme = theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem(THEME_KEY, next);
    } catch {
      /* preference just won't persist */
    }
    themeListeners.forEach((cb) => cb());
  };
  const label = theme === "dark" ? "Switch to light theme" : "Switch to dark theme";
  return (
    <Button variant="ghost" size="sm" onClick={toggle} aria-label={label} title={label} className="w-8 px-0">
      {theme === "dark" ? <Sun size={16} aria-hidden /> : <Moon size={16} aria-hidden />}
    </Button>
  );
}

export function Header({ config, onHome }: { config: PublicConfig | null; onHome: () => void }) {
  const [helpOpen, setHelpOpen] = useState(false);
  return (
    <header className="sticky top-0 z-30 border-b border-border bg-bg/90 backdrop-blur supports-[backdrop-filter]:bg-bg/75">
      <div className="mx-auto flex h-14 max-w-[1240px] items-center justify-between px-4 sm:px-6">
        <a
          href="./"
          onClick={(e) => {
            e.preventDefault();
            onHome();
          }}
          className="rounded-md"
          aria-label="Transcribly home"
        >
          <Logo />
        </a>
        <nav className="flex items-center gap-1" aria-label="Site">
          <Button variant="ghost" size="sm" onClick={() => setHelpOpen(true)}>
            <CircleHelp size={16} aria-hidden />
            <span className="hidden sm:inline">Help &amp; privacy</span>
            <span className="sm:hidden">Help</span>
          </Button>
          <ThemeToggle />
        </nav>
      </div>
      {helpOpen && <HelpDialog config={config} onClose={() => setHelpOpen(false)} />}
    </header>
  );
}

function HelpDialog({ config, onClose }: { config: PublicConfig | null; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => ref.current?.showModal(), []);
  return (
    <dialog
      ref={ref}
      onClose={onClose}
      aria-labelledby="help-title"
      className="m-auto max-h-[85dvh] w-[min(640px,calc(100vw-32px))] overflow-y-auto rounded-lg border border-border bg-surface p-0 text-text"
    >
      <div className="sticky top-0 flex items-center justify-between border-b border-border bg-surface px-5 py-3">
        <h2 id="help-title" className="text-[15px] font-semibold">
          Help &amp; privacy
        </h2>
        <Button variant="ghost" size="sm" onClick={() => ref.current?.close()} aria-label="Close" className="w-8 px-0">
          <X size={16} aria-hidden />
        </Button>
      </div>
      <div className="space-y-5 px-5 py-4 text-[14px] leading-relaxed text-muted">
        <section>
          <h3 className="mb-1 font-semibold text-text">How it works</h3>
          <p>
            Paste a link to a public video or upload an audio or video file. Transcribly uses the video&apos;s own
            captions when the platform provides them; otherwise it downloads only the audio and transcribes it with
            Whisper, an open-source speech recognition model running on this server. An AI model then reads the whole
            transcript and writes the summary, key points, concepts and topics in plain language, linking every point
            to the passages it comes from.{" "}
            {config?.ai_notes.available
              ? `On this server the notes are written by ${config.ai_notes.label}.`
              : "When no AI model is available, the notes are assembled from sentences in the transcript instead."}
          </p>
        </section>
        <section>
          <h3 className="mb-1 font-semibold text-text">What to expect</h3>
          <ul className="list-disc space-y-1 pl-5">
            <li>Processing time depends on the length of the recording and the server&apos;s hardware. Long lectures can take a while; you can leave the page open and come back.</li>
            <li>Automatic transcripts can contain errors, especially with names, technical terms, music or overlapping speakers. Passages recognised with low confidence are underlined with dots.</li>
            <li>AI-written notes can oversimplify or misread a passage. Use the timestamp links next to each point to check anything important against what was actually said.</li>
            <li>Citations are generated from available metadata. Review them, and check your institution&apos;s style guide.</li>
          </ul>
        </section>
        {config && (
          <section>
            <h3 className="mb-1 font-semibold text-text">Limits on this server</h3>
            <ul className="list-disc space-y-1 pl-5">
              <li>Supported links: {config.supported_sources.join(", ")}.</li>
              <li>Uploads up to {config.max_upload_mb >= 1024 ? `${(config.max_upload_mb / 1024).toFixed(1)} GB` : `${config.max_upload_mb} MB`}.</li>
              {config.max_duration_seconds > 0 && <li>Recordings up to {formatTimestamp(config.max_duration_seconds)} long.</li>}
            </ul>
          </section>
        )}
        <section>
          <h3 className="mb-1 font-semibold text-text">Privacy</h3>
          <ul className="list-disc space-y-1 pl-5">
            <li>No account is needed. Each job gets a private, random access key that is stored only in this browser.</li>
            <li>
              {config?.ai_notes.provider === "claude"
                ? "Audio is processed on this server. To write the study notes, the transcript text is sent to Anthropic's Claude API. Nothing is used to train models."
                : "Media and transcripts are processed on this Transcribly server, including the AI that writes the notes. Nothing is sent to third-party AI services or used to train models."}
            </li>
            <li>
              {config?.keep_media ? "This server is configured to keep source media." : "Uploaded and downloaded media is deleted as soon as processing finishes."}{" "}
              Transcripts and results are kept for {config ? `${config.retention_hours} hours` : "a limited time"} and then deleted automatically. You can delete a result sooner from its Export tab.
            </li>
            <li>Only process media you are allowed to use. Transcribly does not bypass private videos, logins or other access restrictions.</li>
          </ul>
        </section>
      </div>
    </dialog>
  );
}
