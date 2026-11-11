# Test report — 26 October 2026

Environment: Windows 11, 20 logical CPUs, no GPU, Python 3.12.15 (faster-whisper 1.2.1, CTranslate2 4.8.4,
int8), Node 24, Next.js 16.4, FFmpeg 7.1 (imageio-ffmpeg build; no system `ffprobe`, so media inspection used
the PyAV path). Docker was **not** available on this machine.

## Automated suites

| Suite | Result |
|---|---|
| Backend unit + integration (`pytest`) | **114 passed** (6 real-transcription tests skipped by default) |
| Backend real transcription (`TRANSCRIBLY_REAL_TESTS=1`, model `small`) | **6 / 6 passed** |
| Backend lint (`ruff`) | clean |
| Frontend lint (`eslint`) + typecheck (`tsc`) | clean |
| Frontend component tests (Vitest) | **28 passed** |
| End-to-end (Playwright, real backend, test engine), desktop 1366×900 + Pixel 7 | **12 passed** |
| End-to-end full workflow with real Whisper `small` (desktop) | **1 passed** (49 s) |
| Backend suite repeated 9× after the race fix below | 9 / 9 green |

Coverage highlights: URL validation, SSRF blocking (private IPs, IPv4-mapped IPv6, DNS resolving to private
ranges, redirects re-validated per hop), YouTube URL normalization, caption parsing (rolling WebVTT, SRT,
json3), timestamp formatting/parsing, chunk-boundary de-duplication, paragraph building, all five citation
styles against published examples (org/person authors, missing date/creator, question-mark titles, uploaded
files never getting a URL), grounded summaries (every summary/key-point sentence verified to be verbatim
transcript text), Ollama layer with mocked HTTP incl. fallback, exports (5,000-segment transcript fully present
in DOCX, 50+ page PDF), job lifecycle (create, poll, cancel queued/running, retry, delete), idempotent
submissions, access control (wrong/missing tokens, path tricks), limits (size, duration, per-IP, queue),
corrupt/no-audio/fake-extension files, restart recovery, **resume from saved chunks**, retention cleanup,
summary failure keeping the transcript, result caching, and queued jobs processed in order.

## Real transcription results (faster-whisper `small`, CPU int8)

Test media is synthesized with Windows SAPI voices from known text (`backend/scripts/make_test_media.py`).

| Case | Audio | Wall time | Result |
|---|---|---|---|
| Short clean speech (WAV) | 16.1 s | 12.2 s* | WER **0.0 %** |
| Lecture, 7 paragraphs, 3 voices (M4A), forced into 40 s chunks | 132 s | 38.1 s | WER **2.1 %**; no duplicated segments; first and last sentences present |
| Multiple segments with pauses (MP3) | 60.2 s | 16.1 s | all expected terms present |
| Speech with 8 s / 12 s / 6 s silences | 56.1 s | 23.6 s | first segment starts at the right time; nothing invented in silence |
| Speech + white noise at 10 dB SNR | 9.5 s | 5.4 s | WER **0.0 %** |
| MP4 with video track | 16.1 s | 5.9 s | correct |
| Video file without audio | — | — | rejected: "No usable audio track was detected" |
| Corrupt file named `.mp4` / `.mp3` | — | — | rejected: "couldn't be read as audio or video" |
| File over the configured limit | — | — | rejected with HTTP 413 and a clear message |
| 22-minute recording (lecture looped 10×) | 1,320 s | 268 s ASR (RTF **0.20**), 273 s total | completed; peak process memory 1.17 GB |

\* includes model load (3.3 s from the local cache).

### Real video sources (network)

| Case | Result |
|---|---|
| YouTube video with creator captions (`jNQXAC9IVRw`, 19 s) | completed in 2.8 s using the captions; correct title/channel/date; APA: `jawed. (2005, April 24). Me at the zoo [Video]. YouTube. https://…` |
| Same video with captions disabled (audio download + Whisper) | completed in 14.6 s; audio-only stream downloaded; temporary files removed |
| Non-existent YouTube video | failed cleanly: "This video could not be accessed…" |

YouTube may behave differently from cloud IP ranges (bot checks); that could not be tested here.

## Other measurements

