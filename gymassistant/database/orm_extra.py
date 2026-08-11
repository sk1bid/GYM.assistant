"""
Дополнения к слою данных, которых не хватало боту.

Ничего из orm_query.py здесь не переписано — только добавлено то, чего там не было:

* таймер отдыха (`RestTimer`), живущий в БД, а не в памяти процесса;
* агрегация истории по *личности* упражнения, а не по строке `Exercise`
  (см. ниже про identity — это лечит потерю рекордов при смене программы);
* настройки программы, которые раньше только читались;
* редактирование и удаление уже записанного подхода.

Импортов aiogram тут нет и быть не должно: модуль общий для бота и Mini App.
"""
from datetime import datetime, timedelta

from sqlalchemy import Float, cast, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    Exercise,
    RestTimer,
    Set,
    TrainingProgram,
    TrainingSession,
)
# utcnow живёт в едином модуле часов (services/clock.py). Реэкспортируем, чтобы не
# ломать существующие `from database.orm_extra import utcnow` (rest_notifier и др.).
from services.clock import utcnow

"""
Личность упражнения
"""


def exercise_identity(exercise: Exercise):
    """
    Условие «это то же самое упражнение, что и переданное».

    `Exercise` — это не упражнение, а *упражнение в конкретном дне конкретной
    программы*. «Жим лёжа» в двух программах — две разные строки с разными id.
    Раньше рекорды и «прошлый раз» фильтровались по `Exercise.id`, поэтому смена
    программы обнуляла всю историю. Настоящий идентификатор — ссылка на каталог:
    `admin_exercise_id` либо `user_exercise_id` (ровно одна из них не NULL,
    это гарантирует CHECK-констрейнт `check_admin_or_user_exercise`).
    """
    if exercise.admin_exercise_id is not None:
        return Exercise.admin_exercise_id == exercise.admin_exercise_id
    return Exercise.user_exercise_id == exercise.user_exercise_id


def _lifted():
    """
    Только то, что человек реально поднял.

    Пропущенный подход (`skipped`) — полноценная строка: он двигает план тренировки
    наравне с выполненным. Но в рекорды, объём, графики и «прошлый раз» ему нельзя:
    там нужен результат, а результата не было. Условие вынесено в помощник, чтобы
    не забыть его в одном месте из шести и не получить рекорд «0 кг».
    """
    return Set.skipped.is_(False)


async def orm_get_max_weight_by_identity(session: AsyncSession, user_id: int, exercise: Exercise) -> float:
    """Рекорд веса по упражнению — через все программы пользователя."""
    stmt = (
        select(func.coalesce(func.max(Set.weight), 0.0))
        .join(Exercise, Set.exercise_id == Exercise.id)
        .join(TrainingSession, Set.training_session_id == TrainingSession.id)
        .where(TrainingSession.user_id == user_id, exercise_identity(exercise), _lifted())
    )
    return float((await session.execute(stmt)).scalar() or 0.0)


async def orm_get_max_volume_by_identity(session: AsyncSession, user_id: int, exercise: Exercise) -> float:
    """Рекорд по объёму одного подхода (вес × повторения)."""
    stmt = (
        select(func.coalesce(func.max(Set.weight * Set.repetitions), 0.0))
        .join(Exercise, Set.exercise_id == Exercise.id)
        .join(TrainingSession, Set.training_session_id == TrainingSession.id)
        .where(TrainingSession.user_id == user_id, exercise_identity(exercise), _lifted())
    )
    return float((await session.execute(stmt)).scalar() or 0.0)


