import type { Results } from "@/lib/types";

const sentences = [
  "Welcome to this lecture on the water cycle.",
  "Evaporation is the process in which liquid water turns into vapor.",
  "Condensation is when water vapor cools into droplets.",
  "Precipitation refers to water that falls from clouds.",
  "In conclusion, the water cycle is a continuous loop.",
];

export function makeResults(): Results {
  const segments = sentences.map((text, i) => ({ index: i, start: i * 10, end: i * 10 + 8, text, low_confidence: i === 3 }));
  return {
    job_id: "job_abcdefghijklmnopqrstuv",
    metadata: {
      creator: "Science Lab", creator_is_person: false, channel: null, title: "The Water Cycle", published: "2024-02-01",
      platform: "YouTube", publisher: null, url: "https://www.youtube.com/watch?v=abcdefghijk", accessed: "2026-10-26",
      duration_seconds: 50, source_type: "youtube_video", harvard_include_medium: false,
    },
    citations: {
      citations: [
        { style: "apa", label: "APA 7th edition", reference: [{ text: "Science Lab. (2024, February 1). ", italic: false }, { text: "The Water Cycle", italic: true }, { text: " [Video]. YouTube.", italic: false }], reference_text: "Science Lab. (2024, February 1). The Water Cycle [Video]. YouTube.", in_text: "(Science Lab, 2024)", note: null, note_label: null },
        { style: "mla", label: "MLA 9th edition", reference: [{ text: "“The Water Cycle.” ", italic: false }, { text: "YouTube", italic: true }, { text: ", 1 Feb. 2024.", italic: false }], reference_text: "“The Water Cycle.” YouTube, 1 Feb. 2024.", in_text: "(“The Water Cycle”)", note: null, note_label: null },
      ],
      warnings: ["Check your required style guide before submitting."],
    },
    transcript: {
      language: "en", language_probability: 0.99, source: "speech_recognition", engine: "faster-whisper", model: "small", duration: 50,
      segments,
      paragraphs: [
        { index: 0, start: 0, end: 28, segment_indices: [0, 1, 2] },
        { index: 1, start: 30, end: 48, segment_indices: [3, 4] },
      ],
    },
    study: {
      method: "extractive",
      executive_summary: "Evaporation is the process in which liquid water turns into vapor. In conclusion, the water cycle is a continuous loop.",
      overview: "Evaporation is the process in which liquid water turns into vapor.",
      key_points: [
        { id: "k1", heading: "Evaporation", text: sentences[1], timestamp: 10, support: { segment_indices: [1], start: 10, end: 18, excerpt: sentences[1] } },
        { id: "k2", heading: "Water cycle", text: sentences[4], timestamp: 40, support: { segment_indices: [4], start: 40, end: 48, excerpt: sentences[4] } },
      ],
      concepts: [{ term: "Evaporation", explanation: sentences[1], explanation_kind: "definition", timestamp: 10, mentions: 2 }],
      conclusions: [{ text: sentences[4], timestamp: 40 }],
      topics: [
        { id: "t1", title: "Evaporation and Condensation", summary: sentences[1], start: 0, end: 28, subtopics: ["Vapor"], passages: [] },
        { id: "t2", title: "Precipitation", summary: sentences[3], start: 30, end: 48, subtopics: [], passages: [] },
      ],
      study_questions: ["In your own words, explain “Evaporation”.", "How does evaporation connect to precipitation?"],
      limitations: ["This summary is extractive."],
    },
    summary_error: null,
    warnings: [],
  };
}
