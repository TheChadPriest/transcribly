import { describe, expect, it } from "vitest";
import { makeResults } from "@/test/fixtures";
import {
  formatDuration,
  formatTimestamp,
  keyPointsText,
  parseTimestamp,
  timestampUrl,
  topicOutlineText,
  transcriptText,
} from "./format";

describe("timestamps", () => {
  it("formats HH:MM:SS", () => {
    expect(formatTimestamp(0)).toBe("00:00:00");
    expect(formatTimestamp(61.9)).toBe("00:01:01");
    expect(formatTimestamp(3725)).toBe("01:02:05");
    expect(formatTimestamp(null)).toBe("--:--:--");
    expect(formatTimestamp(-4)).toBe("00:00:00");
  });
  it("parses user input", () => {
    expect(parseTimestamp("1:05")).toBe(65);
    expect(parseTimestamp("01:02:30")).toBe(3750);
    expect(parseTimestamp("90")).toBe(90);
    expect(parseTimestamp("1:5:a")).toBeNull();
    expect(parseTimestamp("")).toBeNull();
  });
  it("formats durations", () => {
    expect(formatDuration(5400)).toBe("1 h 30 min");
    expect(formatDuration(130)).toBe("2 min");
    expect(formatDuration(null)).toBeNull();
  });
});

describe("copy text builders", () => {
  const r = makeResults();
  it("builds the full transcript without truncation", () => {
    const plain = transcriptText(r.transcript.segments, r.transcript.paragraphs, false);
    for (const s of r.transcript.segments) expect(plain).toContain(s.text);
    expect(plain).not.toContain("[00:");
    expect(plain.split("\n\n")).toHaveLength(2);
    const timed = transcriptText(r.transcript.segments, r.transcript.paragraphs, true);
    expect(timed.startsWith("[00:00:00] Welcome")).toBe(true);
    expect(timed).toContain("[00:00:30] Precipitation");
  });
  it("handles very long transcripts", () => {
    const segs = Array.from({ length: 20000 }, (_, i) => ({ index: i, start: i, end: i + 1, text: `s${i}.`, low_confidence: false }));
    const text = transcriptText(segs, [], false);
    expect(text.endsWith("s19999.")).toBe(true);
  });
  it("builds key points and topic outline", () => {
    expect(keyPointsText(r.study!)).toBe(
      "- Evaporation [00:00:10]: Evaporation is the process in which liquid water turns into vapor.\n" +
        "- Water cycle [00:00:40]: In conclusion, the water cycle is a continuous loop.",
    );
    const outline = topicOutlineText(r.study!.topics);
    expect(outline).toContain("1. Evaporation and Condensation (00:00:00–00:00:28)");
    expect(outline).toContain("Also covers: Vapor");
  });
});

describe("timestampUrl", () => {
  const meta = makeResults().metadata;
  it("adds t= for YouTube", () => {
    expect(timestampUrl(meta, 75.4)).toBe("https://www.youtube.com/watch?v=abcdefghijk&t=75s");
  });
  it("uses a fragment for Vimeo", () => {
    expect(timestampUrl({ ...meta, url: "https://vimeo.com/123" }, 10)).toBe("https://vimeo.com/123#t=10s");
  });
  it("returns null for unsupported or uploaded sources", () => {
    expect(timestampUrl({ ...meta, url: "https://example.com/a.mp4" }, 10)).toBeNull();
    expect(timestampUrl({ ...meta, source_type: "uploaded_file" }, 10)).toBeNull();
    expect(timestampUrl(meta, null)).toBeNull();
  });
});
