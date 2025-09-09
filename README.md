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