- API startup to healthy: **2.1 s** (no model loaded until the first job).
- Process memory before a model is loaded: ~100 MB; during `small` transcription: ~0.8–1.2 GB.
- First-run model download (`small`) took ~50 s here and failed once transiently; the retry logic recovered.

## Defects found and fixed during testing

1. **Chunk timestamps overlapped / sentences split mid-way** — Whisper's segment timestamps could exceed the
   chunk and split sentences. Now word timestamps are regrouped into sentence-aligned segments, clamped to
   the chunk.
2. **Summary crash (`ZeroDivisionError`)** on phrases spanning punctuation — key phrases no longer cross
   punctuation; the failure also proved the transcript survives a summary crash.
3. **Weak concept/heading choices** ("Water") — single-word concepts now need a definition in the video;
   headings prefer defined concepts and multi-word phrases.
4. **MLA titles ended with a comma** instead of a period — fixed and covered by tests.
5. **`javascript:` URLs crashed validation** with an unhandled exception — now a friendly `invalid_url`.
6. **Race in cleanup**: the janitor could delete an upload's directory before its job row existed, failing
   the job. Orphan directories now need to be an hour old before removal.
7. **Model download failures** failed the job immediately — now retried 3× and reported as retryable.
8. **UI**: timestamps wrapping onto two lines, a stray scrollbar on the tab bar, an empty step list in the
   moment between claiming a job and its first stage, search-match ids drifting when sections were collapsed.

## Not verified / known limitations

- **Docker image build** — Docker is not installed on this machine. The Dockerfile and a CI job that builds and
  smoke-tests it are included, but the image has not been built here.
- **Linux `ffprobe` code path** — exercised only in CI (locally the PyAV fallback was used).
- **Optional Ollama summaries** — tested with a mocked HTTP server only; no local LLM was available.
- **GPU** — not available; CUDA selection is untested.
- **Public deployment** — not done (requires your hosting account); see [DEPLOYMENT.md](DEPLOYMENT.md).
- Topic segmentation and headings are heuristic and English-tuned; they are reasonable on lectures with clear
  structure and weaker on conversational content. Speaker diarization is not implemented (no labels are
  invented).
- Citation templates follow standard online-video examples; unusual source types still need checking against
  the institution's guide (the UI says so).

---

# Update — AI-written study notes (31 October 2026)

Study notes are now written by a language model that reads the whole transcript first, with every statement
linked to the transcript lines it is based on. Default: Qwen 2.5 7B (Q4_K_M) through Ollama 0.40.13 on the
server; optional: Claude via the Anthropic API.

## Automated tests

| Suite | Result |
|---|---|
| Backend (`pytest`) | **123 passed**, 6 real-Whisper tests skipped by default |
| End-to-end (Playwright) | **12 passed** |
| Frontend component tests (Vitest) | **32 passed** |
| Lint / typecheck (ruff, eslint, tsc) | clean |

New coverage (fake models, deterministic): strict JSON schemas; single-pass notes use every numbered line;
leaked line numbers ("[17, 18]", "(Refs: 2, 3)") stripped from prose; invalid line numbers removed; over-cited
references pruned; a statement citing the wrong line re-linked to the right passage; an unsupported statement
removed and reported; topics re-ordered and made contiguous; excerpts are verbatim; long transcripts read in parts
covering every line exactly once, then written whole; fallback to extractive notes with a visible reason; Ollama
client streaming/truncation/missing model; Claude request shape (structured output, effort, `fallbacks: "default"`)
and refusal handling; "Write AI study notes" endpoint rewrites notes without touching the transcript; retry
starts a clean run. UI: AI label, reference links highlighting whole passages, verbatim quote reveal, the
write-notes button, fallback notice.

## Real model runs (this laptop: i7-12700H, 16 GB RAM, no GPU, ~1–3 GB free RAM)

- Raw speed: **28 tokens/s reading, 6 tokens/s writing** (CPU only).
- 2-minute water-cycle lecture: notes in 5 min 22 s (first prompt) and 10 min 30 s (second prompt, longer
  output while the machine was swapping). The second version covered all four stages as key points, with
  precise references.
