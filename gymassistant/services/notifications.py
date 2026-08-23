"""
Кому, что и когда написать — чистыми функциями.

Модуль ничего не знает ни про Telegram, ни про SQLAlchemy: на вход факты, на
выход список сообщений. Ровно как services/workout.py и services/progression.py —
и по той же причине: решение «слать или молчать» стоит дороже ошибки в отправке,
а проверять его в тестах можно только тогда, когда оно отделено от ввода-вывода.

Три правила, вокруг которых всё построено.

**Уведомление обязано что-то менять.** Каждое из шести шлётся только там, где
у человека есть, что с ним сделать: до тренировки — пойти, посреди брошенной —
закрыть её, после пропуска — отработать. Сводка недели уходит вечером
в воскресенье, когда закрывать уже нечего, и именно поэтому она одна и последняя.

**Не больше одного «тихого» в сутки.** Напоминание о тренировке и вопрос
о брошенной тренировке привязаны к событию, их не откладывают. Всё остальное —
серия, пропуск, итог недели, возвращение — конкурирует за ОДНО место в дне
(`ambient_today` в фактах). Иначе в воскресенье человек получил бы три сообщения
подряд: про пропущенный четверг, про серию и про итог.

**Тихие часы — с 22:00 до 07:00 по месту пользователя.** Кроме напоминания
о тренировке: его время человек назначил сам, и молча сдвигать назначенное —
худшее, что можно сделать с будильником. Остальное не отменяется, а ЖДЁТ: условие
проверяется каждую минуту и сработает, как только тихие часы кончатся.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta

# Виды уведомлений. Строкой, а не Enum: они уезжают в колонку `notification.kind`
# и в callback_data кнопок, и в обоих местах это всё равно строка.
DAY = "day"
UNFINISHED = "unfinished"
STREAK = "streak"
MISSED = "missed"
WEEK = "week"
COMEBACK = "comeback"

# Тихие часы по месту пользователя.
QUIET_FROM = 22 * 60
QUIET_TO = 7 * 60

# Сколько минут тишины посреди тренировки означают «человек ушёл и забыл нажать
# „Завершить“». Полтора часа: типичная тренировка — 9–12 подходов за 40–60 минут,
# то есть 5–8 минут на подход. Даже очередь к стойке и разговор со знакомым в этот
# порог укладываются, а вот дорога домой — уже нет.
IDLE_MINUTES = 90

# И насколько поздно спрашивать перестаём. Сутки: дальше это уже не «забыл нажать
# „Завершить“», а осадок в базе — и вопрос «ты ещё в зале?» про позавчерашнюю
# тренировку выглядел бы поломкой. Такие сессии вреда не приносят: в историю и
# статистику они попадают наравне с закрытыми.
UNFINISHED_MAX_IDLE = 24 * 60

# С какого дня паузы зовём обратно. Десять — заведомо больше самой редкой
# осмысленной частоты (раз в неделю), то есть это уже не «сдвинул тренировку»,
# а «перестал ходить».
COMEBACK_AFTER_DAYS = 10

# Час по месту пользователя, в который уходит каждое из «тихих». Разнесены,
# чтобы конкуренция за единственное место в дне была осмысленной: сперва то,
# что ещё можно исправить (серия — днём), потом то, что уже только к сведению.
STREAK_HOUR = 12
COMEBACK_HOUR = 18
MISSED_HOUR = 19
WEEK_HOUR = 20

# С какого дня недели предупреждаем о серии. Пятница: раньше говорить не о чем.
# Условие «запаса не осталось» само по себе выполняется ВСЕГДА — цель недели и есть
# число тренировочных дней программы, поэтому в понедельник, когда впереди все
# четыре дня и сделано ноль, «запаса нет» формально верно и совершенно бесполезно.
# Новостью это становится к концу недели, когда каждый оставшийся день — последний
# шанс закрыть кольцо.
STREAK_WEEKDAY = 4

# Насколько поздно ещё имеет смысл прислать напоминание о тренировке, если воркер
# всё её окно пролежал. Четверть часа: лучше с опозданием, чем никогда, — но не
# «вечером о том, что было утром».
DAY_GRACE_MINUTES = 15


@dataclass(frozen=True)
class Prefs:
    """
    Настройки пользователя. Значения по умолчанию обязаны совпадать с
    server_default колонок `notification_prefs`: строка заводится лениво, и до
    первого захода в настройки работают именно эти числа.
    """
    enabled: bool = True
    # Минуты от полуночи. 10:00 — договорённость по умолчанию, правится в Mini App.
    train_at_minutes: int = 600
    lead_minutes: int = 180
    day_reminder: bool = True
    unfinished: bool = True
    weekly: bool = True
    missed: bool = True


DEFAULTS = Prefs()


def prefs_of(row) -> Prefs:
    """Строка настроек → Prefs. Нет строки — значения по умолчанию."""
    if row is None:
        return DEFAULTS
    return Prefs(
        enabled=row.enabled,
        train_at_minutes=row.train_at_minutes,
        lead_minutes=row.lead_minutes,
        day_reminder=row.day_reminder,
        unfinished=row.unfinished,
        weekly=row.weekly,
        missed=row.missed,
    )


@dataclass(frozen=True)
class DayPlan:
    """Что стоит в программе на сегодня. None вместо неё — день отдыха."""
    name: str
    exercises: int
    sets: int


@dataclass(frozen=True)
class Active:
    """
    Незакрытая тренировка. `idle` — минут с последнего записанного подхода.

    «Незакрытая» и «идущая» — не одно и то же, и путать их дорого: сессия
    остаётся с `finished_at = NULL` навсегда, если человек ушёл, не нажав
    «Завершить». Идущей её считает `_in_gym()`.
    """
    session_id: str
    idle: int
    sets: int


@dataclass(frozen=True)
class WeekProgress:
    """То же, что показывает недельная карточка на главной (weekly_progress)."""
    done: int
    goal: int
    left: int
    streak: int


@dataclass(frozen=True)
class Missed:
    day_id: int
    name: str
    days_ago: int


@dataclass(frozen=True)
class Facts:
    """
    Всё, что нужно знать про пользователя, чтобы решить.

    `now` — ОБЯЗАТЕЛЬНО в поясе пользователя: весь модуль рассуждает о времени
    суток и о дне недели, а сервер стоит в НСК и живёт в UTC.
    """
    now: datetime
    prefs: Prefs = DEFAULTS
    today: DayPlan | None = None
    trained_today: bool = False
    active: Active | None = None
    week: WeekProgress | None = None
    missed: Missed | None = None
    # Дней с последней тренировки. None — тренировок не было вовсе: зазывать
    # обратно того, кто ещё не приходил, не из чего.
    days_off: int | None = None
    last_training: date | None = None
    # «Тихое» уведомление сегодня уже отправляли.
    ambient_today: bool = False


@dataclass(frozen=True)
class Push:
    """
    Готовое сообщение.

    `dedup` — ключ «одного раза»: на него стоит уникальный индекс в БД, и он же
    единственная причина, по которой окно напоминания в три часа не превращается
    в 180 одинаковых сообщений при тике раз в минуту.

    `action` — что подвесить кнопкой: `app` открывает Mini App, `finish:<id>`
    завершает названную тренировку.
    """
    kind: str
    dedup: str
    text: str
    ttl: timedelta
    action: str | None = None


def hhmm(minutes: int) -> str:
    """Минуты от полуночи → «18:30». Используется и в тексте, и в API настроек."""
    minutes = max(0, min(24 * 60 - 1, int(minutes)))
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def plan(facts: Facts) -> list[Push]:
    """
    Что отправить этому пользователю прямо сейчас. Обычно — ничего.

    Порядок не косметика: напоминание о тренировке проходит мимо тихих часов
    (его время назначил сам человек), вопрос о брошенной тренировке их ждёт,
    а «тихие» ещё и делят одно место в сутках.
    """
    if not facts.prefs.enabled:
        return []

    out: list[Push] = []

    day = _day_reminder(facts)
    if day:
        out.append(day)

    if _quiet(facts.now):
        return out

    unfinished = _unfinished(facts)
    if unfinished:
        out.append(unfinished)

    ambient = _ambient(facts)
    if ambient:
        out.append(ambient)

    return out


def _in_gym(facts: Facts) -> bool:
    """
    Человек прямо сейчас в зале — то есть молчим про всё остальное.

    Не «есть незакрытая тренировка»: она остаётся незакрытой навсегда, если из
    зала ушли, не нажав «Завершить». На проде такая висела 2.7 суток — и по
    первой версии правила глушила ВСЁ, включая напоминание о дне тренировки.
    Порог тот же, по которому мы перестаём спрашивать «ты ещё в зале?»: после
    суток тишины это уже не тренировка, а строка в базе.
    """
    return facts.active is not None and facts.active.idle <= UNFINISHED_MAX_IDLE


def _quiet(now: datetime) -> bool:
    minutes = now.hour * 60 + now.minute
    return minutes >= QUIET_FROM or minutes < QUIET_TO


def _minutes(now: datetime) -> int:
    return now.hour * 60 + now.minute


def _monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _day_reminder(facts: Facts) -> Push | None:
    """
    «Сегодня тренировка» за `lead_minutes` до назначенного часа.

    Окно, а не момент: воркер просыпается раз в минуту и может пролежать
    перезапуск пода. Начало окна упирается в полночь — напоминание не уезжает
    во вчера, даже если человек тренируется в час ночи с напоминанием за три
    часа. Граница суток здесь и есть граница: сообщение «сегодня тренировка»,
    отправленное вчера, врёт словом «сегодня».
    """
    prefs = facts.prefs
    if not prefs.day_reminder or not facts.today or facts.trained_today or _in_gym(facts):
        return None

    start = max(0, prefs.train_at_minutes - prefs.lead_minutes)
    now = _minutes(facts.now)
    if not start <= now <= prefs.train_at_minutes + DAY_GRACE_MINUTES:
        return None

    day = facts.today
    return Push(
        kind=DAY,
        dedup=facts.now.date().isoformat(),
        text=(
            f"Сегодня тренировка в {hhmm(prefs.train_at_minutes)}.\n\n"
            f"{_plural(day.exercises, 'упражнение', 'упражнения', 'упражнений')}, "
            f"{_plural(day.sets, 'подход', 'подхода', 'подходов')}."
        ),
        # До конца суток: смысл сообщения — «сегодня», завтра оно уже врёт.
        ttl=timedelta(minutes=24 * 60 - now),
        action="app",
    )


def _unfinished(facts: Facts) -> Push | None:
    """
    Тренировка открыта, а подходов давно нет — человек ушёл и не нажал «Завершить».

    Пустую тренировку (ноль подходов) не трогаем: это промах по кнопке «Начать»,
    и её всё равно уберёт orm_delete_empty_sessions. Спрашивать про неё значило бы
    наказывать пушем за случайный тап.
    """
    active = facts.active
    if not facts.prefs.unfinished or not active:
        return None
    if active.sets < 1 or not IDLE_MINUTES <= active.idle <= UNFINISHED_MAX_IDLE:
        return None

    return Push(
        kind=UNFINISHED,
        # Ключ — сама тренировка: спрашиваем один раз за неё, а не раз в сутки.
        dedup=active.session_id,
        text=(
            "Тренировка не завершена.\n\n"
            f"{_plural(active.sets, 'подход', 'подхода', 'подходов')}, "
            f"последний {_ago(active.idle)} назад."
        ),
        ttl=timedelta(hours=6),
        action=f"finish:{active.session_id}",
    )


def _ambient(facts: Facts) -> Push | None:
    """
    Одно «тихое» уведомление в сутки, по приоритету.

    Приоритет — по тому, можно ли ещё что-то сделать: серия под угрозой (можно
    спасти сегодня) важнее пропущенного дня (можно отработать завтра), а тот
    важнее итога недели (только к сведению). Возвращение — последнее: если
    человек не был десять дней, у него нет ни серии, ни свежего пропуска, и до
    остальных веток очередь всё равно не дойдёт.

    Пока идёт тренировка, молчим совсем: человек в зале, и любое из четырёх
    сообщений там неуместно.
    """
    if facts.ambient_today or _in_gym(facts):
        return None

    for build in (_streak, _missed, _week, _comeback):
        push = build(facts)
        if push:
            return push
    return None


def _streak(facts: Facts) -> Push | None:
    """
    Серия под угрозой — единственное уведомление, которое зовёт исправить
    положение, а не сообщает о нём.

    Три условия, и каждое отсекает свой вид пустого сообщения.

    **Конец недели** (с пятницы). Само по себе «запаса не осталось» выполняется
    почти всегда: цель недели И ЕСТЬ число тренировочных дней программы, так что
    в понедельник при нуле сделанных запаса тоже нет — и предупреждение приходило
    бы каждый понедельник. Новостью оно становится тогда, когда оставшиеся дни
    можно пересчитать по пальцам.

    **Запас не растрачен** (`left == goal - done`). Если пропущено больше, чем
    осталось впереди, неделя уже не закрывается, и звать бессмысленно: спасать
    нечего. Строгое равенство отделяет «ещё можно, но только всё подряд» от
    «поздно».

    **Есть что терять** (`streak >= 1`). «Серия 0 недель прервётся» не значит
    ничего.
    """
    week = facts.week
    if not facts.prefs.weekly or not week or week.goal <= 0:
        return None
    if week.done >= week.goal or week.left <= 0 or week.streak < 1:
        return None
    if week.left != week.goal - week.done:
        return None
    if facts.now.weekday() < STREAK_WEEKDAY or facts.now.hour < STREAK_HOUR:
        return None

    return Push(
        kind=STREAK,
        dedup=_monday(facts.now.date()).isoformat(),
        text=(
            f"Серия — {_plural(week.streak, 'неделя', 'недели', 'недель')}.\n\n"
            f"До конца недели — "
            f"{_plural(week.left, 'тренировка', 'тренировки', 'тренировок')}."
        ),
        ttl=timedelta(hours=12),
        action="app",
    )


def _missed(facts: Facts) -> Push | None:
    """
    Пропущенный день. Тот же, что показывает главный экран (`missed_day`), —
    второго мнения о пропусках в приложении быть не должно.

    Ключ повтора — не «сегодня», а сам пропуск: день программы плюс дата, на
    которую он выпал. Иначе один и тот же несделанный вторник напоминал бы о себе
    каждый вечер до конца недели.
    """
    missed = facts.missed
    if not facts.prefs.missed or not missed:
        return None
    if facts.now.hour < MISSED_HOUR:
        return None
    # Ушедшему совсем про отдельный день говорить нечего: пропущены ВСЕ, и у
    # каждого свой ключ повтора — то есть по сообщению на каждый день программы,
    # неделя за неделей. Такого человека забирает `_comeback`, и ровно один раз.
    if facts.days_off is not None and facts.days_off >= COMEBACK_AFTER_DAYS:
        return None

    fell_on = facts.now.date() - timedelta(days=missed.days_ago)
    return Push(
        kind=MISSED,
        dedup=f"{missed.day_id}:{fell_on.isoformat()}",
        # Именительный после двоеточия, а не «Воскресенье остался»: у дней недели
        # три разных рода, и любая согласованная фраза врёт на четырёх из семи.
        text=f"Пропущено: {missed.name.lower()}.",
        ttl=timedelta(hours=20),
        action="app",
    )


def _week(facts: Facts) -> Push | None:
    """
    Итог недели вечером в воскресенье.

    Неделя без единой тренировки не подводится: сводка «0 из 4» — это упрёк,
    а не сводка, и сказать по ней нечего. Такого человека забирает `_comeback`,
    у которого хотя бы есть предложение.
    """
    week = facts.week
    if not facts.prefs.weekly or not week or week.goal <= 0:
        return None
    if facts.now.weekday() != 6 or facts.now.hour < WEEK_HOUR:
        return None
    if week.done < 1:
        return None

    if week.done >= week.goal:
        text = (
            f"Неделя закрыта: {week.done} из {week.goal}.\n\n"
            f"Серия — {_plural(max(week.streak, 1), 'неделя', 'недели', 'недель')}."
        )
    else:
        text = f"Неделя: {week.done} из {week.goal}."

    return Push(
        kind=WEEK,
        dedup=_monday(facts.now.date()).isoformat(),
        text=text,
        ttl=timedelta(hours=14),
        action=None,
    )


def _comeback(facts: Facts) -> Push | None:
    """
    Позвать обратно после долгой паузы. Ровно один раз за паузу.

    Ключ повтора — дата последней тренировки: пока она не изменилась, пауза та же
    самая, и второго приглашения не будет ни через месяц, ни через полгода.
    Именно это отличает напоминание от преследования.
    """
    if not facts.prefs.missed or facts.days_off is None or facts.last_training is None:
        return None
    if facts.days_off < COMEBACK_AFTER_DAYS or facts.now.hour < COMEBACK_HOUR:
        return None

    return Push(
        kind=COMEBACK,
        dedup=facts.last_training.isoformat(),
        text=f"Последняя тренировка — {_plural(facts.days_off, 'день', 'дня', 'дней')} назад.",
        ttl=timedelta(hours=20),
        action="app",
    )


def _plural(n: int, one: str, few: str, many: str) -> str:
    """Русская форма числительного. Копия plural() из ui.js — здесь тексты серверные."""
    mod10, mod100 = n % 10, n % 100
    if mod10 == 1 and mod100 != 11:
        return f"{n} {one}"
    if 2 <= mod10 <= 4 and not 10 <= mod100 < 20:
        return f"{n} {few}"
    return f"{n} {many}"


def _ago(minutes: int) -> str:
    """«40 минут» или «2 часа». Точность до минуты вопросу не нужна."""
    if minutes < 60:
        return _plural(minutes, "минуту", "минуты", "минут")
    return _plural((minutes + 30) // 60, "час", "часа", "часов")
