# memvine.dev

The homepage. Static files, no build step: `index.html`, `styles.css`, `vine.js` (the vine
that grows as the page is read) and `icon.svg`; `how-it-works.html` (served at `/how-it-works`: one
lesson followed through a real run in four steps, with the two sessions as text and the console
screenshots `how-kept.png` and `how-rechecked.png`; `how-memvine-works.svg` is the animated picture
the repository's README and docs show, and the site does not serve it); and `terms.html` and
`privacy.html` (served at `/terms` and `/privacy`; drafts until a lawyer has reviewed them). Open `index.html` in a browser, or
`python3 -m http.server 8787` here and visit `http://127.0.0.1:8787/`.

Deployed on Railway as the `www` service, root directory `site/`, custom domain `memvine.dev`
(see `deploy/railway/README.md`). The Dockerfile serves the files with nginx on Railway's `PORT`.

Links on the page point at `app.memvine.dev` (the console) and the GitHub repository; keep them in
step with `deploy/railway/README.md` when a domain changes.
