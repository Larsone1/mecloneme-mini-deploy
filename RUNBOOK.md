# RUNBOOK — MeCloneMe (Gemini workflow)

## Zasada

- Każde zadanie = blok **DLA GEMINI** → Gemini edytuje pliki, uruchamia polecenia, robi commit+push.
- CEO pisze wizję, CTO podaje kroki, Gemini wykonuje.
- Po kroku piszemy krótkie: **DONE (opis)**.

## Szablony

### Patch pliku

ZADANIE: [krótko co i po co]
PATCH ścieżka/do/pliku (NADPISZ/WSTAW FRAGMENT):
...treść...
POLECENIA:
git add ścieżka/do/pliku
git commit -m "<typ>: <opis>"
git push

### Dodanie workflow

PLIK .github/workflows/<nazwa>.yml (NADPISZ/UTWÓRZ):
...yaml...
POLECENIA:
git add .github/workflows/<nazwa>.yml
git commit -m "ci: <opis>"
git push

### Smoke-test

POLECENIA:
curl -s http://localhost:8000/api/health || true
curl -s http://localhost:8000/api/version || true
echo "PAGES → https://Larsone1.github.io/mecloneme-mini-deploy/"

## Szybkie komendy

- reset sesji: POST /api/session/soft-reset (sid)
- eksport historii: GET /api/session/export?sid=<sid>
- upload audio: POST /api/upload/audio (file+sid)
- upload image: POST /api/upload/image (file+sid)
