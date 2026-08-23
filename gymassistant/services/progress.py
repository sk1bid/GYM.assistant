"""
Неделя программы: что запланировано, что пропущено, как идёт серия.

Вынесено из `miniapp/routers/schedule.py`, потому что об этом же спрашивает воркер
напоминаний — а он живёт в процессе БОТА. Импортировать оттуда роутер нельзя:
`miniapp.config` требует `MINIAPP_BOT_TOKEN`, которого у бота нет, и первый же
импорт уронил бы его на старте. Плюс FastAPI ради двух функций, считающих даты.

Модуль работает с объектами моделей, а не с готовым JSON: сериализация — забота
Mini App, воркеру от неё нужны только числа. Роутер разворачивает результат
в свои словари сам.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Sequence
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Exercise, TrainingDay
from database.orm_extra import (
    orm_get_exercises_of_days,
    orm_get_last_training_per_day,
    orm_get_sessions_summary,
    utcnow,
)
from database.orm_query import orm_get_training_days
from services.clock import today_in

# Дни недели по-русски в календарном порядке. Живут здесь, а не в miniapp/config.py,
# по той же причине, по которой сюда переехали функции: тот модуль тянет за собой
# токен бота. config.py их реэкспортирует, чтобы не править десяток импортов.
WEEK_DAYS_RU = [
    "Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье",
]


@dataclass(frozen=True)
class PlannedDay:
    """День программы вместе с его упражнениями."""
    day: TrainingDay
    exercises: list[Exercise]

    @property
    def name(self) -> str:
        return self.day.day_of_week

    @property
    def weekday(self) -> int | None:
        """Номер дня недели (0 — понедельник) или None, если имя нераспознаваемое."""
        return _ORDER.get(self.day.day_of_week.strip().lower())


_ORDER = {name.lower(): index for index, name in enumerate(WEEK_DAYS_RU)}


async def program_week(session: AsyncSession, program_id: int) -> list[PlannedDay]:
    """
    Дни программы в порядке Пн→Вс — в БД они лежат в порядке вставки.

    Упражнения всех семи дней забираются ОДНИМ запросом. Раньше здесь был вызов
    orm_get_exercises внутри цикла: семь последовательных обращений к постгресу
    на каждое открытие главной и расписания, и их задержки складывались — экран
    заметно «думал» перед появлением.
    """
    days = await orm_get_training_days(session, program_id)
    by_name = {d.day_of_week.strip().lower(): d for d in days}

    ordered = [by_name[name.lower()] for name in WEEK_DAYS_RU if name.lower() in by_name]
    exercises = await orm_get_exercises_of_days(session, [d.id for d in ordered])

    return [PlannedDay(day=day, exercises=list(exercises.get(day.id, []))) for day in ordered]


def day_of(week: Sequence[PlannedDay], name: str) -> PlannedDay | None:
    """День недели по имени, с нормализацией: в базе оно лежит как ввёл пользователь."""
    target = name.strip().lower()
    return next((d for d in week if d.day.day_of_week.strip().lower() == target), None)


async def missed_day(
    session: AsyncSession, user_id: int, tz: ZoneInfo, week: Sequence[PlannedDay]
) -> dict | None:
    """
    Последний пропущенный тренировочный день, если он есть.

    Пропуск — это день программы с упражнениями, чей день недели уже прошёл, а
    тренировки по нему за последнюю неделю не было. Отдаём ОДИН день, самый свежий,
    а не список: пять строк «пропущено» на главной были бы упрёком, а не помощью.

    Три границы, каждая по своей причине.

    **Сравниваем дни ПРОГРАММЫ, а не даты.** Отработал понедельник во вторник —
    это перенос, и в среду напоминать не о чем: сессия помнит `training_day_id`,
    и день считается закрытым независимо от того, какого числа его закрыли.

    **Тренировка обязана быть НЕ РАНЬШЕ самого дня.** День недели повторяется, а
    тренировка по нему закрывает ровно один его повтор — тот, что уже наступил к её
    моменту. Сначала здесь стояло «была ли вообще сессия по этому дню за семь суток»,
    и одна тренировка гасила сразу два повтора: отработал прошлое воскресенье во
    вторник — и в понедельник сервер считал закрытым уже наступившее воскресенье,
    а пропущенной показывал субботу, то есть день ПОЗАДИ ближайшего. Отсюда сравнение
    даты сессии (в поясе клиента, дата хранится naive-UTC) с датой самого дня.

    **Свежесть — неделя.** Окно на выборке тренировок: то, что делалось больше семи
    суток назад, день уже не закрывает. После сравнения дат это ещё и дешёвый
    предфильтр — дальше семи суток назад ни один искомый день всё равно не лежит.

    **Ищем шесть дней назад, а не семь.** Седьмой — это тот же день недели, что
    сегодня, то есть СЕГОДНЯШНЯЯ тренировка. Предлагать «отработать» то, что и так
    стоит в плане на сегодня, бессмысленно: экран показал бы один и тот же день
    дважды, и обе кнопки открыли бы одну и ту же тренировку. Заодно это и есть
    правило «пропущенное должно идти строго ПЕРЕД тем, что будет»: при поиске на
    шесть дней у дня всегда остаётся хотя бы сутки до его собственного повтора.
    """
    planned = {
        d.day.day_of_week.strip().lower(): d
        for d in week
        if d.exercises
    }
    if not planned:
        return None

    today = today_in(tz)
    last_trained = await orm_get_last_training_per_day(
        session, user_id, utcnow() - timedelta(days=7)
    )

    for back in range(1, 7):
        past = today - timedelta(days=back)
        day = planned.get(WEEK_DAYS_RU[past.weekday()].lower())
        if not day:
            continue

        last = last_trained.get(day.day.id)
        closed = last is not None and last.replace(tzinfo=timezone.utc).astimezone(tz).date() >= past
        if not closed:
            return {"id": day.day.id, "day_of_week": day.day.day_of_week, "days_ago": back}

    return None


async def weekly_progress(
    session: AsyncSession,
    user_id: int,
    tz: ZoneInfo,
    week: Sequence[PlannedDay],
    trained: set[date] | None = None,
) -> dict:
    """
    Прогресс недели и серия — мотивационная сводка для главного экрана.

    «Сделано» — сколько РАЗНЫХ дней текущей календарной недели (Пн→сегодня) были
    тренировочными; «цель» — сколько тренировочных дней в программе. Серия — сколько
    недель подряд цель была ЗАКРЫТА.

    `left` — сколько тренировочных дней ПРОГРАММЫ на этой неделе ещё впереди
    (сегодняшний считается, пока не отработан). Без него экран обещал невозможное:
    в пятницу при цели 4 и нуле сделанных он писал «ещё 4 тренировки», хотя до
    воскресенья таких дней в программе оставалось разве что один. Разница между
    «сколько не хватает до цели» и «сколько ещё физически влезет» — и есть повод
    считать это на сервере, а не вычитать на клиенте.

    **Неделя входит в серию, только если закрыта целиком** (`done >= goal`). Раньше
    хватало одной тренировки за неделю — и серия росла месяцами у человека, который
    из четырёх запланированных дней делал один. Число в карточке обязано означать
    то, чем его называют: «серия» рядом с кольцом 0/4 — это про закрытые кольца,
    а не про «хоть раз зашёл в зал».

    В какие именно дни недели тренировались — не важно: цель задана числом дней,
    а не расписанием. Отработал субботнюю программу в среду — неделя всё равно
    закрыта, для этого `done` и считает РАЗНЫЕ ДАТЫ, а не совпадения с днями недели.

    Серия при этом мягкая: текущая неделя не рвёт её, пока не кончилась. Если цель
    этой недели ещё не закрыта, отсчёт начинается с прошлой (грейс) — иначе серия
    обнулялась бы каждый понедельник. Так же устроены недельные серии у Apple
    Fitness+ и цель «N дней в неделю» у Fitbit: неделя календарная, Пн→Вс, а не
    скользящее окно.

    Оговорка, которую стоит помнить: `goal` берётся из ТЕКУЩЕЙ программы, истории
    её правок мы не храним. Сменив программу с трёх дней на пять, пользователь
    пересудит и прошлые недели по новой мерке. Чинится только версионированием
    программы — до тех пор это осознанное упрощение, а не недосмотр.

    Дата тренировки хранится в naive-UTC, а неделя раскладывается по КАЛЕНДАРЮ
    пользователя: тренировка в 6 утра по Новосибирску — это 23:00 UTC накануне,
    и без перевода в пояс часть тренировок села бы в соседнюю неделю.
    """
    # Готовые даты принимает воркер напоминаний: он про них уже спрашивал, чтобы
    # понять, тренировался ли человек сегодня и как давно был в зале.
    if trained is None:
        trained = await trained_dates(session, user_id, tz)

    today = today_in(tz)
    week_start = today - timedelta(days=today.weekday())
    this_week = {day for day in trained if week_start <= day <= today}

    # Дни недели программы, в которых есть упражнения.
    planned = {d.weekday for d in week if d.exercises and d.weekday is not None}

    left = sum(1 for weekday in planned if weekday > today.weekday())
    if today.weekday() in planned and today not in this_week:
        left += 1

    by_week: dict[date, set[date]] = {}
    for day in trained:
        by_week.setdefault(day - timedelta(days=day.weekday()), set()).add(day)

    goal = len(planned)

    def closed(monday: date) -> bool:
        # goal == 0 — в программе нет тренировочных дней, закрывать нечего. Без этой
        # проверки любая неделя оказалась бы «закрытой» и серия росла бы из ничего.
        return bool(goal) and len(by_week.get(monday, ())) >= goal

    anchor = week_start if closed(week_start) else week_start - timedelta(days=7)
    streak = 0
    while closed(anchor):
        streak += 1
        anchor -= timedelta(days=7)

    return {"done": len(this_week), "goal": goal, "streak": streak, "left": left}


async def trained_dates(session: AsyncSession, user_id: int, tz: ZoneInfo) -> set[date]:
    """
    Календарные даты тренировок в поясе пользователя.

    Отдельной функцией, потому что об этом же спрашивает воркер напоминаний: «когда
    человек был в зале в последний раз» и «тренировался ли он сегодня» — это те же
    даты, посчитанные один раз.
    """
    rows = await orm_get_sessions_summary(session, user_id, limit=400)
    return {
        row.date.replace(tzinfo=timezone.utc).astimezone(tz).date()
        for row in rows
        if row.date
    }




async def usual_start_minutes(session: AsyncSession, user_id: int, tz: ZoneInfo, limit: int = 20) -> int | None:
    """
    Во сколько человек ОБЫЧНО начинает тренировку — минуты от полуночи по его месту.

    Нужна одному экрану: настройкам напоминаний, чтобы предложить время, а не
    заставлять вспоминать его самому. Медиана, а не среднее: одна тренировка,
    начатая в семь утра перед самолётом, сдвинула бы среднее на полчаса, а
    медиану — никуда.

    Меньше трёх тренировок — молчим. Подсказка, построенная на одном случае, хуже
    её отсутствия: она выглядит как знание, которым мы не располагаем.

    Через полночь медиана не работает (23:50 и 00:10 усреднятся в полдень), и это
    оставлено как есть: тренировок, размазанных по полуночи, не бывает.
    """
    rows = await orm_get_sessions_summary(session, user_id, limit=limit)
    started = [local_time(row.date, tz) for row in rows if row.date]
    minutes = sorted(moment.hour * 60 + moment.minute for moment in started)
    if len(minutes) < 3:
        return None
    return minutes[len(minutes) // 2]


def local_time(moment: datetime, tz: ZoneInfo) -> datetime:
    """Naive-UTC момент из базы → то же время в поясе пользователя."""
    return moment.replace(tzinfo=timezone.utc).astimezone(tz)
