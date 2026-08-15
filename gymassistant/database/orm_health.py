"""
Запись метрик Apple Health. Отдельным модулем, потому что здоровье — не тренировки:
приложение эти строки не читает, их пишет только синхронизатор.
"""
import logging

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import HealthMetric

# Сколько строк уходит одним INSERT. Полная выгрузка — это десятки тысяч строк,
# и одним запросом их слать нельзя: у asyncpg предел на число параметров,
# а у Postgres — на размер запроса.
CHUNK = 1000


async def orm_upsert_health_metrics(session: AsyncSession, user_id: int, rows) -> int:
    """
    Кладёт метрики, обновляя уже существующие. Возвращает число обработанных строк.

    Именно апсерт, а не вставка: Health Auto Export шлёт перекрывающиеся окна, и
    один и тот же день приезжает при каждой синхронизации. Вставка плодила бы
    дубли, а «сначала удалить, потом вставить» на полсекунды оставляла бы дыру
    в данных и требовала бы знать, что удалять.

    Обновляем ЗНАЧЕНИЕ, а не игнорируем конфликт: Apple задним числом уточняет
    замеры (тот же день после досчёта на часах приходит с другим qty), и свежая
    присылка авторитетнее прошлой.
    """
    rows = list(rows)
    if not rows:
        return 0

    dialect = session.get_bind().dialect.name
    # SQLite умеет ON CONFLICT с 3.24, и тесты гоняются на нём же — иначе
    # проверять идемпотентность было бы негде.
    insert = pg_insert if dialect == "postgresql" else sqlite_insert

    written = 0
    for start in range(0, len(rows), CHUNK):
        chunk = [{**row, "user_id": user_id} for row in rows[start:start + CHUNK]]

        stmt = insert(HealthMetric).values(chunk)
        # Конфликт описан колонками, а не именем ограничения: `constraint=`
        # понимает только Postgres, а тесты идемпотентности гоняются на SQLite.
        # Колонки те же, что в uq_health_metric.
        stmt = stmt.on_conflict_do_update(
            index_elements=["user_id", "name", "field", "measured_at", "source"],
            set_={
                "value": stmt.excluded.value,
                "units": stmt.excluded.units,
            },
        )
        await session.execute(stmt)
        written += len(chunk)

    await session.commit()
    logging.info("метрик записано: %s", written)
    return written
