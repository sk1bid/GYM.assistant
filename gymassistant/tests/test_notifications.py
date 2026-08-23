"""
Тесты напоминаний.

Разделены ровно так же, как разделён сам код: решения проверяются без базы и без
Telegram (services/notifications.py — чистый модуль), а воркер отвечает только за
«застолбил → отправил → убрал», и с него спрашивается именно это.

Что здесь зафиксировано и почему это важно:

* окно напоминания о тренировке шириной в три часа при тике раз в минуту даёт
  ОДНО сообщение — за это отвечает уникальный ключ в БД, а не память процесса;
* напоминание о тренировке проходит мимо тихих часов, всё остальное — ждёт;
* «тихих» уведомлений не больше одного в сутки, и приоритет у того, что ещё можно
  исправить;
* убирается сообщение по двум поводам: срок ИЛИ отпавший повод.
"""
import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

_TMP_DB = Path(tempfile.mkdtemp()) / "notify.db"
os.environ.setdefault("DB_URL", f"sqlite+aiosqlite:///{_TMP_DB}")
os.environ.setdefault("MINIAPP_BOT_TOKEN", "123:TEST")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database.engine import create_db, engine, session_maker  # noqa: E402
from database.models import Base, TrainingSession  # noqa: E402
from database.orm_notify import (  # noqa: E402
    orm_attach_message,
    orm_claim_notification,
    orm_get_prefs,
    orm_live_notifications,
    orm_save_prefs,
    orm_users_to_notify,
)
from database.orm_query import orm_add_user  # noqa: E402
from services.clock import utcnow  # noqa: E402
from services.notifications import (  # noqa: E402
    COMEBACK,
    DAY,
    IDLE_MINUTES,
    MISSED,
    STREAK,
    UNFINISHED,
    WEEK,
    Active,
    DayPlan,
    Facts,
    Missed,
    Prefs,
    WeekProgress,
    plan,
    prefs_of,
)

TZ = ZoneInfo("Asia/Novosibirsk")

MONDAY = date(2026, 8, 17)
SUNDAY = date(2026, 8, 23)
USER_ID = 999_000_222


def moment(day: date, hour: int, minute: int = 0) -> datetime:
    """Время в поясе пользователя — именно в нём рассуждает весь модуль."""
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)


def facts(**kwargs) -> Facts:
    """
    Факты с разумной серединой: будний тренировочный день, ничего не сделано.

    Так каждый тест правит ровно то, о чём он, — и видно, что именно он проверяет.
    """
    base = {
        "now": moment(MONDAY, 7),
        "prefs": Prefs(),
        "today": DayPlan(name="Понедельник", exercises=5, sets=15),
        "trained_today": False,
        "active": None,
        "week": WeekProgress(done=0, goal=4, left=4, streak=0),
        "missed": None,
        "days_off": 2,
        "last_training": MONDAY - timedelta(days=2),
        "ambient_today": False,
    }
    base.update(kwargs)
    return Facts(**base)


def kinds(pushes) -> list[str]:
    return [push.kind for push in pushes]


# ---------------------------------------------------------------- день тренировки


def test_day_reminder_fires_across_the_whole_window():
    """
    Окно, а не момент: воркер просыпается раз в минуту и может пролежать
    перезапуск пода. По умолчанию тренировка в 10:00 с напоминанием за три часа —
    значит окно 07:00…10:00, и сообщение положено в любой его точке.
    """
    for hour in (7, 8, 9, 10):
        assert kinds(plan(facts(now=moment(MONDAY, hour)))) == [DAY], hour


def test_day_reminder_is_silent_outside_the_window():
    """
    До окна — рано, сильно после — уже незачем: «сегодня тренировка», присланное
    вечером, ничего не меняет. Четверть часа после назначенного времени оставлены
    намеренно, на случай лежавшего воркера.
    """
    assert DAY not in kinds(plan(facts(now=moment(MONDAY, 6, 59))))
    assert DAY in kinds(plan(facts(now=moment(MONDAY, 10, 15))))
    assert DAY not in kinds(plan(facts(now=moment(MONDAY, 10, 16))))


