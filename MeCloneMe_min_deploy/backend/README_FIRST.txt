README_FIRST — PWA Always‑Fresh (Android/iOS pełny ekran) — 1756594801

WRZUĆ‑I‑DZIAŁA (co robi paczka)
- Dodaje /pwa (ekran instalacji „Dodaj do ekranu głównego”)
- Wystawia service workera pod **/sw.js** i manifest pod **/manifest.webmanifest**
- Index (/) = splash → logo → „powered by Trustanica” → /onboarding
- Cache busting automatyczny (templates.env.globals["version"])

Jak wgrać (GitHub → Deploy)
1) W repo przeciągnij **cały folder `MeCloneMe_min_deploy`** z tej paczki i **nadpisz** pliki.
2) Zrób deploy (Render/Railway).
3) Wejdź:  /pwa  → postępuj wg instrukcji na ekranie (Android/iOS).

Szybkie linki (przykład Railway):
- /pwa
- /manifest.webmanifest
- /sw.js
- /onboarding
- /alerts/health
