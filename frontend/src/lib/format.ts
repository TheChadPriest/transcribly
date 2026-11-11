import type { Citation, CitationMetadata, Paragraph, Results, Segment, StudyMaterials, Topic } from "./types";

export function formatTimestamp(seconds: number | null | undefined): string {
  if (seconds == null || Number.isNaN(seconds)) return "--:--:--";
  const total = Math.max(0, Math.floor(seconds + 1e-6));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return [h, m, s].map((n) => String(n).padStart(2, "0")).join(":");
}

/** Accepts "1:02:03", "62:03", "125" -> seconds, or null when invalid. */
export function parseTimestamp(input: string): number | null {
  const v = input.trim();
  if (!/^\d+(:\d{1,2}){0,2}$/.test(v)) return null;
  return v.split(":").reduce((acc, p) => acc * 60 + Number(p), 0);
}

export function paragraphText(p: Paragraph, byIndex: Map<number, Segment>): string {
  return p.segment_indices
    .map((i) => byIndex.get(i)?.text ?? "")
    .filter(Boolean)
    .join(" ");
}

export function segmentMap(segments: Segment[]): Map<number, Segment> {
  return new Map(segments.map((s) => [s.index, s]));
}

/** Full transcript as plain text. Never truncated. */
export function transcriptText(
  segments: Segment[],
  paragraphs: Paragraph[],
  withTimestamps: boolean,
): string {
  const byIndex = segmentMap(segments);
  const paras = paragraphs.length
    ? paragraphs
    : segments.map((s, i) => ({ index: i, start: s.start, end: s.end, segment_indices: [s.index] }));
  return paras
    .map((p) => {
      const text = paragraphText(p, byIndex);
      return withTimestamps ? `[${formatTimestamp(p.start)}] ${text}` : text;
    })
    .join("\n\n");
}

export function keyPointsText(study: StudyMaterials, withTimestamps = true): string {
  return study.key_points
    .map((k) => {
      const ts = withTimestamps && k.timestamp != null ? ` [${formatTimestamp(k.timestamp)}]` : "";
      return `- ${k.heading}${ts}: ${k.text}`;
    })
    .join("\n");
}

export function topicOutlineText(topics: Topic[]): string {
  return topics
    .map((t, i) => {
      const lines = [`${i + 1}. ${t.title} (${formatTimestamp(t.start)}–${formatTimestamp(t.end)})`, `   ${t.summary}`];
      if (t.subtopics.length) lines.push(`   Also covers: ${t.subtopics.join(", ")}`);
      return lines.join("\n");
    })
    .join("\n\n");
}

export function sidebarText(r: Results): string {
  const s = r.study;
  if (!s) return "";
  const parts = [
    r.metadata.title ?? "Untitled recording",
    "",
    "OVERVIEW",
    s.overview,
    "",
    "KEY POINTS",
    keyPointsText(s),
    "",
    "TOPICS",
    s.topics.map((t) => `- [${formatTimestamp(t.start)}] ${t.title}`).join("\n"),
  ];
  return parts.join("\n");
}

export function citationHtml(c: Citation): string {
  const esc = (t: string) => t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return c.reference.map((r) => (r.italic ? `<i>${esc(r.text)}</i>` : esc(r.text))).join("");
}

export function sourceLabel(source: string): string {
  switch (source) {
    case "human_captions":
      return "Creator captions";
    case "auto_captions":
      return "Auto-generated captions";
    default:
      return "Speech recognition";
  }
}

/** A link that opens the source at a time, only for platforms known to support it. */
export function timestampUrl(meta: CitationMetadata, seconds: number | null): string | null {
  if (!meta.url || seconds == null || meta.source_type === "uploaded_file") return null;
  try {
    const u = new URL(meta.url);
    const t = Math.floor(seconds);
    if (/(^|\.)youtube\.com$/.test(u.hostname) || u.hostname === "youtu.be") {
      u.searchParams.set("t", `${t}s`);
      return u.toString();
    }
    if (/(^|\.)vimeo\.com$/.test(u.hostname)) {
      u.hash = `t=${t}s`;
      return u.toString();
    }
  } catch {
    return null;
  }
  return null;
}

export function formatDuration(seconds: number | null | undefined): string | null {
  if (!seconds) return null;
  const h = Math.floor(seconds / 3600);
  const m = Math.round((seconds % 3600) / 60);
  if (h) return `${h} h ${m} min`;
  if (m) return `${m} min`;
  return `${Math.round(seconds)} s`;
}
