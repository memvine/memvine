# memvine.dev

The homepage. Static files, no build step: `index.html`, `styles.css`, `vine.js` (the vine
that grows as the page is read) and `icon.svg`, plus `terms.html` and `privacy.html` (served at
`/terms` and `/privacy`; drafts until a lawyer has reviewed them). Open `index.html` in a browser, or
`python3 -m http.server 8787` here and visit `http://127.0.0.1:8787/`.

Deployed on Railway as the `www` service, root directory `site/`, custom domain `memvine.dev`
(see `deploy/railway/README.md`). The Dockerfile serves the files with nginx on Railway's `PORT`.

Links on the page point at `app.memvine.dev` (the console) and the GitHub repository; keep them in
step with `deploy/railway/README.md` when a domain changes.
