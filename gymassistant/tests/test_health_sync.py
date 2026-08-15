"""
Перенос метрик Apple Health: разбор документов и идемпотентность записи.

Разбор чистый — документы на входе, словари на выходе, ни базы, ни HTTP.
Отдельно проверяется то, ради чего заводился уникальный ключ: повторная
синхронизация не должна плодить дубли, а два устройства за один момент
не должны затирать друг друга.
"""
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

os.environ.setdefault("DB_URL", f"sqlite+aiosqlite:///{Path(tempfile.mkdtemp())}/health.db")
os.environ.setdefault("MINIAPP_BOT_TOKEN", "123456:TEST-TOKEN")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.health_sync import rows_from, to_moment, to_number  # noqa: E402


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


def field_map(rows):
    return {row["field"]: row["value"] for row in rows}


# --- разбор документов ------------------------------------------------------

def test_simple_metric_gives_one_row():
    """Шаги — одно число, одна строка."""
    docs = [{"_id": "x", "__v": 0, "date": "2026-06-19T12:00:00.000Z",
             "qty": 5200, "units": "count", "source": "Apple Watch"}]

    rows = rows_from("step_count", docs)

    assert len(rows) == 1
    assert rows[0] == {
        "name": "step_count",
        "field": "qty",
        "measured_at": datetime(2026, 6, 19, 12, 0, 0),
        "value": 5200.0,
        "units": "count",
        "source": "Apple Watch",
    }


def test_heart_rate_gives_three_rows():
    """У пульса в документе три числа — значит три строки, а не одна."""
    docs = [{"_id": "x", "__v": 0, "date": "2026-08-08T17:49:43.000Z",
             "Avg": 64, "Min": 58, "Max": 71,
             "units": "count/min", "source": "Apple Watch — Артем"}]

    rows = rows_from("heart_rate", docs)

    assert len(rows) == 3
    assert field_map(rows) == {"avg": 64.0, "min": 58.0, "max": 71.0}


def test_sleep_keeps_durations_and_drops_moments():
    """
    У сна числа — длительности фаз. sleepStart/inBedEnd тоже поля документа,
    но это МОМЕНТЫ: числом они не являются и значением метрики стать не могут.
    """
    docs = [{"_id": "x", "__v": 0, "date": "2026-08-08T17:00:00.000Z",
             "awake": 0.04, "core": 4.08, "deep": 0.86, "rem": 1.44, "inBed": 0,
             "inBedStart": "2026-08-08T19:28:38.000Z",
             "inBedEnd": "2026-08-09T01:55:09.000Z",
             "sleepStart": "2026-08-08T19:28:38.000Z",
             "sleepEnd": "2026-08-09T01:55:09.000Z",
             "units": "hr", "source": "Apple Watch — Артем"}]

    rows = rows_from("sleep_analysis", docs)

    assert field_map(rows) == {
        "awake": 0.04, "core": 4.08, "deep": 0.86, "rem": 1.44, "in_bed": 0.0,
    }


def test_service_keys_never_become_fields():
    """`_id` и `__v` — служебные. `__v` ещё и число, так что без списка исключений
    он бы стал метрикой «версия документа»."""
    docs = [{"_id": "abc", "__v": 0, "date": "2026-06-19T00:00:00.000Z", "qty": 83}]

    rows = rows_from("weight_body_mass", docs)

    assert [row["field"] for row in rows] == ["qty"]


def test_missing_measurement_is_not_zero():
    """
    Пустая строка означает «замера не было». Записать её нулём значило бы
    утверждать, что пульс покоя в этот день равнялся нулю, и утащить вниз
    любое среднее.
    """
    docs = [{"date": "2026-07-26T00:00:00.000Z", "qty": "", "units": "count/min"}]

    assert rows_from("resting_heart_rate", docs) == []


def test_document_without_date_is_dropped():
    """Без момента строку некуда положить на ось времени, и ключ уникальности неполон."""
    docs = [{"qty": 42, "units": "count"}, {"date": "мусор", "qty": 42}]

    assert rows_from("step_count", docs) == []


def test_decimal_comma():
    """Русская локаль телефона отдаёт `55,616`. Срезать дробную часть нельзя."""
    assert to_number("55,616") == pytest.approx(55.616)
    assert to_number("19.4") == pytest.approx(19.4)
    assert to_number("") is None
    assert to_number(None) is None
    assert to_number(True) is None


