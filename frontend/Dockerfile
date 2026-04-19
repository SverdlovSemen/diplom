FROM node:20-alpine

WORKDIR /app

COPY package.json package-lock.json* /app/
RUN npm install

COPY . /app

# npm run dev вызывает node_modules/.bin/vite; при копировании контекста без +x оболочка даёт "Permission denied".
# Запуск через node не зависит от прав на wrapper в .bin.
EXPOSE 5173

CMD ["node", "./node_modules/vite/bin/vite.js", "--host", "0.0.0.0", "--port", "5173"]
