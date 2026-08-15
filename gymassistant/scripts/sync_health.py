"""
Перенос метрик Apple Health из hae-server в наш Postgres.

    python -m scripts.sync_health          (из каталога /app в образе бота)

Запускается CronJob'ом раз в сутки (k8s/health-sync-cronjob.yaml). Ходит в
hae-server по HTTP, а не в его MongoDB напрямую: у API стабильный контракт из
трёх полей, а лезть в чужое хранилище значило бы привязаться к его внутренней
схеме и тащить в образ бота драйвер Mongo ради одной задачи.

Читаем КАЖДЫЙ РАЗ ВСЮ историю метрики, хотя API умеет `?from=&to=` (epoch в мс —
так его дёргают дашборды самого проекта). Инкрементальность здесь не нужна и даже
вредна: полный проход занимает 14 секунд на 31 тысячу строк, зато самоисцеляется —
пропущенный по любой причине запуск не оставляет дыры, а уточнённые задним числом
замеры подхватываются сами. Апсерт по уникальному ключу делает повтор бесплатным
(database/orm_health.py). Фильтр пригодится, когда история перестанет тянуться
за приемлемое время; до тех пор он лишний рычаг.

Одна упавшая метрика не роняет прогон: их три десятка, и потерять двадцать девять
из-за одной было бы глупо. Ненулевой код возврата — только если не удалось ничего.
"""
import asyncio
import logging
import os
import sys

import aiohttp

from database.orm_health import orm_upsert_health_metrics
from database.engine import session_maker
from services.health_sync import rows_from

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

BASE_URL = os.getenv("HAE_BASE_URL", "https://sk1bid.ru/hae").rstrip("/")
READ_TOKEN = os.getenv("HAE_READ_TOKEN", "")
USER_ID = os.getenv("HEALTH_USER_ID", "")

# Секунд на одну метрику. Самые крупные (basal_energy_burned — под десять тысяч
# документов) отдаются мегабайтами и без пагинации, так что запас нужен щедрый.
TIMEOUT = int(os.getenv("HAE_TIMEOUT", "120"))

# Что тянем. Списком, а не автоопределением: у hae-server нет ручки «перечисли
# метрики» — читать их можно только поимённо. Список снят с живой базы 15 августа
# 2026; лишнее имя стоит одного пустого ответа, поэтому дешевле перечислить с
# запасом, чем не досчитаться. Переопределяется HEALTH_METRICS через запятую.
#
# `workouts` и `workout_routes` сюда не входят намеренно: это не метрики, у них
# своя ручка и своя форма (начало, конец, маршрут), и им нужна отдельная таблица.
DEFAULT_METRICS = (
    "active_energy",
    "apple_exercise_time",
    "apple_sleeping_wrist_temperature",
    "apple_stand_hour",
    "apple_stand_time",
    "basal_energy_burned",
    "blood_oxygen_saturation",
    "blood_pressure",
    "body_fat_percentage",
    "environmental_audio_exposure",
    "flights_climbed",
    "handwashing",
    "headphone_audio_exposure",
    "heart_rate",
    "heart_rate_variability",
    "physical_effort",
    "respiratory_rate",
    "resting_heart_rate",
    "six_minute_walking_test_distance",
    "sleep_analysis",
    "stair_speed_down",
    "stair_speed_up",
    "step_count",
    "time_in_daylight",
    "vo2_max",
    "walking_asymmetry_percentage",
    "walking_double_support_percentage",
    "walking_heart_rate_average",
    "walking_running_distance",
    "walking_speed",
    "walking_step_length",
    "weight_body_mass",
)


def metrics_to_sync() -> tuple[str, ...]:
    raw = os.getenv("HEALTH_METRICS", "")
    if not raw.strip():
        return DEFAULT_METRICS
    return tuple(name.strip() for name in raw.split(",") if name.strip())


async def fetch(session: aiohttp.ClientSession, name: str):
    """Документы одной метрики. None — сходить не удалось (не то же, что «пусто»)."""
    url = f"{BASE_URL}/api/metrics/{name}"
    try:
        async with session.get(url) as response:
            if response.status == 404:
                # Метрику ни разу не присылали — обычное дело для списка с запасом.
                return []
            if response.status != 200:
                logging.warning("%s: HTTP %s", name, response.status)
                return None
            return await response.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError) as error:
        logging.warning("%s: %s", name, error)
        return None


async def main() -> int:
    if not READ_TOKEN:
        logging.error("HAE_READ_TOKEN не задан")
        return 2
    if not USER_ID.isdigit():
        logging.error("HEALTH_USER_ID не задан или не число: %r", USER_ID)
        return 2

    user_id = int(USER_ID)
    names = metrics_to_sync()
    total, failed = 0, 0

    timeout = aiohttp.ClientTimeout(total=TIMEOUT)
    headers = {"api-key": READ_TOKEN}

    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as http:
        for name in names:
            docs = await fetch(http, name)
            if docs is None:
                failed += 1
                continue

            rows = rows_from(name, docs)
            if not rows:
                continue

            # Пишем метрику за метрикой, а не всё скопом в конце: упавший на
            # середине прогон должен оставить сделанное, а не откатить его.
            async with session_maker() as db:
                await orm_upsert_health_metrics(db, user_id, rows)

            total += len(rows)
            logging.info("%s: строк %s (документов %s)", name, len(rows), len(docs))

    logging.info("итого строк: %s, метрик не прочитано: %s", total, failed)

    # Полный провал (сеть, токен, упавший hae-server) — повод покраснеть в k8s.
    # Частичный — нет: остальное записано и повторится завтра.
    if failed == len(names):
        logging.error("не удалось прочитать ни одной метрики")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
