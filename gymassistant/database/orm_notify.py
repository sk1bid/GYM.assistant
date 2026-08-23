"""
Слой данных для напоминаний: настройки и журнал отправленного.

Две таблицы, две обязанности. `notification_prefs` — что человек разрешил.
`notification` — что ему уже слали и что из этого ещё висит в чате.

Импортов aiogram здесь нет и быть не должно: настройки правит Mini App, а шлёт
и убирает воркер бота — модуль общий, как и весь database/.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Notification, NotificationPrefs, Set, TrainingSession, User
from services.clock import utcnow

# Виды, которые делят одно место в сутках (см. `_ambient` в services/notifications.py).
AMBIENT_KINDS = ("streak", "missed", "week", "comeback")

# Поля, которые правит Mini App. Перечислены явно: PATCH приходит с фронта, и
# принимать оттуда произвольные имена колонок нельзя.
PREF_FIELDS = (
    "enabled",
    "train_at_minutes",
    "lead_minutes",
    "day_reminder",
    "unfinished",
    "weekly",
    "missed",
)


"""
Настройки
"""


async def orm_get_prefs(session: AsyncSession, user_id: int) -> NotificationPrefs | None:
    """
    Настройки пользователя или None, если он ещё ни разу их не открывал.

    None — это не «выключено», а «по умолчанию»: значения берёт `prefs_of()`
    в services/notifications.py. Заводить строку заранее незачем — пользователей
    может быть много, а настройки правят единицы.
    """
    stmt = select(NotificationPrefs).where(NotificationPrefs.user_id == user_id).limit(1)
    return (await session.execute(stmt)).scalars().first()


async def orm_save_prefs(session: AsyncSession, user_id: int, data: dict) -> NotificationPrefs:
    """Правка настроек. Строки нет — заводим её здесь, с дефолтами модели."""
    prefs = await orm_get_prefs(session, user_id)
    if prefs is None:
        prefs = NotificationPrefs(user_id=user_id)
        session.add(prefs)

    for field in PREF_FIELDS:
        if field in data and data[field] is not None:
            setattr(prefs, field, data[field])

    await session.commit()
    return prefs


async def orm_disable_notifications(session: AsyncSession, user_id: int) -> None:
    """
    Выключить всё для этого пользователя.

    Зовётся, когда Telegram отвечает «бот заблокирован»: писать больше некуда,
    а пытаться каждую минуту — это лог, полный одинаковых ошибок, и очередь
    запросов к API, из которой ничего не выйдет. Человек разблокирует бота и
    включит уведомления сам — тем же тумблером в настройках.
    """
    prefs = await orm_get_prefs(session, user_id)
    if prefs is None:
        prefs = NotificationPrefs(user_id=user_id)
        session.add(prefs)
    prefs.enabled = False
    await session.commit()


@dataclass(frozen=True)
class NotifyTarget:
    """
    Пользователь глазами воркера — три обычных числа и строка.

    Не ORM-объект, и это ВАЖНО. Воркер за одну итерацию разбирает всех, а посреди
    итерации случается `session.rollback()` (столбить уже занятое право — штатная
    ситуация: окно напоминания шириной в три часа, тик раз в минуту). Откат
    ПРОТУХАЕТ все загруженные объекты, и следующее же обращение к атрибуту полезло
    бы за ним в базу — в асинхронном SQLAlchemy это `MissingGreenlet` и падение
    всей итерации. Обычные значения протухнуть не могут.
    """
    user_id: int
    actual_program_id: int
    timezone: str | None


async def orm_users_to_notify(session: AsyncSession) -> list[NotifyTarget]:
    """
    Кого воркер вообще разбирает.

    Только с активной программой: без неё нет ни дня тренировки, ни цели недели,
    ни пропусков — то есть ни одного повода написать. Выключившие уведомления
    отсеиваются здесь же, в SQL: смысла тащить их строки в Python нет.
    """
    stmt = (
        select(User.user_id, User.actual_program_id, User.timezone)
        .outerjoin(NotificationPrefs, NotificationPrefs.user_id == User.user_id)
        .where(
            User.actual_program_id.isnot(None),
            (NotificationPrefs.enabled.is_(True)) | (NotificationPrefs.user_id.is_(None)),
        )
    )
    return [NotifyTarget(*row) for row in (await session.execute(stmt)).all()]


"""
Журнал отправленного
"""


async def orm_claim_notification(
    session: AsyncSession, user_id: int, kind: str, dedup: str, chat_id: int
) -> Notification | None:
    """
    Застолбить отправку. None — это уже слали.

    Порядок именно такой: сперва запись, потом поход в Telegram. Обратный
    (отправить, затем записать) при любой ошибке между шагами даёт ПОВТОР —
    а повторный пуш хуже пропущенного, особенно в окне напоминания шириной
    в три часа при тике раз в минуту. Здесь же сбой отправки означает всего
    лишь «сегодня не написали»: строка останется с пустым message_id.

    Право на «один раз» держит уникальный индекс в БД, а не память процесса:
    воркер перезапускается вместе с подом, и всё, что он «помнил», исчезает.

    Проверка отдельным SELECT перед вставкой — не подстраховка индекса, а способ
    НЕ доводить дело до отката. Повтор здесь — обычное дело (окно напоминания
    шириной в три часа при тике раз в минуту), а `session.rollback()` протухает
    всё, что сессия успела загрузить. Индекс при этом остаётся последним словом:
    он ловит настоящую гонку, которой при одном воркере быть не должно, но
    «не должно» — не гарантия.
    """
    taken = (
        await session.execute(
            select(Notification.id)
            .where(
                Notification.user_id == user_id,
                Notification.kind == kind,
                Notification.dedup == dedup,
            )
            .limit(1)
        )
    ).scalars().first()
    if taken is not None:
        return None

    row = Notification(
        user_id=user_id,
        kind=kind,
        dedup=dedup,
        chat_id=chat_id,
        sent_at=utcnow(),
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return None
    return row


async def orm_attach_message(
    session: AsyncSession, notification_id: int, message_id: int, expires_at: datetime
) -> None:
    """Сообщение ушло: запоминаем, что удалять и до какого срока его терпеть."""
    await session.execute(
        update(Notification)
        .where(Notification.id == notification_id)
        .values(message_id=message_id, expires_at=expires_at)
    )
    await session.commit()


async def orm_live_notifications(session: AsyncSession) -> list[Notification]:
    """Всё, что ещё висит в чате и когда-нибудь должно оттуда исчезнуть."""
    stmt = select(Notification).where(Notification.message_id.isnot(None))
    return list((await session.execute(stmt)).scalars().all())


async def orm_clear_notification(session: AsyncSession, notification_id: int) -> None:
    """
    Сообщение убрано из чата — забываем его id.

    Саму строку не удаляем: она ещё держит «этому человеку в этот день такое уже
    слали». Уберёт её `orm_prune_notifications`, когда повторять станет нечего.
    """
    await session.execute(
        update(Notification).where(Notification.id == notification_id).values(message_id=None)
    )
    await session.commit()


async def orm_prune_notifications(session: AsyncSession, older_than_days: int = 60) -> None:
    """
    Чистка журнала. Держим два месяца — заведомо больше самого длинного ключа
    повтора (неделя у итогов, сутки у остальных). Строки с живым сообщением
    не трогаем: удалить строку — значит потерять message_id и оставить сообщение
    в чате навсегда.
    """
    await session.execute(
        delete(Notification).where(
            Notification.sent_at < utcnow() - timedelta(days=older_than_days),
            Notification.message_id.is_(None),
        )
    )
    await session.commit()


async def orm_ambient_sent_since(session: AsyncSession, user_id: int, since: datetime) -> bool:
    """Слали ли этому человеку «тихое» уведомление после указанного момента."""
    stmt = (
        select(Notification.id)
        .where(
            Notification.user_id == user_id,
            Notification.kind.in_(AMBIENT_KINDS),
            Notification.sent_at >= since,
        )
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first() is not None


"""
Факты о тренировке
"""


async def orm_session_activity(session: AsyncSession, training_session_id) -> tuple[int, datetime | None]:
    """
    Сколько подходов записано в тренировке и когда лёг последний.

    Одним запросом, потому что воркеру нужно и то, и другое: подходов ноль —
    промах по кнопке «Начать», и спрашивать не о чем; последний давно — человек
    ушёл домой. `Set.created` заполняет func.now() — та же naive-UTC шкала, что
    и у `TrainingSession.date`.
    """
    stmt = select(func.count(Set.id), func.max(Set.created)).where(
        Set.training_session_id == training_session_id
    )
    count, last = (await session.execute(stmt)).one()
    return int(count or 0), last


async def orm_open_sessions_of(session: AsyncSession, user_id: int) -> TrainingSession | None:
    """
    Незакрытая тренировка пользователя — без ограничения по возрасту.

    Отличается от `orm_get_active_session` намеренно: та отсекает всё старше
    восьми часов, потому что показывает «продолжить» в интерфейсе. Здесь же
    вопрос обратный — именно про забытую, и восьмичасовой отсечкой мы отрезали
    бы ровно те тренировки, о которых стоит спросить.
    """
    stmt = (
        select(TrainingSession)
        .where(
            TrainingSession.user_id == user_id,
            TrainingSession.finished_at.is_(None),
            TrainingSession.training_day_id.isnot(None),
        )
        .order_by(TrainingSession.date.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


async def orm_has_training_since(session: AsyncSession, user_id: int, since: datetime) -> bool:
    """
    Начинал ли человек тренировку после указанного момента.

    По этому вопросу гаснет напоминание «сегодня тренировка»: повод отпал, значит
    сообщению в чате делать нечего. Смотрим на НАЧАЛО, а не на завершение —
    напоминание своё дело сделало ровно в тот момент, когда человек нажал «Начать».
    """
    stmt = (
        select(TrainingSession.id)
        .where(TrainingSession.user_id == user_id, TrainingSession.date >= since)
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first() is not None


async def orm_session_still_open(session: AsyncSession, training_session_id) -> bool:
    """
    Идёт ли ещё названная тренировка.

    По этому вопросу гаснет вопрос «ты ещё в зале?»: завершили — убираем. Удалённая
    тренировка (её мог унести orm_delete_empty_sessions) тоже считается закрытой.
    """
    stmt = (
        select(TrainingSession.finished_at)
        .where(TrainingSession.id == training_session_id)
        .limit(1)
    )
    row = (await session.execute(stmt)).first()
    return row is not None and row[0] is None


async def orm_remember_timezone(session: AsyncSession, user_id: int, name: str) -> None:
    """
    Запомнить пояс пользователя.

    Зовётся из Mini App на каждом запросе, но пишет только при РАСХОЖДЕНИИ —
    иначе один UPDATE на каждое открытие экрана. Проверку «изменилось ли»
    делает вызывающий: у него объект пользователя уже в руках.
    """
    await session.execute(
        update(User).where(User.user_id == user_id).values(timezone=name)
    )
    await session.commit()