def test_day_reminder_ignores_quiet_hours():
    """
    Единственное уведомление, которое проходит мимо ночной тишины.

    Время назначил сам человек: тренировка в 6 утра с напоминанием за три часа —
    это 3:00, и молча сдвинуть его значило бы сломать будильник. Проверяем, что
    именно оно приходит и что остальное в этот час молчит.
    """
    prefs = Prefs(train_at_minutes=6 * 60, lead_minutes=180)
    night = facts(
        now=moment(MONDAY, 3),
        prefs=prefs,
        missed=Missed(day_id=1, name="Воскресенье", days_ago=1),
    )
    assert kinds(plan(night)) == [DAY]


def test_day_reminder_is_silent_when_there_is_nothing_to_remind_about():
    """День отдыха, уже отработанный день и идущая прямо сейчас тренировка."""
    assert DAY not in kinds(plan(facts(today=None)))
    assert DAY not in kinds(plan(facts(trained_today=True)))
    assert DAY not in kinds(plan(facts(active=Active(session_id="s", idle=5, sets=3))))


def test_day_reminder_does_not_leak_into_yesterday():
    """
    Начало окна упирается в полночь.

    Тренировка в час ночи с напоминанием за три часа дала бы 22:00 ПРЕДЫДУЩЕГО
    дня — а сообщение «сегодня тренировка», отправленное вчера, врёт словом
    «сегодня». Поэтому окно начинается в 00:00.
    """
    prefs = Prefs(train_at_minutes=60, lead_minutes=180)
    assert kinds(plan(facts(now=moment(MONDAY, 0, 1), prefs=prefs))) == [DAY]
    assert DAY not in kinds(plan(facts(now=moment(MONDAY, 22), prefs=prefs)))


# ---------------------------------------------------------------- брошенная тренировка


def test_unfinished_waits_out_the_silence():
    """Полтора часа без единого подхода — это уже не «отдыхаю между подходами»."""
    def with_idle(minutes):
        return facts(
            now=moment(MONDAY, 14),
            active=Active(session_id="s", idle=minutes, sets=6),
        )

    assert UNFINISHED not in kinds(plan(with_idle(IDLE_MINUTES - 1)))
    assert UNFINISHED in kinds(plan(with_idle(IDLE_MINUTES)))


def test_unfinished_stops_asking_about_ancient_sessions():
    """
    Через сутки это уже не «забыл нажать „Завершить“», а осадок в базе. Вопрос
    «ты ещё в зале?» про позавчерашнюю тренировку читался бы как поломка.
    """
    from services.notifications import UNFINISHED_MAX_IDLE

    def with_idle(minutes):
        return facts(now=moment(MONDAY, 14),
                     active=Active(session_id="s", idle=minutes, sets=6))

    assert UNFINISHED in kinds(plan(with_idle(UNFINISHED_MAX_IDLE)))
    assert UNFINISHED not in kinds(plan(with_idle(UNFINISHED_MAX_IDLE + 1)))


def test_unfinished_ignores_an_empty_session():
    """
    Тренировка без единого подхода — это промах по кнопке «Начать».

    Её и так уберёт orm_delete_empty_sessions; спрашивать про неё значило бы
    присылать пуш за случайный тап.
    """
    empty = facts(now=moment(MONDAY, 14), active=Active(session_id="s", idle=200, sets=0))
    assert kinds(plan(empty)) == []


def test_unfinished_is_keyed_to_the_session():
    """
    Ключ повтора — сама тренировка, а не сутки: спрашиваем один раз за неё.
    Иначе брошенная в полночь тренировка спрашивала бы и назавтра.
    """
    pushes = plan(facts(now=moment(MONDAY, 14), active=Active(session_id="abc", idle=120, sets=6)))
    assert [p.dedup for p in pushes] == ["abc"]


