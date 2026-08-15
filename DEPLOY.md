# Put Sersi on your phone (auto-refreshing) — GitHub Pages

**The key fact:** the daily refresh and your phone can't both use your Mac. The
local `refresh.sh` writes files your phone can't see. To have it **on your phone
AND updating itself daily**, run the refresh in the cloud and serve it at a URL.
GitHub does both for free.

What you get: a link like `https://<you>.github.io/propintel/` that **rebuilds
itself every morning** (ABS data + news + report) and installs on your phone home
screen as an app called **Sersi**.

## One-time setup (~10 min)

1. **Create a GitHub account** (free) if you don't have one: https://github.com

2. **Create a repository** — name it e.g. `propintel`. Make it **Public**
   (free GitHub Pages needs a public repo; the report is only aggregate public
   ABS/government data — no personal info, and your `.env` is git-ignored so your
   Domain keys are never uploaded). *(Private repo instead? That needs GitHub Pro.)*

3. **Push this project** to it. In Terminal, from the project folder:
   ```bash
   cd "/Users/gullesh/Claude Code/Domain Scraper"
   git init
   git add .
   git commit -m "Sersi property growth map"
   git branch -M main
   git remote add origin https://github.com/<YOUR-USERNAME>/propintel.git
   git push -u origin main
   ```
   (`.gitignore` already excludes `.env`, `data/*.db`, tokens — secrets stay local.)

4. **Turn on Pages via Actions:** on GitHub → your repo → **Settings → Pages** →
   under *Build and deployment*, set **Source = GitHub Actions**. Save.

5. The **"Sersi daily refresh & deploy"** workflow runs automatically (on push,
   then daily at ~7am AEST). Watch it under the repo's **Actions** tab. When it
   finishes, your URL appears there and in **Settings → Pages**.

## On your phone

1. Open the URL in **Safari (iPhone)** or **Chrome (Android)**.
2. **Add to Home Screen** (iPhone: Share → Add to Home Screen · Android:
   ⋮ → Install app / Add to Home screen).
3. It appears as **Sersi** with an icon and opens full-screen like an app.
   Every morning it's already refreshed — open it and read Sersi's update at the
   top of the Summary tab.

## Run it on demand
Repo → **Actions** → *Sersi daily refresh & deploy* → **Run workflow**. Rebuilds
and redeploys in ~1–2 minutes.

## Notes
- The cloud run pulls **ABS (public API) + the news RSS feeds** — no keys needed.
- Schedule time: edit the `cron` in `.github/workflows/refresh.yml`
  (it's in UTC; `0 21 * * *` = 7am AEST).
- Government funding/jobs (`catalysts.json`) is curated — Sersi flags in the daily
  update when it's >90 days old; ask me to refresh it then.
