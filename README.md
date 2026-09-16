# AI Code Screening

Мобильное приложение для HR и IT-рекрутеров, которое помогает проводить первичный screening разработчиков по GitHub-проектам.

## Что делает MVP

- принимает ссылку на GitHub repository;
- получает исходники через GitHub REST API;
- рассчитывает эвристический screening-score для подозрительных участков;
- показывает найденные файлы и причины;
- формирует вопросы, которые можно задать кандидату на собеседовании.

## Архитектура

```text
Android / Kotlin / Jetpack Compose
            |
            | HTTP POST /api/analyze
            v
FastAPI backend (Python)
            |
            +---- GitHub REST API
            |
            +---- analysis heuristics
            |
            +---- AI integration point (next iteration)
```

## Запуск backend

```bash
cd backend
python -m venv .venv
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Для приватных GitHub-репозиториев перед запуском backend задайте `GITHUB_TOKEN`.

## Запуск Android

Открой корень проекта в Android Studio, дождись Gradle Sync и запусти `app` на Android Emulator. Эмулятор обращается к backend компьютера через `http://10.0.2.2:8000`.

Проект настроен на Android Gradle Plugin 8.13.2, Gradle 8.13, Kotlin 2.3.21 и Java 17.

### Если Android Studio продолжает показывать старую ошибку Gradle

1. Закрой Android Studio.
2. Удали локальную папку `.gradle` внутри проекта, если она есть.
3. Запусти Android Studio снова и сделай `File → Sync Project with Gradle Files`.
4. Если Android Studio предлагает выбрать Gradle JDK, выбери JDK 17.

## Важное ограничение

Приложение не утверждает, что способно со 100% точностью установить, был ли код создан нейросетью. Score — это сигнал для дополнительной проверки. Финальная оценка кандидата остаётся за HR и техническим интервью.

## Идея курсовой

Проблема: HR сложно самостоятельно проверить реальный опыт разработчика по GitHub.

Решение: автоматизировать первичный анализ реальных проектов и подготовку вопросов, позволяющих проверить понимание кандидатом собственного кода.
