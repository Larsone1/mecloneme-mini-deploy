# MeCloneMe (MVP)

[![Deploy](https://github.com/Larsone1/mecloneme-mini-deploy/actions/workflows/deploy.yml/badge.svg)](https://github.com/Larsone1/mecloneme-mini-deploy/actions/workflows/deploy.yml)

**Live:** https://Larsone1.github.io/mecloneme-mini-deploy/

MeCloneMe to system klonów-asystentów (web + mobile), który łączy onboarding PWA, emocjonalny UX i panel CEO/CTO z pełnym mirrorem pracy na GitHubie. Celem MVP jest: (1) szybki onboarding użytkownika (selfie+voice – placeholdery), (2) minimalny front web (Vite + React), (3) pipeline push/pull (gsync/gpull) dla pełnej widoczności CTO.

## Szybki start (lokalnie)

cd web
npm install
npm run dev -- --host

## Sesje i pliki

- Endpoint: `POST /api/session/new` → `{ sid }`
- Upload audio: `POST /api/upload/audio (file, sid)`
- Upload image: `POST /api/upload/image (file, sid)`
- Publiczny podgląd: `/files/{sid}/...`

## Backend (Render, darmowy)

1. Połącz repo z Render → New + Web Service → Deploy from repo (Blueprint `render.yaml`).
2. Po deployu skopiuj publiczny URL backendu (np. https://mecloneme-backend.onrender.com).
3. Włącz CORS (jest w API).
4. W Pages ustaw `VITE_API_URL` w build time: Settings → Pages → Build → Environment variables → `VITE_API_URL=<Twój URL>`. Zrób redeploy.
