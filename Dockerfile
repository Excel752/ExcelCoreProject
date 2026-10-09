FROM node:20-slim

WORKDIR /app

COPY package.json ./
RUN npm install --omit=dev

COPY bot.js formatter_core.js ./

CMD ["npm", "start"]
