# memvine.dev

The homepage. Static files, no build step: `index.html`, `styles.css`, `vine.js` (the vine
that grows as the page is read) and `icon.svg`; `how-it-works.html` (served at `/how-it-works`: one
lesson followed through a real run in four steps, with the two sessions as text and the console
screenshots `how-kept.png` and `how-rechecked.png`, made by `benchmark/how-it-works/run.py`; `how-memvine-works.svg` is the animated picture
the repository's README and docs show, and the site does not serve it); and `terms.html` and
`privacy.html` (served at `/terms` and `/privacy`; drafts until a lawyer has reviewed them). For search engines and link
previews: `robots.txt`, `sitemap.xml` (add each new page to it, and to the Dockerfile's `COPY`),
`og.png` (1200×630), and a canonical link on every page that names its `www.memvine.dev` address. Open `index.html` in a browser, or
`python3 -m http.server 8787` here and visit `http://127.0.0.1:8787/`.

The Dockerfile serves the files with nginx on the `PORT` its host sets (8080 by default).

Links on the page point at `app.memvine.dev` (the console) and the GitHub repository.
