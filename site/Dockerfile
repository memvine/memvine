# The memvine.dev homepage: static files behind nginx. Railway sets PORT; nginx's template
# support fills it into the listen directive at start.
FROM nginx:1.27-alpine
ENV PORT=8080
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY index.html styles.css vine.js icon.svg /usr/share/nginx/html/
EXPOSE 8080
