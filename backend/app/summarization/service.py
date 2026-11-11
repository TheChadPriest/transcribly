"""Study-notes generation: AI-written notes when a model is available, extractive notes otherwise.

The extractive notes always work (no model needed) and are the fallback whenever the AI step is
disabled or fails, so a finished transcript never loses its study notes.
"""

from __future__ import annotations

import logging
from typing import Callable

from ..ai.llm import LLMError, get_llm
from ..ai.notes import AINotesWriter, Report
from ..errors import Cancelled
from ..models import StudyMaterials, Transcript
from .extractive import ExtractiveSummarizer

log = logging.getLogger(__name__)


class StudyNotesService:
    def __init__(self, llm=None, use_ai: bool = True):
        self.llm = llm if llm is not None else (get_llm() if use_ai else None)

    @property
    def uses_ai(self) -> bool:
        return self.llm is not None

    def summarize(self, transcript: Transcript, title: str | None = None, report: Report | None = None,
                  should_cancel: Callable[[], bool] | None = None) -> StudyMaterials:
        extractive = ExtractiveSummarizer().summarize(transcript, title)
        if self.llm is None:
            return extractive
        try:
            return AINotesWriter(self.llm).write(transcript, title, report, should_cancel,
                                                 base_limitations=extractive.limitations)
        except Cancelled:
            raise
        except LLMError as e:
            reason = str(e)
        except Exception:
            log.exception("AI notes failed")
            reason = "The AI model hit an unexpected error."
        finally:
            release = getattr(self.llm, "release", None)
            if release:
                release()
        log.warning("AI notes unavailable, using extractive notes: %s", reason)
        extractive.notice = (f"AI study notes couldn't be written: {reason} The notes below are assembled "
                             "from sentences in the transcript instead.")
        return extractive


def get_summarizer() -> StudyNotesService:
    return StudyNotesService()