async def orm_get_prev_sets_by_identity(
    session: AsyncSession,
    user_id: int,
    exercise: Exercise,
    current_session_id=None,
):
    """
    Подходы этого упражнения из последней тренировки, где оно вообще делалось
    (текущая сессия исключается). Именно это показывается как «прошлый раз».
    """
    prev_session = (
        select(TrainingSession.id)
        .join(Set, TrainingSession.id == Set.training_session_id)
        .join(Exercise, Set.exercise_id == Exercise.id)
        .where(TrainingSession.user_id == user_id, exercise_identity(exercise), _lifted())
    )
    if current_session_id is not None:
        prev_session = prev_session.where(TrainingSession.id != current_session_id)

    prev_session = prev_session.order_by(TrainingSession.date.desc()).limit(1).scalar_subquery()

    stmt = (
        select(Set)
        .join(Exercise, Set.exercise_id == Exercise.id)
        .where(Set.training_session_id == prev_session, exercise_identity(exercise), _lifted())
        .order_by(Set.id)
    )
    return (await session.execute(stmt)).scalars().all()


async def orm_get_exercise_progress(session: AsyncSession, user_id: int, exercise: Exercise):
    """
    История упражнения по тренировкам — для графика прогресса.
    Одна точка на тренировку: дата, максимальный вес, суммарный тоннаж.
    """
    stmt = (
        select(
            TrainingSession.date.label("date"),
            func.max(Set.weight).label("max_weight"),
            func.sum(cast(Set.weight, Float) * Set.repetitions).label("volume"),
            func.count(Set.id).label("sets"),
        )
        .join(Set, TrainingSession.id == Set.training_session_id)
        .join(Exercise, Set.exercise_id == Exercise.id)
        .where(TrainingSession.user_id == user_id, exercise_identity(exercise), _lifted())
        .group_by(TrainingSession.id, TrainingSession.date)
        .order_by(TrainingSession.date)
    )
    return (await session.execute(stmt)).all()


"""
Тренировки и подходы
"""


async def orm_get_sets_of_session(session: AsyncSession, training_session_id):
    """Все подходы тренировки в порядке выполнения."""
    stmt = (
        select(Set)
        .where(Set.training_session_id == training_session_id)
        .order_by(Set.id)
    )
    return (await session.execute(stmt)).scalars().all()


async def orm_add_skipped_sets(session: AsyncSession, training_session_id, exercise_id: int, count: int):
    """
    Отмечает `count` подходов несделанными.

    Вес и повторения нулевые — из статистики такие строки всё равно исключены
    (`_lifted`), а нули честнее любого выдуманного числа. Порядок вставки важен:
    шаг тренировки считается по количеству строк в плановом порядке, поэтому
    пропуски должны лечь ровно на то место, где человек их поставил.
    """
    session.add_all([
        Set(
            exercise_id=exercise_id,
            weight=0,
            repetitions=0,
            training_session_id=training_session_id,
            skipped=True,
        )
        for _ in range(count)
    ])
    await session.commit()


async def orm_update_set(session: AsyncSession, set_id: int, weight: float, repetitions: int):
    """
    Правим уже записанный подход — ошибиться на степпере легко.

    Правка снимает и отметку «пропущен»: раз человек вводит вес и повторения,
    значит подход всё-таки сделан (нулевых повторений схема не принимает). Без
    этого исправленный пропуск остался бы вне рекордов и объёма — то есть правка
    как будто не сработала бы.
    """
    await session.execute(
        update(Set)
        .where(Set.id == set_id)
        .values(weight=weight, repetitions=repetitions, skipped=False)
    )
    await session.commit()


async def orm_delete_set(session: AsyncSession, set_id: int):
    await session.execute(delete(Set).where(Set.id == set_id))
    await session.commit()


async def orm_get_sessions_summary(session: AsyncSession, user_id: int, limit: int = 50, offset: int = 0):
    """
    Список тренировок пользователя со сводкой: сколько подходов, сколько тоннажа.
    Пустые сессии (тренировку начали и бросили) не показываем — они только мусорят историю.
    """
    stmt = (
        select(
            TrainingSession.id,
            TrainingSession.date,
            TrainingSession.note,
            func.count(Set.id).label("sets"),
            func.coalesce(func.sum(cast(Set.weight, Float) * Set.repetitions), 0.0).label("volume"),
            func.count(func.distinct(Set.exercise_id)).label("exercises"),
        )
        .join(Set, TrainingSession.id == Set.training_session_id)
        .where(TrainingSession.user_id == user_id, _lifted())
        .group_by(TrainingSession.id, TrainingSession.date, TrainingSession.note)
        .order_by(TrainingSession.date.desc())
        .limit(limit)
        .offset(offset)
    )
    return (await session.execute(stmt)).all()


