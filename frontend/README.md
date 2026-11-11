# Transcribly frontend

Next.js (static export) + Tailwind CSS. See the root [README](../README.md).

```bash
npm ci
NEXT_PUBLIC_API_BASE=http://localhost:8000 npm run dev   # UI on :3000 against a local API
npm run build        # static site in ./out (served by the backend or any static host)
npm test             # Vitest component tests
npm run test:e2e     # Playwright (needs a build and the backend's Python environment; set E2E_PYTHON)
```