# ---------------------------------------------------------------- тихие


def test_quiet_hours_hold_everything_but_the_day_reminder():
    """
    Ночью не пишем. Уведомление при этом не отменяется, а ждёт: условие проверяется
    каждую минуту и сработает утром — поэтому тест смотрит на обе стороны границы.
    """
    night = facts(now=moment(MONDAY, 23), missed=Missed(day_id=1, name="Воскресенье", days_ago=1),
                  today=None)
    assert kinds(plan(night)) == []

    morning = facts(now=moment(MONDAY, 19), missed=Missed(day_id=1, name="Воскресенье", days_ago=1),
                    today=None)
    assert kinds(plan(morning)) == [MISSED]


def test_only_one_ambient_per_day():
    """
    В воскресенье вечером сходятся сразу трое: пропущенный день, итог недели и
    (если не повезло) серия. Без ограничения человек получил бы три сообщения
    подряд, и это ровно то, что называется спамом.
    """
    crowded = dict(
        now=moment(SUNDAY, 20),
        today=None,
        week=WeekProgress(done=2, goal=4, left=0, streak=3),
        missed=Missed(day_id=1, name="Четверг", days_ago=3),
        days_off=12,
        last_training=SUNDAY - timedelta(days=12),
    )
    assert len(plan(facts(**crowded))) == 1
    assert plan(facts(**crowded, ambient_today=True)) == []


def test_ambient_is_silent_during_a_training():
    """Человек в зале — любое из четырёх сообщений там неуместно."""
    training = facts(
        now=moment(SUNDAY, 20),
        today=None,
        active=Active(session_id="s", idle=3, sets=4),
        week=WeekProgress(done=2, goal=4, left=0, streak=3),
    )
    assert WEEK not in kinds(plan(training))


# ---------------------------------------------------------------- серия


FRIDAY = MONDAY + timedelta(days=4)


def test_streak_warning_does_not_fire_at_the_start_of_the_week():
    """
    Самая дорогая грабля этого модуля.

    «Запаса не осталось» (`left == goal - done`) выполняется САМО СОБОЙ: цель
    недели и есть число тренировочных дней программы, поэтому в понедельник при
    нуле сделанных запаса формально нет. Первая версия правила состояла только из
    этого условия — и предупреждала бы о серии каждый понедельник в полдень, то
    есть ровно тогда, когда ничего ещё не случилось.
    """
    monday = facts(now=moment(MONDAY, 13), today=None,
                   week=WeekProgress(done=0, goal=4, left=4, streak=5))
    assert STREAK not in kinds(plan(monday))


def test_streak_warning_fires_when_every_remaining_day_counts():
    """Пятница, сделано два из четырёх, впереди ровно два — каждый обязателен."""
    tight = facts(now=moment(FRIDAY, 13), today=None,
                  week=WeekProgress(done=2, goal=4, left=2, streak=5))
    assert kinds(plan(tight)) == [STREAK]


def test_streak_warning_is_silent_when_the_week_is_already_lost():
    """
    Пропущено больше, чем осталось впереди: неделя не закрывается, спасать нечего.
    Звать в зал «ради серии», которой уже не будет, — обман.
    """
    lost = facts(now=moment(FRIDAY, 13), today=None,
                 week=WeekProgress(done=0, goal=4, left=2, streak=5))
    assert STREAK not in kinds(plan(lost))


def test_streak_warning_needs_a_streak_to_protect():
    """«Серия 0 недель прервётся» не значит ничего — молчим."""
    nothing_to_lose = facts(now=moment(FRIDAY, 13), today=None,
                            week=WeekProgress(done=2, goal=4, left=2, streak=0))
    assert STREAK not in kinds(plan(nothing_to_lose))