def test_offset_is_converted_to_utc():
    """
    Сырые пакеты несут смещение (`+0700`). Без приведения к UTC ночной замер
    уехал бы на соседние сутки и разошёлся бы с датой тренировки.
    """
    assert to_moment("2026-08-09 23:39:00 +0700") == datetime(2026, 8, 9, 16, 39, 0)
    assert to_moment("2026-06-19T00:00:00.000Z") == datetime(2026, 6, 19, 0, 0, 0)


# --- запись в базу ----------------------------------------------------------

async def prepare_db():
    """Чистые таблицы и пользователь, на которого вешаются метрики."""
    from database.engine import engine, session_maker
    from database.models import Base
    from database.orm_query import orm_add_user

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async with session_maker() as session:
        await orm_add_user(session, {"user_id": 851690283, "name": "Артем", "weight": 79.0})

    return session_maker


@pytest.mark.anyio
async def test_repeated_sync_does_not_duplicate():
    """
    Health Auto Export шлёт перекрывающиеся окна — один день приезжает при каждой
    синхронизации. Второй прогон обязан обновить строку, а не добавить вторую.
    """
    from sqlalchemy import func, select

    from database.models import HealthMetric
    from database.orm_health import orm_upsert_health_metrics

    session_maker = await prepare_db()
    docs = [{"date": "2026-06-19T00:00:00.000Z", "qty": 83, "units": "kg", "source": "iPhone"}]

    async with session_maker() as session:
        await orm_upsert_health_metrics(session, 851690283, rows_from("weight_body_mass", docs))
        await orm_upsert_health_metrics(session, 851690283, rows_from("weight_body_mass", docs))

    async with session_maker() as session:
        count = await session.scalar(select(func.count()).select_from(HealthMetric))

    assert count == 1


@pytest.mark.anyio
async def test_refined_value_overwrites():
    """Apple уточняет замеры задним числом — свежая присылка авторитетнее прошлой."""
    from sqlalchemy import select

    from database.models import HealthMetric
    from database.orm_health import orm_upsert_health_metrics

    session_maker = await prepare_db()
    moment = "2026-06-19T00:00:00.000Z"

    async with session_maker() as session:
        await orm_upsert_health_metrics(session, 851690283, rows_from(
            "step_count", [{"date": moment, "qty": 5200, "units": "count", "source": "iPhone"}]))
        await orm_upsert_health_metrics(session, 851690283, rows_from(
            "step_count", [{"date": moment, "qty": 5310, "units": "count", "source": "iPhone"}]))

    async with session_maker() as session:
        values = (await session.scalars(select(HealthMetric.value))).all()

    assert values == [5310.0]


@pytest.mark.anyio
async def test_document_without_source_still_deduplicates():
    """
    Источник входит в ключ уникальности, а NULL в ключе не склеивается сам с собой.
    Поэтому отсутствующее устройство — пустая строка: иначе документ без источника
    дублировался бы при каждой синхронизации, и заметить это было бы некому.
    """
    from sqlalchemy import func, select

    from database.models import HealthMetric
    from database.orm_health import orm_upsert_health_metrics

    session_maker = await prepare_db()
    docs = [{"date": "2026-06-19T00:00:00.000Z", "qty": 83, "units": "kg"}]

    assert rows_from("weight_body_mass", docs)[0]["source"] == ""

    async with session_maker() as session:
        await orm_upsert_health_metrics(session, 851690283, rows_from("weight_body_mass", docs))
        await orm_upsert_health_metrics(session, 851690283, rows_from("weight_body_mass", docs))

    async with session_maker() as session:
        count = await session.scalar(select(func.count()).select_from(HealthMetric))

    assert count == 1


@pytest.mark.anyio
async def test_two_devices_do_not_overwrite_each_other():
    """
    19 июня шаги пришли дважды: 3270 с телефона и 5200 с часов. Это один день,
    посчитанный двумя устройствами, — обе строки должны выжить. Поэтому source
    и входит в уникальный ключ.
    """
    from sqlalchemy import select

    from database.models import HealthMetric
    from database.orm_health import orm_upsert_health_metrics

    session_maker = await prepare_db()
    moment = "2026-06-19T12:00:00.000Z"

    async with session_maker() as session:
        await orm_upsert_health_metrics(session, 851690283, rows_from("step_count", [
            {"date": moment, "qty": 3270, "units": "count", "source": "iPhone"},
            {"date": moment, "qty": 5200, "units": "count", "source": "Apple Watch"},
        ]))

    async with session_maker() as session:
        values = sorted((await session.scalars(select(HealthMetric.value))).all())

    assert values == [3270.0, 5200.0]
