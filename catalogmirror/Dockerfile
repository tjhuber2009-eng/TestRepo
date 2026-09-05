FROM node:20-alpine
RUN apk add --no-cache openssl
WORKDIR /app
ENV NODE_ENV=production
COPY package.json ./
RUN npm install --omit=dev && npm cache clean --force
COPY . .
RUN npx prisma generate && npm run build
EXPOSE 3000
CMD ["npm", "run", "docker-start"]
