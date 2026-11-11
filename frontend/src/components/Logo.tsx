/** Document outline with a folded corner; four waveform bars stand in for lines of text. */
export function LogoMark({ size = 24, className = "" }: { size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden className={className}>
      <path
        d="M6 2.75h8.25L19.25 7.75v12.5a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V3.75a1 1 0 0 1 1-1z"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
      <path d="M14 2.75v5h5.25" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
      <g stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
        <path d="M8.5 13v2.5" />
        <path d="M11 11v6.5" />
        <path d="M13.5 12.25v4" />
        <path d="M16 13.5v1.5" />
      </g>
    </svg>
  );
}

export function Logo() {
  return (
    <span className="inline-flex items-center gap-2 text-text">
      <LogoMark size={22} />
      <span className="text-[15px] font-semibold tracking-tight">Transcribly</span>
    </span>
  );
}
