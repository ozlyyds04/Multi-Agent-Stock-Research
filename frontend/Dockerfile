FROM node:20-alpine AS build
WORKDIR /app
# 先拷 lockfile 再 npm ci：依赖安装可复现，且 lockfile 不变时走 Docker 层缓存
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
