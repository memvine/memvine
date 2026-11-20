# The memvine.dev homepage: static files behind nginx. Railway sets PORT; nginx's template
# support fills it into the listen directive at start.
FROM nginx:1.27-alpine
ENV PORT=8080
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY index.html how-it-works.html terms.html privacy.html styles.css vine.js icon.svg how-kept.png how-rechecked.png home-made.png home-decide.png home-yours.png home-team.png home-made-phone.png home-decide-phone.png home-yours-phone.png home-team-phone.png og.png robots.txt sitemap.xml /usr/share/nginx/html/
EXPOSE 8080
