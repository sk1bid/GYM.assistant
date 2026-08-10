# GYM.assistant 👊🏻💪

![Python](https://img.shields.io/badge/python-3.12-blue.svg)
![Aiogram](https://img.shields.io/badge/aiogram-3.x-red.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-latest-009688.svg)
![Kubernetes](https://img.shields.io/badge/Kubernetes-Production-blue.svg)
![Docker](https://img.shields.io/badge/Docker-Ready-2496ED.svg)

**GYM.assistant** — Telegram-бот для планирования тренировок и Mini App к нему:
программа по дням недели, ведение тренировки подход за подходом, история и графики.

[Попробовать бота](https://t.me/gymassit_bot?start=readme)

---

## 🌟 Основные возможности

- **Тренировка в Mini App** — приложение ведёт по плану дня: вес и повторения
  предзаполнены прошлым разом, подход можно записать, поправить или пропустить.
- **Программы**: свои дни недели, упражнения из каталога или собственные, круговые
  блоки.
- **Пинги отдыха в чат.** Таймер живёт в базе, а не в браузере, поэтому уведомление
  придёт даже с закрытым приложением — телефон может лежать экраном вниз на скамье.
- **Статистика**: история сессий, личные рекорды, графики прогресса и календарь
  активности «квадратиками».
- **Админ-панель**: управление баннерами и базой упражнений прямо из Telegram.

---

## 🛠 Технологический стек

- **Backend**: Python 3.12, Aiogram 3.x (поллинг через SOCKS5), FastAPI.
- **Database**: PostgreSQL + SQLAlchemy (AsyncPG), Alembic для миграций.
- **Frontend**: ES-модули без сборки и без npm, Material 3.
- **DevOps**: Docker, Docker Compose, Kubernetes (k3s).
- **CI/CD**: GitHub Actions — сборка образов, миграции Job'ом и деплой на сервер.

---

## Быстрый запуск

### Локально через Docker Compose
Самый простой способ поднять бота с базой одной командой:

1. Склонируйте репозиторий.
2. Создайте `.env` на основе `.env.example`.
3. Запустите:
   ```bash
   docker-compose up -d --build
   ```

### Только Mini App
```bash
cd gymassistant
DB_URL=sqlite+aiosqlite:///./demo.db MINIAPP_BOT_TOKEN=<любой токен> \
  ./.venv-bot/bin/uvicorn miniapp.main:app --port 8099
```

---

## Структура проекта

- `gymassistant/` — основной исходный код.
  - `handlers/` — обработчики бота: точка входа и админка.
  - `workers/` — фоновые задачи бота, включая рассылку пингов отдыха.
  - `services/` — общие часы и вычисление текущего шага тренировки.
  - `database/` — модели данных и ORM-запросы, общие для бота и Mini App.
  - `miniapp/` — FastAPI-приложение и фронт (`miniapp/static/`).
  - `k8s/` — манифесты Kubernetes.
- `docker-compose.yml` — оркестрация для локальной разработки.
- `.github/workflows/` — автоматизация CI/CD.

---

## Документация

- [ONBOARDING.md](ONBOARDING.md) — раскладка репозитория, архитектура фронта,
  разобранные грабли, развёртывание. Начинать отсюда.
- [gymassistant/miniapp/README.md](gymassistant/miniapp/README.md) — почему гибрид
  бота и Mini App, и почему таймер отдыха живёт в базе.
