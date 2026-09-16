# AI Code Screening

Мобильное приложение для HR и IT-рекрутеров, которое помогает проводить первичный screening разработчиков по публичным GitHub-проектам.

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

## Запуск

### Backend

```bash
cd backend
python -m venv .venv
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### Android

Открыть корень проекта в Android Studio, дождаться Gradle Sync и запустить `app` на эмуляторе. Эмулятор обращается к локальному backend хоста через `10.0.2.2:8000`.

Стек Android использует Jetpack Compose, актуальный Kotlin Compose Compiler plugin и Compose BOM.

## Важное ограничение

Приложение не утверждает, что способно со 100% точностью установить, был ли код создан нейросетью. Score — это сигнал для дополнительной проверки. Финальная оценка кандидата остаётся за HR и техническим интервью.

## Идея курсовой

Проблема: HR сложно самостоятельно проверить реальный опыт разработчика по GitHub.

Решение: автоматизировать первичный анализ реальных проектов и подготовку вопросов, позволяющих проверить понимание кандидатом собственного кода.
