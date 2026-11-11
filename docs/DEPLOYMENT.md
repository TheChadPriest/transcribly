# Deployment guide

Transcribly ships as **one container**: FastAPI serves the API and the static UI, and runs the
transcription worker in background threads. Job state lives in SQLite on a persistent volume.

## Requirements for a host

| Need | Why |
|---|---|
| Docker or Python 3.10–3.13 + FFmpeg | media decoding and speech recognition |
| ≥ 2 GB RAM (`small` model), ≥ 1 GB (`base`) | the model plus decoding buffers |
| Long-running process (no request timeout on background work) | a 1-hour lecture takes minutes to tens of minutes on CPU |
| Persistent disk (a few GB) | job database, temporary audio, model cache |

**Do not** deploy the backend to serverless functions (Vercel/Netlify functions, Lambda): their execution
limits are far shorter than a transcription.

## 1. Any VPS or home server (most reliable)

```bash
git clone <your repo> transcribly && cd transcribly
cp .env.example .env            # adjust limits, retention, model
PRELOAD_MODEL=small docker compose up -d --build
curl http://localhost:8000/api/health
```

Put a reverse proxy with HTTPS in front (Caddy: `transcribly.example.com { reverse_proxy localhost:8000 }`)
and set `TRANSCRIBLY_TRUST_PROXY_HEADERS=true` so per-IP limits see real client addresses.

Cost: free on hardware you own. Small cloud VMs with 2 vCPU / 4 GB typically cost a few dollars a month;
some providers (e.g. Oracle Cloud's Always Free Ampere instances) offer free ARM VMs subject to availability
and their terms — check current terms before relying on them.

## 2. Hugging Face Spaces (Docker)

Spaces run Docker images and the CPU Basic hardware (2 vCPU, 16 GB RAM, non-persistent disk) is listed at no
hourly cost. **However**, as of October 2026 the Hub documentation states that compute (Docker/Gradio) Spaces
require a paid plan for new Spaces, community threads report `cpu-basic quota limit` errors on free accounts,
and free Spaces sleep when idle. Treat it as "may be free for your account; verify when you create it".

Steps:
1. Create a Space → SDK **Docker** → blank.
2. Push this repository to the Space, with this front matter at the top of the Space's `README.md`:
   ```yaml
   ---
   title: Transcribly
   sdk: docker
   app_port: 8000
   ---
   ```
3. In the Space settings add variables: `TRANSCRIBLY_WHISPER_MODEL=base` (2 vCPU is slow with `small`),
   `TRANSCRIBLY_MAX_DURATION_SECONDS=3600`, `TRANSCRIBLY_TRUST_PROXY_HEADERS=true`.
4. Without paid persistent storage, the disk is wiped on restart: in-flight and stored jobs are lost, and the
   model re-downloads (use `PRELOAD_MODEL=base` as a build arg to bake it in).

## 3. Render / Koyeb / Railway style free tiers

Render's free web services spin down after 15 minutes without inbound traffic (about a minute to wake),
lose their filesystem on every restart or spin-down, and are capped at 750 instance-hours a month. A
transcription job would be killed when the instance spins down, and free instances have too little RAM for
`small`. These tiers are suitable only for demos with `tiny`/`base` and short files. Paid instances
(~$7/month and up) remove the spin-down.

## Hosting the UI separately (optional)

The UI is a static export (`frontend/out`). To host it on Vercel/Netlify/GitHub Pages/Cloudflare Pages:

```bash
cd frontend
NEXT_PUBLIC_API_BASE=https://api.your-domain.example npm run build   # upload ./out
```

and on the backend set `TRANSCRIBLY_CORS_ORIGINS=https://your-ui-domain.example`.

## Scaling beyond one instance

- The SQLite store and in-process workers assume **one** process per data directory. Run uvicorn with a
  single worker. For a separate worker process on the same host: API with
  `TRANSCRIBLY_RUN_WORKER_IN_API=false` and `python -m app.worker` sharing the same `/data` volume.
- For multiple hosts, replace `app/storage/db.py` with a PostgreSQL implementation (same method surface)
  and run several worker processes; `claim_next_job` already uses an atomic claim.
- Throughput is roughly `WORKER_CONCURRENCY` × (cores per job). On CPU, one job using all cores is usually
  faster than several jobs sharing them.

## Operations

- Health: `GET /api/health` (also used by the Docker `HEALTHCHECK`).
- Logs: single-line to stdout; they include job id prefixes, stages, timings and error codes — never
  transcript text or full source URLs.
- Restart behaviour: jobs that were running are re-queued and resume from their last saved chunk; after
  `TRANSCRIBLY_MAX_ATTEMPTS` (3) interruptions a job fails with a clear message.
- Backups: the only state worth backing up is `transcribly.sqlite3` in the data volume (results expire anyway).
- Updating yt-dlp: platforms change often; rebuild the image periodically (`docker compose build --pull`).