async def orm_get_last_training_per_day(session: AsyncSession, user_id: int, since) -> dict[int, datetime]:
    """
    Для каждого дня программы — КОГДА по нему тренировались в последний раз.

    Нужно, чтобы понять, что пропущено. Сравнение идёт по дню ПРОГРАММЫ, а не по
    календарной дате: отработать понедельник во вторник — это не пропуск, а перенос,
    и напоминать о нём не надо.

    Отдаём момент, а не просто «да/нет». Раньше здесь было множество id, и день
    считался закрытым, если по нему вообще была тренировка за последние семь суток —
    БЕЗ ОГЛЯДКИ на то, случилась она раньше самого дня или позже. Отсюда вылезал
    чужой день: отработал прошлое воскресенье во вторник — и в понедельник сервер
    считал закрытым уже НАСТУПИВШЕЕ воскресенье, а «пропущенной» показывал субботу.
    Одна и та же тренировка не может закрыть два повтора дня недели подряд, поэтому
    вызывающий сверяет дату сессии с датой самого дня (`missed_day` в routers/schedule.py).

    Тренировкой считается сессия, в которой есть хотя бы один подход. Пустая — это
    «открыл и передумал»: она и так живёт до первой уборки (`orm_delete_empty_sessions`),
    и закрывать ею день недели нельзя, иначе случайное нажатие «Начать» гасило бы
    напоминание о невыполненной тренировке. Пропущенные подходы при этом считаются:
    человек прошёл по упражнениям и решил, что сегодня не идёт, — день закрыт.
    """
    has_sets = select(Set.id).where(Set.training_session_id == TrainingSession.id).exists()
    stmt = (
        select(TrainingSession.training_day_id, func.max(TrainingSession.date))
        .where(
            TrainingSession.user_id == user_id,
            TrainingSession.date >= since,
            TrainingSession.training_day_id.isnot(None),
            has_sets,
        )
        .group_by(TrainingSession.training_day_id)
    )
    return {day_id: last for day_id, last in (await session.execute(stmt)).all()}


async def orm_delete_empty_sessions(session: AsyncSession, user_id: int, older_than_hours: int = 12):
    """
    Подчищает брошенные тренировки без единого подхода.
    Раньше такие копились молча — в боте сессия создавалась в момент нажатия «Начать».
    """
    has_sets = select(Set.id).where(Set.training_session_id == TrainingSession.id).exists()
    cutoff = utcnow() - timedelta(hours=older_than_hours)
    await session.execute(
        delete(TrainingSession).where(
            TrainingSession.user_id == user_id,
            TrainingSession.date < cutoff,
            ~has_sets,
        )
    )
    await session.commit()


async def orm_start_training_session(
    session: AsyncSession,
    user_id: int,
    training_day_id: int,
    note: str | None = None,
) -> TrainingSession:
    """
    Начинает тренировку. В отличие от orm_add_training_session запоминает день,
    из которого её запустили — без этого нельзя восстановить экран после закрытия Mini App.
    """
    new = TrainingSession(
        user_id=user_id,
        training_day_id=training_day_id,
        note=note,
    )
    session.add(new)
    await session.commit()
    return new


async def orm_finish_training_session(session: AsyncSession, training_session_id):
    await session.execute(
        update(TrainingSession)
        .where(TrainingSession.id == training_session_id)
        .values(finished_at=utcnow())
    )
    await session.commit()