def test_streak_beats_the_missed_day():
    """
    Приоритет — по тому, можно ли ещё что-то сделать. Серию спасают сегодня,
    пропущенный день отрабатывают когда угодно.
    """
    both = facts(
        now=moment(FRIDAY, 19),
        today=None,
        week=WeekProgress(done=2, goal=4, left=2, streak=5),
        missed=Missed(day_id=1, name="Четверг", days_ago=1),
    )
    assert kinds(plan(both)) == [STREAK]


# ---------------------------------------------------------------- пропуск и возвращение


def test_missed_day_is_keyed_to_the_occurrence_not_to_today():
    """
    Ключ повтора — день программы плюс дата, на которую он выпал.

    Иначе один и тот же несделанный вторник напоминал бы о себе каждый вечер
    до конца недели: `missed_day` продолжает его показывать, а «раз в сутки»
    ключом по сегодняшней дате разрешило бы новое сообщение.
    """
    wednesday = MONDAY + timedelta(days=2)
    thursday = MONDAY + timedelta(days=3)
    missed = Missed(day_id=7, name="Вторник", days_ago=1)

    first = plan(facts(now=moment(wednesday, 19), today=None, missed=missed))[0]
    later = plan(facts(
        now=moment(thursday, 19), today=None,
        missed=Missed(day_id=7, name="Вторник", days_ago=2),
    ))[0]

    assert first.kind == MISSED and first.dedup == later.dedup
    assert first.dedup.endswith(str(MONDAY + timedelta(days=1)))


def test_comeback_is_sent_once_per_pause():
    """
    Ключ — дата последней тренировки. Пока она не изменилась, пауза та же самая,
    и второго приглашения не будет ни через месяц, ни через полгода. Именно это
    отличает напоминание от преследования.
    """
    gone = date(2026, 8, 1)
    early = plan(facts(now=moment(MONDAY, 18), today=None, days_off=16, last_training=gone))
    late = plan(facts(now=moment(MONDAY + timedelta(days=40), 18), today=None,
                      days_off=56, last_training=gone))

    assert kinds(early) == [COMEBACK]
    assert early[0].dedup == late[0].dedup == gone.isoformat()


def test_comeback_needs_a_pause_and_a_history():
    """Кто ещё ни разу не приходил, того и звать обратно не из чего."""
    fresh = facts(now=moment(MONDAY, 18), today=None, days_off=3,
                  last_training=MONDAY - timedelta(days=3))
    assert COMEBACK not in kinds(plan(fresh))

    never = facts(now=moment(MONDAY, 18), today=None, days_off=None, last_training=None)
    assert COMEBACK not in kinds(plan(never))


# ---------------------------------------------------------------- итог недели


def test_week_summary_is_a_sunday_evening_thing():
    assert WEEK not in kinds(plan(facts(now=moment(SUNDAY, 19), today=None,
                                        week=WeekProgress(done=4, goal=4, left=0, streak=3))))
    assert kinds(plan(facts(now=moment(SUNDAY, 20), today=None,
                            week=WeekProgress(done=4, goal=4, left=0, streak=3)))) == [WEEK]
    assert WEEK not in kinds(plan(facts(now=moment(MONDAY, 20), today=None,
                                        week=WeekProgress(done=4, goal=4, left=0, streak=3))))


def test_week_summary_says_nothing_about_an_empty_week():
    """
    Сводка «0 из 4» — это упрёк, а не сводка. Такого человека забирает
    приглашение вернуться, у которого хотя бы есть предложение.
    """
    empty = facts(now=moment(SUNDAY, 20), today=None,
                  week=WeekProgress(done=0, goal=4, left=0, streak=0),
                  days_off=12, last_training=SUNDAY - timedelta(days=12))
    assert kinds(plan(empty)) == [COMEBACK]


def test_closed_week_names_the_streak():
    closed = plan(facts(now=moment(SUNDAY, 20), today=None,
                        week=WeekProgress(done=4, goal=4, left=0, streak=7)))
    assert "7 недель" in closed[0].text


# ---------------------------------------------------------------- рубильники


def test_disabled_prefs_silence_everything():
    assert plan(facts(prefs=Prefs(enabled=False))) == []


