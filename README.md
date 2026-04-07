# Gauge Reader System

Серверная система приёма видеопотоков и регистрации показаний визуально отображаемых измерительных приборов (аналоговые/цифровые) на основе компьютерного зрения.

На текущем этапе репозиторий содержит **полностью запускаемый скелет**:
- RTMP-шлюз (`nginx-rtmp`) для приёма push-потоков
- Backend (`FastAPI` + `SQLAlchemy 2` + `Alembic`, PostgreSQL, Redis)
- Frontend (`React` + `Vite` + `TypeScript` + `Tailwind`)

## Быстрый старт (Docker)

Требования: установленный Docker Desktop.

Запуск:

```bash
cd gauge-reader-system
docker-compose up --build
```

Проверка:
- Backend Swagger: `http://localhost:8000/docs`
- Frontend: `http://localhost:5173`
- RTMP ingest: `rtmp://localhost:1935/live/<stream_key>`

Пример: поток со смартфона (любой RTMP broadcaster) направьте на:
`rtmp://<ip_вашего_компьютера>:1935/live/logger-1`

## Тестовый RTMP-поток (FFmpeg)

На Windows с Docker Desktop:

```bat
cd gauge-reader-system\scripts
.\test_stream.bat logger-1
```

Если `localhost` не работает (в рамках Docker), укажите `host.docker.internal`:

```bat
.\test_stream.bat logger-1 host.docker.internal
```

На Linux/Mac/WSL:

```bash
cd gauge-reader-system/scripts
./test_stream.sh logger-1
```

Проверка статуса RTMP (nginx):
- `http://localhost:8080/stat` (должен быть виден активный поток)

## Миграции БД (Alembic)

В Docker миграции выполняются при старте backend (см. `backend/entrypoint.sh`).

Локально (опционально):

```bash
cd backend
alembic upgrade head
```

## Структура

- `backend/` — FastAPI API + модели/схемы/миграции
- `frontend/` — веб-интерфейс (Dashboard/Loggers/LoggerSetup)
- `nginx/` — конфигурация RTMP + http status

## Следующие шаги (этап 2+)

- Подключить захват кадров из RTMP и пайплайн CV (OpenCV/YOLO/OCR) через очередь задач (Celery)
- Добавить аутентификацию и роли (admin/viewer) на API и UI
- Реализовать интерактивную настройку ROI и калибровку шкалы (canvas)

