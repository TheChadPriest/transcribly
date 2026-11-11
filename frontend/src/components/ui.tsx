"use client";

import { Check, Copy, X } from "lucide-react";
import {
  createContext,
  forwardRef,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type ReactNode,
} from "react";
import { copyText } from "@/lib/clipboard";

type Variant = "primary" | "secondary" | "ghost" | "danger";

const VARIANTS: Record<Variant, string> = {
  primary: "bg-accent text-accent-contrast hover:bg-accent-hover border border-transparent",
  secondary: "bg-surface text-text border border-border hover:border-border-strong hover:bg-surface-2",
  ghost: "bg-transparent text-muted hover:text-text hover:bg-surface-2 border border-transparent",
  danger: "bg-surface text-danger border border-border hover:bg-danger-soft",
};

export const Button = forwardRef<
  HTMLButtonElement,
  ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: "sm" | "md" | "lg" }
>(function Button({ variant = "secondary", size = "md", className = "", ...props }, ref) {
  const sizes = { sm: "h-8 px-2.5 text-[13px] gap-1.5", md: "h-9 px-3.5 text-sm gap-2", lg: "h-11 px-5 text-[15px] gap-2" };
  return (
    <button
      ref={ref}
      type="button"
      {...props}
      className={`inline-flex shrink-0 items-center justify-center rounded-md font-medium transition-colors disabled:pointer-events-none disabled:opacity-50 ${sizes[size]} ${VARIANTS[variant]} ${className}`}
    />
  );
});

// --- toasts -------------------------------------------------------------------

interface Toast {
  id: number;
  message: string;
  tone: "success" | "error";
}

const ToastContext = createContext<(message: string, tone?: Toast["tone"]) => void>(() => {});
const FallbackContext = createContext<(text: string) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [fallback, setFallback] = useState<string | null>(null);
  const next = useRef(1);
  const push = useCallback((message: string, tone: Toast["tone"] = "success") => {
    const id = next.current++;
    setToasts((t) => [...t.slice(-2), { id, message, tone }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 2600);
  }, []);
  return (
    <ToastContext.Provider value={push}>
      <FallbackContext.Provider value={setFallback}>
        {children}
        <div
          className="pointer-events-none fixed inset-x-0 bottom-4 z-50 flex flex-col items-center gap-2 px-4"
          role="status"
          aria-live="polite"
        >
          {toasts.map((t) => (
            <div
              key={t.id}
              className={`pointer-events-auto flex items-center gap-2 rounded-md border px-3 py-2 text-sm shadow-sm ${
                t.tone === "error" ? "border-danger/40 bg-danger-soft text-danger" : "border-border bg-surface text-text"
              }`}
            >
              {t.tone === "success" ? <Check size={15} className="text-success" aria-hidden /> : <X size={15} aria-hidden />}
              {t.message}
            </div>
          ))}
        </div>
        {fallback !== null && <CopyFallback text={fallback} onClose={() => setFallback(null)} />}
      </FallbackContext.Provider>
    </ToastContext.Provider>
  );
}

export const useToast = () => useContext(ToastContext);

function CopyFallback({ text, onClose }: { text: string; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const area = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    ref.current?.showModal();
    area.current?.select();
  }, []);
  return (
    <dialog
      ref={ref}
      onClose={onClose}
      className="m-auto w-[min(640px,calc(100vw-32px))] rounded-lg border border-border bg-surface p-0 text-text"
      aria-labelledby="copy-fallback-title"
    >
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <h2 id="copy-fallback-title" className="text-sm font-semibold">
          Copy manually
        </h2>
        <Button variant="ghost" size="sm" onClick={() => ref.current?.close()} aria-label="Close">
          <X size={16} aria-hidden />
        </Button>
      </div>
      <div className="p-4">
        <p className="mb-2 text-sm text-muted">
          Your browser blocked automatic copying. The text below is selected — press Ctrl+C (⌘C on Mac).
        </p>
        <textarea
          ref={area}
          readOnly
          value={text}
          className="h-64 w-full resize-y rounded-md border border-border bg-surface-2 p-3 font-mono text-[13px] text-text"
        />
      </div>
    </dialog>
  );
}

export function useCopy() {
  const toast = useToast();
  const fallback = useContext(FallbackContext);
  return useCallback(
    async (text: string, label: string, html?: string) => {
      const ok = await copyText(text, html);
      if (ok) toast(`${label} copied`);
      else fallback(text);
      return ok;
    },
    [toast, fallback],
  );
}

export function CopyButton({
  getText,
  label,
  getHtml,
  size = "sm",
  variant = "secondary",
  children,
  iconOnly = false,
}: {
  getText: () => string | Promise<string>;
  label: string;
  getHtml?: () => string;
  size?: "sm" | "md";
  variant?: Variant;
  children?: ReactNode;
  iconOnly?: boolean;
}) {
  const copy = useCopy();
  const toast = useToast();
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);
  return (
    <Button
      size={size}
      variant={variant}
      aria-label={iconOnly ? `Copy ${label.toLowerCase()}` : undefined}
      title={iconOnly ? `Copy ${label.toLowerCase()}` : undefined}
      disabled={busy}
      onClick={async () => {
        setBusy(true);
        try {
          const text = await getText();
          if (await copy(text, label, getHtml?.())) {
            setDone(true);
            setTimeout(() => setDone(false), 1500);
          }
        } catch (e) {
          toast(e instanceof Error ? e.message : "Copy failed", "error");
        } finally {
          setBusy(false);
        }
      }}
      className={iconOnly ? "w-8 px-0" : ""}
    >
      {done ? <Check size={14} aria-hidden className="text-success" /> : <Copy size={14} aria-hidden />}
      {!iconOnly && (children ?? "Copy")}
    </Button>
  );
}

export function SectionHeader({ title, children, id }: { title: string; children?: ReactNode; id?: string }) {
  return (
    <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
      <h2 id={id} className="text-[15px] font-semibold tracking-tight text-text">
        {title}
      </h2>
      {children && <div className="flex flex-wrap items-center gap-1.5">{children}</div>}
    </div>
  );
}

export function Notice({ tone = "info", children }: { tone?: "info" | "warn" | "error"; children: ReactNode }) {
  const tones = {
    info: "border-border bg-surface-2 text-muted",
    warn: "border-warn/30 bg-warn-soft text-warn",
    error: "border-danger/30 bg-danger-soft text-danger",
  };
  return <div className={`rounded-md border px-3 py-2 text-[13px] leading-relaxed ${tones[tone]}`}>{children}</div>;
}

export function Spinner({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" className="spin" aria-hidden>
      <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeOpacity="0.2" strokeWidth="3" />
      <path d="M21 12a9 9 0 0 0-9-9" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}