def test_each_switch_silences_only_its_own():
    assert DAY not in kinds(plan(facts(prefs=Prefs(day_reminder=False))))

    idle = dict(now=moment(MONDAY, 14), active=Active(session_id="s", idle=200, sets=6))
    assert UNFINISHED not in kinds(plan(facts(prefs=Prefs(unfinished=False), **idle)))

    evening = dict(now=moment(MONDAY, 19), today=None,
                   missed=Missed(day_id=1, name="Воскресенье", days_ago=1))
    assert MISSED not in kinds(plan(facts(prefs=Prefs(missed=False), **evening)))


# ---------------------------------------------------------------- слой данных


@pytest.fixture
async def db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await create_db()
    async with session_maker() as session:
        await orm_add_user(session, {"user_id": USER_ID, "name": "Тестер", "weight": 75.0})
        yield session


async def test_claim_is_granted_exactly_once(db):
    """
    То, ради чего в таблице стоит уникальный ключ.

    Воркер просыпается раз в минуту, а окно напоминания — три часа. Без этого
    ключа человек получил бы 180 одинаковых сообщений; и заметьте, что память
    процесса тут не помогла бы — под перезапускается вместе с ней.
    """
    first = await orm_claim_notification(db, USER_ID, DAY, "2026-08-17", USER_ID)
    second = await orm_claim_notification(db, USER_ID, DAY, "2026-08-17", USER_ID)

    assert first is not None
    assert second is None

    # Другой день — другое право.
    assert await orm_claim_notification(db, USER_ID, DAY, "2026-08-18", USER_ID) is not None


async def test_prefs_fall_back_to_defaults_until_saved(db):
    """
    Строка настроек заводится лениво. До первого сохранения читаются те же числа,
    что зашиты в services/notifications.py, — иначе «я ничего не менял» означало бы
    разное поведение до и после первого захода в настройки.
    """
    assert await orm_get_prefs(db, USER_ID) is None
    assert prefs_of(None) == Prefs()

    await orm_save_prefs(db, USER_ID, {"train_at_minutes": 18 * 60 + 30, "weekly": False})
    saved = prefs_of(await orm_get_prefs(db, USER_ID))

    assert saved.train_at_minutes == 1110
    assert saved.weekly is False
    # Нетронутое осталось на месте, а не обнулилось.
    assert saved.lead_minutes == Prefs().lead_minutes and saved.enabled is True


async def test_user_without_a_program_is_not_polled(db):
    """
    Без активной программы нет ни дня тренировки, ни цели недели, ни пропусков —
    то есть ни одного повода написать. Выключившие уведомления отсеиваются там же,
    в SQL: тащить их строки в Python незачем.
    """
    from database.orm_query import orm_update_user

    assert await orm_users_to_notify(db) == []

    from sqlalchemy import update

    from database.models import User
    await db.execute(update(User).where(User.user_id == USER_ID).values(actual_program_id=1))
    await db.commit()
    assert [u.user_id for u in await orm_users_to_notify(db)] == [USER_ID]

    await orm_save_prefs(db, USER_ID, {"enabled": False})
    assert await orm_users_to_notify(db) == []

    assert orm_update_user is not None  # импорт держим ради читаемости соседних тестов


# ---------------------------------------------------------------- уборка


class FakeBot:
    """Записывает, что бот попытался сделать, вместо похода в Telegram."""

    def __init__(self):
        self.deleted: list[int] = []

    async def delete_message(self, chat_id, message_id):
        self.deleted.append(message_id)


async def _hang(db, kind: str, dedup: str, message_id: int, expires_in_minutes: int, sent_ago=0):
    """Кладёт в чат сообщение, которое кто-то должен убрать."""
    from sqlalchemy import update

    from database.models import Notification

    row = await orm_claim_notification(db, USER_ID, kind, dedup, USER_ID)
    await orm_attach_message(db, row.id, message_id, utcnow() + timedelta(minutes=expires_in_minutes))
    if sent_ago:
        await db.execute(
            update(Notification)
            .where(Notification.id == row.id)
            .values(sent_at=utcnow() - timedelta(minutes=sent_ago))
        )
        await db.commit()
    return row