async def orm_get_active_session(session: AsyncSession, user_id: int, max_age_hours: int = 8):
    """
    Незавершённая тренировка пользователя, если она есть.

    Источник правды о ходе тренировки — БД, а не FSM в памяти: закрыли Mini App,
    перезапустили под — тренировка продолжается ровно с того места, где встали.
    Дальше max_age_hours считаем, что человек просто ушёл домой и не нажал «Завершить».
    """
    cutoff = utcnow() - timedelta(hours=max_age_hours)
    stmt = (
        select(TrainingSession)
        .where(
            TrainingSession.user_id == user_id,
            TrainingSession.finished_at.is_(None),
            TrainingSession.date >= cutoff,
            # Только тренировки, запущенные из Mini App. Старое меню бота тоже умеет
            # начинать тренировку и не проставляет день — такую сессию веб не сможет
            # ни отобразить, ни продолжить, и «продолжать» её не должен.
            TrainingSession.training_day_id.isnot(None),
        )
        .order_by(TrainingSession.date.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


"""
Настройки программы
"""

PROGRAM_SETTINGS = (
    "rest_between_set",
    "rest_between_exercise",
    "circular_rounds",
    "circular_rest_between_rounds",
    "circular_rest_between_exercise",
)


async def orm_update_program_settings(session: AsyncSession, program_id: int, data: dict):
    """
    Настройки отдыха и кругов. В боте они существовали, но UI для них не было —
    их нельзя было ни увидеть, ни поменять.
    """
    values = {k: data[k] for k in PROGRAM_SETTINGS if k in data}
    if "name" in data:
        values["name"] = data["name"]
    if not values:
        return
    await session.execute(
        update(TrainingProgram).where(TrainingProgram.id == program_id).values(**values)
    )
    await session.commit()


"""
Таймер отдыха
"""


async def orm_start_rest_timer(
    session: AsyncSession,
    user_id: int,
    chat_id: int,
    seconds: int,
    next_up: str | None = None,
) -> RestTimer:
    """
    Ставит (или перезаписывает) таймер отдыха. У пользователя он ровно один.
    Дальше его ведёт воркер в процессе бота — Mini App может быть уже закрыт.
    """
    now = utcnow()
    timer = (
        await session.execute(select(RestTimer).where(RestTimer.user_id == user_id))
    ).scalars().first()

    if timer is None:
        timer = RestTimer(user_id=user_id, chat_id=chat_id)
        session.add(timer)

    timer.chat_id = chat_id
    timer.ends_at = now + timedelta(seconds=seconds)
    timer.total_seconds = seconds
    timer.last_ping = None
    # message_id СОЗНАТЕЛЬНО не обнуляем: там висит прошлое сообщение таймера —
    # чаще всего «🔔 Отдых окончен!» от предыдущего подхода. last_ping=None делает
    # ближайший пинг «первым», а он перед отправкой удаляет старое message_id
    # (_delete_quietly в воркере). Так прошлое «окончено» гаснет ровно тогда, когда
    # начинается новый отдых, и в чате остаётся одно живое сообщение бота.
    timer.next_up = (next_up or "")[:150] or None
    timer.active = True

    await session.commit()
    return timer


async def orm_get_rest_timer(session: AsyncSession, user_id: int) -> RestTimer | None:
    stmt = select(RestTimer).where(RestTimer.user_id == user_id).limit(1)
    return (await session.execute(stmt)).scalars().first()


async def orm_stop_rest_timer(session: AsyncSession, user_id: int):
    """«Закончить отдых» — из бота или из Mini App, разницы нет."""
    await session.execute(
        update(RestTimer).where(RestTimer.user_id == user_id).values(active=False)
    )
    await session.commit()


async def orm_get_active_rest_timers(session: AsyncSession):
    """Все живые таймеры — воркер разбирает их сам."""
    stmt = select(RestTimer).where(RestTimer.active.is_(True))
    return (await session.execute(stmt)).scalars().all()


async def orm_save_rest_ping(session: AsyncSession, timer_id: int, message_id: int | None):
    """
    Запоминаем, что и когда отправили: следующий пинг сначала удалит это сообщение,
    а от last_ping отсчитывается следующая граница минуты (_should_ping в воркере).
    """
    await session.execute(
        update(RestTimer)
        .where(RestTimer.id == timer_id)
        .values(last_ping=utcnow(), message_id=message_id)
    )
    await session.commit()


async def orm_finish_rest_timer(session: AsyncSession, timer_id: int):
    """
    Отдых кончился. message_id НЕ трогаем: воркер только что положил туда id
    сообщения «🔔 Отдых окончен!». Пусть оно висит как единственное живое сообщение
    таймера — его уберёт либо первый пинг следующего отдыха, либо sweep по TTL, если
    тренировка на этом закончилась.
    """
    await session.execute(
        update(RestTimer).where(RestTimer.id == timer_id).values(active=False)
    )
    await session.commit()


async def orm_get_stale_rest_messages(session: AsyncSession, cutoff):
    """
    Завершённые таймеры, чьё последнее сообщение зависло в чате и его пора убрать.

    Это финальные «🔔 Отдых окончен!» после конца тренировки (нового отдыха, который
    бы их сменил, уже не будет) и осиротевшие пинги после «Закончить отдых» из веба —
    удалить сообщение в Telegram может только процесс бота, не Mini App.

    last_ping — когда отправили это сообщение; NULL означает таймер, остановленный
    ещё до единого пинга (там message_id — унаследованный мусор, тоже под снос).
    """
    stmt = select(RestTimer).where(
        RestTimer.active.is_(False),
        RestTimer.message_id.is_not(None),
        (RestTimer.last_ping < cutoff) | RestTimer.last_ping.is_(None),
    )
    return (await session.execute(stmt)).scalars().all()


async def orm_clear_rest_message(session: AsyncSession, timer_id: int):
    """Сообщение таймера удалено из чата — забываем его id, чтобы не сносить повторно."""
    await session.execute(
        update(RestTimer).where(RestTimer.id == timer_id).values(message_id=None)
    )
    await session.commit()


async def orm_get_exercises_of_days(session: AsyncSession, day_ids) -> dict[int, list[Exercise]]:
    """
    Упражнения сразу нескольких дней — ОДНИМ запросом.

    Наивный путь (звать orm_get_exercises на каждый день) стоил восьми обращений
    к постгресу на один экран: список дней плюс по запросу на каждый из семи дней
    недели. Круговые задержки складываются последовательно, и главный экран
    ощутимо «думал» перед появлением.

    Сортировка по (день, позиция) — та же, что в orm_get_exercises: порядок
    упражнений внутри дня не косметика, подряд идущие круговые собираются
    в один круговой блок.
    """
    ids = list(day_ids)
    if not ids:
        return {}

    stmt = (
        select(Exercise)
        .where(Exercise.training_day_id.in_(ids))
        .order_by(Exercise.training_day_id, Exercise.position)
    )

    grouped: dict[int, list[Exercise]] = {day_id: [] for day_id in ids}
    for exercise in (await session.execute(stmt)).scalars():
        grouped.setdefault(exercise.training_day_id, []).append(exercise)

    return grouped


async def orm_reorder_exercises(session: AsyncSession, day_id: int, ids: list[int]) -> bool:
    """
    Раскладывает упражнения дня в присланном порядке.

    Нужно взамен пошаговых move_up/move_down: перетаскивание задаёт сразу весь
    порядок, и слать его обменами соседей значило бы N запросов на одно движение
    пальца — да ещё и с промежуточными состояниями, каждое из которых сервер считал
    бы настоящей программой.

    Список обязан совпадать с днём ПОЛНОСТЬЮ, а не быть его частью: неполный порядок
    оставил бы часть упражнений с прежними позициями, и они перемешались бы
    с новыми. Не совпал — не трогаем ничего, порядок здесь определяет структуру
    тренировки (подряд идущие круговые собираются в один круг).
    """
    stmt = select(Exercise).where(Exercise.training_day_id == day_id)
    exercises = {e.id: e for e in (await session.execute(stmt)).scalars()}

    if set(ids) != set(exercises) or len(ids) != len(exercises):
        return False

    for position, exercise_id in enumerate(ids):
        exercises[exercise_id].position = position

    await session.commit()
    return True
