# Privacy notice

This describes the default behaviour of a Transcribly server. Whoever operates a server can change the
settings mentioned below; ask them if in doubt.

**What is processed.** The link you paste or the file you upload, the audio extracted from it, the transcript,
and the study notes and citation details generated from it.

**Where.** On the Transcribly server itself. Speech recognition (Whisper) and, by default, the AI that writes the
study notes (an open-source model run by Ollama) run locally on that server. Media and transcripts are not used to
train models. For links, the server contacts the video platform (e.g. YouTube) to read public metadata, captions
and audio.

**Exception — Claude.** An operator may switch the study notes to Anthropic's Claude API
(`TRANSCRIBLY_AI_PROVIDER=claude`). Then the transcript text and video title of each job are sent to Anthropic to
write the notes (not the audio or video). The app says which model wrote the notes on every result. Anthropic's
own terms and retention policies apply to that data.

**How long.**
- Uploaded and downloaded media is deleted as soon as processing finishes (or fails permanently). If a job fails
  in a way that can be retried, the prepared audio is kept until the result expires so the retry can resume.
- Transcripts and results are deleted automatically after the retention period (72 hours by default). You can
  delete a result sooner from its Export tab.
- For public links, a finished transcript may be reused for another request of the same link during the
  retention period, so the video is not processed twice. Uploaded files are never shared this way.

**Who can see results.** There are no accounts. Each job has a random identifier and a separate secret access
key that is kept only in your browser's local storage. Without that key, nobody — including someone who knows
the job's address — can open the result. Clearing your browser data removes your access.

**Logs.** Server logs record job identifiers, processing stages, timings and error codes. They do not record
transcript text or full source URLs. Client IP addresses are stored only as a salted hash, used for rate limits.

**Your responsibility.** Only submit media you are allowed to process. Transcribly does not bypass private
videos, logins, DRM or other access restrictions.