async def test_sweep_removes_the_day_reminder_once_training_started(db):
    """
    Главный повод уборки — не срок, а отпавший повод. Напоминание «сегодня
    тренировка» живёт до конца суток, но висеть над уже идущей тренировкой ему
    незачем.
    """
    from workers.notifier import _sweep

    await _hang(db, DAY, "2026-08-17", message_id=501, expires_in_minutes=600, sent_ago=60)

    bot = FakeBot()
    await _sweep(bot, db)
    assert bot.deleted == [], "повод ещё не отпал — сообщение на месте"

    db.add(TrainingSession(user_id=USER_ID, date=utcnow()))
    await db.commit()

    await _sweep(bot, db)
    assert bot.deleted == [501]
    # Второй раз то же сообщение не сносим: message_id забыт.
    await _sweep(bot, db)
    assert bot.deleted == [501]


async def test_sweep_removes_by_deadline(db):
    """
    Срок — страховка на случай, когда повод так и не отпал. Итог недели никто не
    «выполняет», и убрать его можно только по времени.
    """
    from workers.notifier import _sweep

    await _hang(db, WEEK, "2026-08-17", message_id=502, expires_in_minutes=-1)

    bot = FakeBot()
    await _sweep(bot, db)
    assert bot.deleted == [502]
    assert await orm_live_notifications(db) == []


# ---------------------------------------------------------------- воркер целиком


class FakeSender(FakeBot):
    """Ещё и отправляет: воркер должен получить назад message_id."""

    def __init__(self):
        super().__init__()
        self.sent: list[dict] = []
        self._next_id = 700

    async def send_message(self, chat_id, text, reply_markup=None):
        self._next_id += 1
        self.sent.append({"chat_id": chat_id, "text": text, "message_id": self._next_id})

        class Sent:
            message_id = self._next_id

        return Sent()


async def _program_with_today(db, tz_name: str):
    """
    Программа, у которой тренировочный день — сегодняшний по месту пользователя.

    Именно по месту: сервер живёт в UTC, и «сегодня» у него бывает вчерашним.
    """
    from sqlalchemy import select, update

    from database.models import (
        AdminExercises, Exercise, ExerciseCategory, TrainingDay, TrainingProgram, User,
    )
    from services.clock import today_in
    from services.progress import WEEK_DAYS_RU

    category = (await db.execute(select(ExerciseCategory).limit(1))).scalars().first()
    preset = AdminExercises(
        category_id=category.id, name="Жим лёжа (тест)", description="", equipment="barbell"
    )
    db.add(preset)
    await db.commit()

    program = TrainingProgram(name="Тестовая", user_id=USER_ID)
    db.add(program)
    await db.commit()

    day = TrainingDay(
        training_program_id=program.id,
        day_of_week=WEEK_DAYS_RU[today_in(ZoneInfo(tz_name)).weekday()],
    )
    db.add(day)
    await db.commit()

    db.add(Exercise(
        training_day_id=day.id, name=preset.name, description="", position=0,
        admin_exercise_id=preset.id, base_sets=3, base_reps=10, equipment="barbell",
    ))
    await db.execute(
        update(User).where(User.user_id == USER_ID)
        .values(actual_program_id=program.id, timezone=tz_name)
    )
    await db.commit()
    return day


