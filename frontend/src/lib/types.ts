// Mirrors backend/app/models.py

export type JobStatus = "queued" | "running" | "completed" | "failed" | "cancelled";

export interface StageEvent {
  stage: string;
  label: string;
  started_at: number;
  finished_at: number | null;
}

export interface ApiErrorBody {
  code: string;
  message: string;
}

export interface JobState {
  job_id: string;
  status: JobStatus;
  stage: string | null;
  stage_label: string | null;
  detail: string | null;
  progress: number | null;
  stages: StageEvent[];
  source_type: string;
  title: string | null;
  duration: number | null;
  error: ApiErrorBody | null;
  warnings: string[];
  created_at: number;
  updated_at: number;
  expires_at: number | null;
}

export interface JobCreated {
  job_id: string;
  token: string;
  status: JobStatus;
}

export interface Segment {
  index: number;
  start: number;
  end: number;
  text: string;
  speaker?: string | null;
  confidence?: number | null;
  low_confidence: boolean;
}

export interface Paragraph {
  index: number;
  start: number;
  end: number;
  segment_indices: number[];
}

export type TranscriptSource = "human_captions" | "auto_captions" | "speech_recognition";

export interface Transcript {
  language: string | null;
  language_probability: number | null;
  source: TranscriptSource;
  engine: string | null;
  model: string | null;
  duration: number | null;
  segments: Segment[];
  paragraphs: Paragraph[];
}

export interface Highlight {
  segment_indices: number[];
  start: number;
  end: number;
  excerpt: string;
}

export interface CitedText {
  text: string;
  refs: Highlight[];
}

export interface KeyPoint {
  id: string;
  heading: string;
  text: string;
  timestamp: number | null;
  support: Highlight | null;
  refs?: Highlight[];
}

export interface Concept {
  term: string;
  explanation: string;
  explanation_kind: "definition" | "context";
  timestamp: number | null;
  mentions: number;
  refs?: Highlight[];
}

export interface Topic {
  id: string;
  title: string;
  summary: string;
  start: number;
  end: number;
  subtopics: string[];
  passages: Highlight[];
}

export interface Conclusion {
  text: string;
  timestamp: number | null;
  refs?: Highlight[];
}

export interface StudyMaterials {
  method: string;
  executive_summary: string;
  overview: string;
  key_points: KeyPoint[];
  concepts: Concept[];
  conclusions: Conclusion[];
  topics: Topic[];
  study_questions: string[];
  limitations: string[];
  generator?: "ai" | "extractive";
  generated_by?: string | null;
  summary_paragraphs?: CitedText[];
  questions?: CitedText[];
  notice?: string | null;
}

export type SourceType =
  | "youtube_video"
  | "online_video"
  | "lecture"
  | "documentary"
  | "interview"
  | "uploaded_file";

export interface CitationMetadata {
  creator: string | null;
  creator_is_person: boolean;
  channel: string | null;
  title: string | null;
  published: string | null;
  platform: string | null;
  publisher: string | null;
  url: string | null;
  accessed: string | null;
  duration_seconds: number | null;
  source_type: SourceType;
  harvard_include_medium: boolean;
}

export interface CitationRun {
  text: string;
  italic: boolean;
}

export interface Citation {
  style: string;
  label: string;
  reference: CitationRun[];
  reference_text: string;
  in_text: string | null;
  note: string | null;
  note_label: string | null;
}

export interface CitationSet {
  citations: Citation[];
  warnings: string[];
}

export interface Results {
  job_id: string;
  metadata: CitationMetadata;
  citations: CitationSet;
  transcript: Transcript;
  study: StudyMaterials | null;
  summary_error: string | null;
  warnings: string[];
}

export interface PublicConfig {
  max_upload_mb: number;
  max_duration_seconds: number;
  default_model: string;
  models: string[];
  supported_sources: string[];
  retention_hours: number;
  keep_media: boolean;
  ai_notes: { available: boolean; label: string | null; provider: string | null };
}

export interface JobOptions {
  language: string;
  model: string;
  prefer_captions: boolean;
}
