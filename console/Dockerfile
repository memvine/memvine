# Memvine console (Next.js). MEMVINE_API_URL points at the API; leave MEMVINE_MANAGEMENT_KEY unset
# for a multi-tenant console where each organization signs in with its own key.
FROM node:22-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM node:22-slim
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build /app/package.json /app/package-lock.json ./
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/.next ./.next
EXPOSE 3000
CMD ["npx", "next", "start", "-p", "3000"]
