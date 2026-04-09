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

Аналоговый поток со стрелкой (logger-1, смена кадра раз в 8 сек):

```bat
cd gauge-reader-system\scripts
py .\generate_analog_sequence.py
.\test_analog_stream.bat logger-1 127.0.0.1 8
```

Для `logger-1` в `Logger setup` используйте калибровку из файла:
- `test_images/analog_sequence/calibration_logger1.json`
- `roi_json` оставьте на весь кадр: `{"x":0,"y":0,"w":640,"h":480}`

Рекомендуемый интервал опроса для этого теста:
- `sample_interval_sec = 8` (или 7-10)

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

### Два потока одновременно (docker profiles)

- Цифровой тестовый поток (`logger-2`):
  - `docker compose --profile test-stream up -d ffmpeg-test`
- Аналоговый поток со стрелкой (`logger-1`):
  - `py scripts/generate_analog_sequence.py`
  - `docker compose --profile test-stream-analog up -d ffmpeg-analog`

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

