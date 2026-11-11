"""User-facing errors. Messages here are safe to show; never put paths or raw exceptions in them."""

from __future__ import annotations

MESSAGES: dict[str, str] = {
    "unsupported_source": "This video source is not supported yet. Try another public video URL or upload a media file you are authorized to process.",
    "invalid_url": "That doesn't look like a valid web address. Paste a full link starting with https://.",
    "blocked_url": "That address points to a private or internal network and cannot be used.",
    "access_restricted": "This video could not be accessed. It may be private, restricted, unavailable, or blocked by the source platform.",
    "rate_limited": "The video platform is limiting requests right now. Wait a few minutes and try again, or upload the media file instead.",
    "live_stream": "Live streams can't be transcribed while they are live. Try again after the stream has ended.",
    "no_audio": "No usable audio track was detected. Try a different file.",
    "invalid_media": "This file couldn't be read as audio or video. It may be corrupt or in an unsupported format.",
    "too_large": "This file is larger than this server accepts. Try a smaller file, upload just the audio track, or use the local installation.",
    "too_long": "This video exceeds the processing capacity of the current server. Try a smaller file or use the local installation.",
    "transcription_failed": "Transcription could not be completed. Your job details are available, and you can retry if the problem is temporary.",
    "model_unavailable": "The speech recognition model could not be loaded (the first run downloads it). You can retry in a moment.",
    "no_speech": "No speech was detected in this recording.",
    "download_failed": "The audio could not be downloaded from the source. You can retry if the problem is temporary.",
    "interrupted": "Processing was interrupted repeatedly and has been stopped. You can retry the job.",
    "server_busy": "The server is busy with other jobs right now. Please try again in a few minutes.",
    "too_many_requests": "You've started a lot of jobs recently. Please wait a while before starting another.",
    "not_found": "This job doesn't exist or has expired.",
    "forbidden": "You don't have access to this job.",
    "not_ready": "Results aren't ready yet.",
    "missing_input": "Paste a video link or choose a file to upload.",
    "invalid_option": "One of the selected options isn't available on this server.",
    "export_failed": "The export could not be generated. You can still copy the content from the page.",
    "internal": "Something went wrong on our side. Please try again.",
}

RETRYABLE = {"model_unavailable", "download_failed", "transcription_failed", "rate_limited", "interrupted", "internal"}


class AppError(Exception):
    def __init__(self, code: str, http_status: int = 400, message: str | None = None):
        self.code = code
        self.http_status = http_status
        self.message = message or MESSAGES.get(code, MESSAGES["internal"])
        super().__init__(f"{code}: {self.message}")

    @property
    def retryable(self) -> bool:
        return self.code in RETRYABLE


class Cancelled(Exception):
    """Raised inside the pipeline when the user cancels a job."""