async def test_worker_sends_the_day_reminder_exactly_once(db):
    """
    Сквозная проверка: факты из базы → решение → отправка → запись.

    Второй прогон обязан промолчать. Это и есть весь смысл журнала: воркер
    просыпается раз в минуту, и без него человек получил бы сообщение на каждом
    тике окна.
    """
    from database.orm_notify import orm_save_prefs
    from services.clock import now_in
    from workers.notifier import _handle_user

    await _program_with_today(db, TZ.key)

    # Окно «прямо сейчас»: тренировка в текущую минуту, напоминание без запаса.
    # Так тест не зависит от того, в котором часу его запустили.
    now = now_in(TZ)
    await orm_save_prefs(db, USER_ID, {
        "train_at_minutes": now.hour * 60 + now.minute,
        "lead_minutes": 0,
    })

    target = (await orm_users_to_notify(db))[0]

    bot = FakeSender()
    await _handle_user(bot, db, target)
    assert len(bot.sent) == 1
    assert "тренировка" in bot.sent[0]["text"]
    assert bot.sent[0]["chat_id"] == USER_ID

    await _handle_user(bot, db, target)
    assert len(bot.sent) == 1, "второй тик обязан промолчать"

    # Отправленное записано вместе с тем, что потом удалять.
    live = await orm_live_notifications(db)
    assert [(row.kind, row.message_id) for row in live] == [(DAY, bot.sent[0]["message_id"])]


async def test_worker_says_nothing_on_a_rest_day(db):
    """
    День отдыха — и это единственная причина молчать, которую надо проверить
    на живой базе: остальные разобраны на чистых функциях выше.
    """
    from sqlalchemy import update

    from database.models import TrainingDay
    from workers.notifier import _handle_user

    day = await _program_with_today(db, TZ.key)
    # Тот же день, но под чужим именем: сегодня в программе теперь пусто.
    await db.execute(
        update(TrainingDay).where(TrainingDay.id == day.id).values(day_of_week="Тестдень")
    )
    await db.commit()

    bot = FakeSender()
    await _handle_user(bot, db, (await orm_users_to_notify(db))[0])
    assert bot.sent == []


# ---------------------------------------------------------------- найдено на проде


def test_a_forgotten_session_does_not_silence_everything():
    """
    Найдено выкаткой, а не тестом.

    Тренировка от 20 августа висела на проде незакрытой 2.7 суток — так бывает
    всегда, когда из зала ушли, не нажав «Завершить». Первая версия правила
    считала «идёт тренировка» по наличию незакрытой строки и потому глушила ВСЁ:
    и напоминание о дне, и тихие. То есть человек с одной забытой сессией не
    получил бы ни одного уведомления никогда.
    """
    from services.notifications import UNFINISHED_MAX_IDLE

    stale = Active(session_id="s", idle=UNFINISHED_MAX_IDLE + 1, sets=9)
    assert kinds(plan(facts(active=stale))) == [DAY]

    evening = facts(now=moment(MONDAY, 19), today=None, active=stale,
                    missed=Missed(day_id=1, name="Воскресенье", days_ago=1))
    assert kinds(plan(evening)) == [MISSED]

    # А настоящая тренировка по-прежнему всё глушит: человек в зале.
    fresh = Active(session_id="s", idle=20, sets=4)
    assert kinds(plan(facts(active=fresh))) == []


def test_missed_day_is_silent_for_someone_long_gone():
    """
    Тоже с прода: у пользователя 159 дней без зала и программа на месте.

    `missed_day` в такой ситуации показывает пропущенным КАЖДЫЙ день программы,
    и у каждого свой ключ повтора — то есть четыре сообщения в неделю тому, кто
    полгода не заходил. Его забирает приглашение вернуться, и ровно один раз.
    """
    gone = facts(now=moment(MONDAY, 19), today=None, days_off=159,
                 last_training=MONDAY - timedelta(days=159),
                 missed=Missed(day_id=1, name="Суббота", days_ago=1))
    assert kinds(plan(gone)) == [COMEBACK]

    # У того, кто ходит, пропущенный день по-прежнему первее возвращения.
    active_user = facts(now=moment(MONDAY, 19), today=None, days_off=2,
                        last_training=MONDAY - timedelta(days=2),
                        missed=Missed(day_id=1, name="Суббота", days_ago=1))
    assert kinds(plan(active_user)) == [MISSED]
