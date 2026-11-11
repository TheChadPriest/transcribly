"""Pydantic models shared by the API, pipeline, and exporters."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class Stage(str, Enum):
    validating = "validating"
    metadata = "metadata"
    captions = "captions"
    downloading = "downloading"
    preparing = "preparing"
    transcribing = "transcribing"
    summarizing = "summarizing"
    ai_notes = "ai_notes"
    citations = "citations"
    finalizing = "finalizing"
    cache = "cache"


STAGE_LABELS: dict[str, str] = {
    "validating": "Validating source",
    "metadata": "Retrieving metadata",
    "captions": "Checking captions",
    "downloading": "Extracting audio",
    "preparing": "Preparing audio",
    "transcribing": "Transcribing",
    "summarizing": "Generating summary, key points and topics",
    "ai_notes": "Reading the transcript and writing study notes",
    "citations": "Preparing citations",
    "finalizing": "Finalizing results",
    "cache": "Reusing an existing transcript of this source",
}

TERMINAL = {JobStatus.completed, JobStatus.failed, JobStatus.cancelled}

TranscriptSource = Literal["human_captions", "auto_captions", "speech_recognition"]


class Segment(BaseModel):
    index: int
    start: float
    end: float
    text: str
    speaker: str | None = None
    confidence: float | None = None  # approx. probability derived from the engine's avg log-prob
    low_confidence: bool = False


class Paragraph(BaseModel):
    index: int
    start: float
    end: float
    segment_indices: list[int]


class Transcript(BaseModel):
    language: str | None = None
    language_probability: float | None = None
    source: TranscriptSource
    engine: str | None = None
    model: str | None = None
    duration: float | None = None
    segments: list[Segment]
    paragraphs: list[Paragraph] = []

    @property
    def text(self) -> str:
        return " ".join(s.text for s in self.segments)


class Highlight(BaseModel):
    """A passage of the transcript, identified by segment indices (never rewritten text)."""

    segment_indices: list[int]
    start: float
    end: float
    excerpt: str


class CitedText(BaseModel):
    """Written text plus the transcript passages it is based on."""

    text: str
    refs: list[Highlight] = []


class KeyPoint(BaseModel):
    id: str
    heading: str
    text: str
    timestamp: float | None = None
    support: Highlight | None = None
    refs: list[Highlight] = []


class Concept(BaseModel):
    term: str
    explanation: str
    explanation_kind: Literal["definition", "context"]
    timestamp: float | None = None
    mentions: int = 0
    refs: list[Highlight] = []


class Topic(BaseModel):
    id: str
    title: str
    summary: str
    start: float
    end: float
    subtopics: list[str] = []
    passages: list[Highlight] = []


class Conclusion(BaseModel):
    text: str
    timestamp: float | None = None
    refs: list[Highlight] = []


class StudyMaterials(BaseModel):
    method: str  # "extractive" or "ai:<provider>:<model>"
    executive_summary: str
    overview: str
    key_points: list[KeyPoint]
    concepts: list[Concept]
    conclusions: list[Conclusion]
    topics: list[Topic]
    study_questions: list[str]
    limitations: list[str]
    # AI notes: who wrote them, the summary as referenced paragraphs, questions with where-to-look links
    generator: Literal["ai", "extractive"] = "extractive"
    generated_by: str | None = None
    summary_paragraphs: list[CitedText] = []
    questions: list[CitedText] = []
    notice: str | None = None  # e.g. why AI notes fell back to extractive notes


SourceType = Literal["youtube_video", "online_video", "lecture", "documentary", "interview", "uploaded_file"]


class CitationMetadata(BaseModel):
    creator: str | None = None
    creator_is_person: bool = False
    channel: str | None = None  # platform account / uploader, when different from the creator
    title: str | None = None
    published: str | None = Field(default=None, description="YYYY, YYYY-MM or YYYY-MM-DD")
    platform: str | None = None
    publisher: str | None = None
    url: str | None = None
    accessed: str | None = Field(default=None, description="YYYY-MM-DD")
    duration_seconds: float | None = None
    source_type: SourceType = "online_video"
    harvard_include_medium: bool = False


class CitationRun(BaseModel):
    text: str
    italic: bool = False


class Citation(BaseModel):
    style: str
    label: str
    reference: list[CitationRun]
    reference_text: str
    in_text: str | None = None
    note: str | None = None
    note_label: str | None = None


class CitationSet(BaseModel):
    citations: list[Citation]
    warnings: list[str]


class JobOptions(BaseModel):
    language: str | None = None
    model: str | None = None
    prefer_captions: bool = True


class StageEvent(BaseModel):
    stage: str
    label: str
    started_at: float
    finished_at: float | None = None


class JobState(BaseModel):
    job_id: str
    status: JobStatus
    stage: str | None
    stage_label: str | None
    detail: str | None
    progress: float | None = Field(None, description="0..1, only when measured (e.g. audio transcribed)")
    stages: list[StageEvent]
    source_type: str
    title: str | None
    duration: float | None
    error: ApiErrorBody | None = None
    warnings: list[str] = []
    created_at: float
    updated_at: float
    expires_at: float | None


class ApiErrorBody(BaseModel):
    code: str
    message: str


class JobCreated(BaseModel):
    job_id: str
    token: str
    status: JobStatus


class Results(BaseModel):
    job_id: str
    metadata: CitationMetadata
    citations: CitationSet
    transcript: Transcript
    study: StudyMaterials | None
    summary_error: str | None = None
    warnings: list[str] = []


JobState.model_rebuild()