- Quality observed: clear, well-organized explanations in the model's own words, correct references for every
  key point after grounding. Weaknesses: occasional overgeneralization not stated in the video (e.g. "essential
  for supporting life"), which the reference check cannot catch because it shares vocabulary with real passages.
- **Defect found and fixed:** with the AI model resident in RAM, Whisper could not allocate memory
  (`mkl_malloc: failed to allocate memory`) and the job failed. The pipeline now unloads the AI model before
  transcription and frees Whisper before writing notes; out-of-memory errors from Ollama show a clear message.
- **In-app run (full workflow):** the lecture job in the running app, then **Write AI study notes**: AI step
  completed in **6 min 44 s**, no fallback. Notes: 3-paragraph summary, 4 key points (evaporation, condensation,
  precipitation, collection), 4 defined concepts, 2 conclusions, 4 study questions, each linked to the correct
  passages. Verified in the browser: AI label; clicking a summary timestamp opens the transcript with the whole
  cited passage highlighted; "Show what the video says" reveals the verbatim sentences. Remaining flaws seen:
  one small embellishment ("depending on the temperature") and a single topic for this 2-minute video.
- **Resilience:** one earlier run crashed Ollama when the laptop ran out of RAM; the job still completed with
  transcript-sentence notes and a visible explanation, and the new button upgraded it afterwards.
- **Also fixed:** retrying a failed job no longer shows the failed attempt's steps; the elapsed-time label counts
  from the current run.
- End-to-end (Playwright, desktop + mobile): **12 passed** after these changes.
- **Not verified:** Claude was not run live (no API key on this machine); its request code is covered only by
  tests against a fake client.

---

# 10-minute educational video test (1 November 2026)

Video: Khan Academy, "History and development of cell theory" (YouTube `L3nH3KTNS3E`, 11 min 14 s), submitted
through the app's homepage (paste link → Generate transcript).

| Step | Result |
|---|---|
| Source check | "YouTube" badge shown while typing |
| Metadata + captions | 8 s; creator captions used (95 segments, 1,863 words) — no speech recognition needed |
| AI notes (Qwen 2.5 7B, CPU, ~1 GB free RAM) | 17 min 20 s; 3-paragraph summary, 6 key points, 3 concepts, 6 topics in order, 6 study questions, all referenced |
| Citations | correct in all 5 styles (channel, date 2015-01-21, URL, 11:13 duration, access date) |
| Exports | TXT / MD / DOCX / PDF (5 pages): all 95 segments present, all 9 sections, AI attribution line |

**Fact check of the AI notes against the transcript:** correct — Leeuwenhoek, dental scrapings, "animacules",
"father of modern microbiology", Hooke and the cork, "cellula", Schleiden and Schwann (1837/1838),
abiogenesis/spontaneous generation, Pasteur's swan-neck flask, binary fission, "200 years", the three tenets.
**Wrong:** (1) "Virchow … coined the phrase *omnis cellula e cellula*" — the video says the phrase was coined
earlier by Raspail (7:54–8:06); (2) "Schleiden and Schwann … established the first two tenets" — the video
credits the first tenet to the earlier period (2:43). Both cited the right passages, so reference checking could
not catch them.

**Fact-check pass experiment (7B model):**
1. Free rewriting: a full rewrite took 21 min; the new draft had *more* errors and the checker turned 8 correct
   statements into confusing ones and removed a correct key point. Rejected.
2. Evidence-gated (changes only with a verbatim, machine-verified quote from the lines; size and grounding
   limits): 16 min on the first draft; caught neither known error and made two small harmful edits (removed a
   correct "father of modern microbiology" clause; dropped Pasteur from a summary sentence).
3. Decision: the fact-check pass now runs only with Claude by default (`TRANSCRIBLY_AI_FACT_CHECK=auto`); the
   video's original first draft was restored.

**Fixed during this test:** in-text short titles no longer end on a function word ("History and development",
not "History and development of"); stray "()" removed from AI text; retry/elapsed-time issues from earlier.

**Conclusion:** the full workflow works on a real 11-minute lecture, and the notes are well structured and
referenced. A 7B local model is good for a first pass but not reliable enough on its own for facts students
will be graded on; Claude (not testable here without an API key) is the recommended option when accuracy matters.

Automated tests after these changes: backend **127 passed**, frontend 32 passed, end-to-end 12 passed (last run
before the citation tweak; unaffected code paths).
