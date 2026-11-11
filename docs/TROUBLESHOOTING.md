# Troubleshooting

| Symptom | Cause and fix |
|---|---|
| "The speech recognition model could not be loaded" | The first run downloads the model from Hugging Face. Check outbound internet access, then press Retry. Pre-download with `PRELOAD_MODEL=small` (Docker) or set `TRANSCRIBLY_MODEL_DIR` to a folder that already contains it. |
| `ImportError: DLL load failed ... filename or extension is too long` (Windows) | The virtual environment path is too long for PyAV's DLLs. Create the venv somewhere short, e.g. `C:\venvs\transcribly`. |
| `faster-whisper` won't install | It needs Python 3.10–3.13 (no wheels for newer versions yet). `uv venv --python 3.12` is the quickest fix. |
| "This video could not be accessed" for a public YouTube video | YouTube sometimes asks servers to "confirm you're not a bot", especially from cloud/datacenter IPs. Update yt-dlp (`docker compose build --pull`), try later, or download the audio yourself and upload it. Transcribly does not bypass these checks. |
| "The video platform is limiting requests" | Rate limiting (HTTP 429). Wait and retry, or upload the file. |
| Transcription is very slow | Use a smaller model (`TRANSCRIBLY_WHISPER_MODEL=base`), keep `WORKER_CONCURRENCY=1` on small CPUs, or use a CUDA GPU. Check timing lines (`asr: ... rtf=`) in the logs. |
| Server runs out of memory | Lower the model size or concurrency. `small` needs ~1.2 GB per concurrent job, `medium` ~2.5 GB. |
| "This file couldn't be read as audio or video" | The file is corrupt or not a media file (Transcribly checks the content, not the extension). Re-export it, or convert to MP3/WAV. |
| "No usable audio track was detected" | The file has video only. Use a version with sound. |
| Job stuck at "Waiting in queue" | Another job is running (`WORKER_CONCURRENCY`), or the worker isn't running: if `TRANSCRIBLY_RUN_WORKER_IN_API=false`, start `python -m app.worker`. |
| "Result not available" when opening a link | Results are tied to the browser that created them (the access key is stored locally) and expire after the retention period. |
| PDF export shows boxes instead of characters | No Unicode font was found. Install DejaVu (`apt-get install fonts-dejavu-core`; included in the Docker image). `GET /api/health` reports `pdf_unicode_font`. |
| Copy buttons open a "Copy manually" box | The browser blocked clipboard access (common on plain `http://` origins other than localhost). Serve over HTTPS, or copy from the box. |
| UI on another domain can't reach the API | Set `NEXT_PUBLIC_API_BASE` at build time and add the UI origin to `TRANSCRIBLY_CORS_ORIGINS`. |
