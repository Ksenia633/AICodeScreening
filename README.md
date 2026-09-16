# AI Code Screening

Мобильное Android-приложение для HR и IT-рекрутеров, которое помогает проводить первичный screening разработчиков по GitHub-проектам.

## Текущий этап MVP

На данном этапе используется связка **Android + FastAPI backend + GitHub REST API**. Это промежуточная рабочая версия проекта. Автономную локальную базу и работу без backend добавим следующим этапом.

```text
Android / Kotlin / Jetpack Compose
            |
            | POST /api/analyze
            v
FastAPI backend (Python)
            |
            +---- GitHub REST API
            |
            +---- эвристический анализ кода
            |
            +---- вопросы для технического скрининга
```

### Что умеет текущая версия

- принимает ссылку на публичный GitHub repository;
- получает структуру и исходный код через GitHub REST API;
- рассчитывает screening-score по эвристическим признакам;
- показывает найденные участки и причины для дополнительной проверки;
- формирует вопросы для технического интервью.

## Запуск backend

Открой Terminal в Android Studio:

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

`run.py` запускает FastAPI на `0.0.0.0:8000`.

Проверка:

```text
http://127.0.0.1:8000/docs
```

В Swagger должны отображаться `GET /health` и `POST /api/analyze`.

Для приватных GitHub-репозиториев перед запуском backend можно задать `GITHUB_TOKEN`.

## Запуск Android

Открой корень проекта в Android Studio, дождись Gradle Sync и запусти `app` на Android Emulator.

Эмулятор обращается к backend компьютера через:

```text
http://10.0.2.2:8000
```

Поэтому backend должен быть запущен во время текущего MVP-сценария.

Проект настроен на Android Gradle Plugin 8.13.2, Gradle 8.13, Kotlin 2.3.21 и Java 17.

### Если Android Studio показывает ошибку Gradle

1. Закрой Android Studio.
2. Удали локальную папку `.gradle` внутри проекта, если она есть.
3. Запусти Android Studio снова.
4. Выполни `File → Sync Project with Gradle Files`.
5. Если Android Studio предлагает выбрать Gradle JDK, выбери JDK 17.

## Важное ограничение

Приложение не утверждает, что способно со 100% точностью установить, был ли код создан нейросетью. Score — это вспомогательный сигнал для дополнительной проверки. Финальная оценка кандидата должна основываться на совокупности данных и техническом интервью.

## Следующий этап

После стабилизации текущего MVP можно добавить локальную SQLite/Room-базу, историю анализов и автономный демонстрационный режим, чтобы приложение не зависело от backend компьютера.
