"""
Настройки напоминаний.

Единственное, что Mini App делает с уведомлениями: шлёт их бот, а здесь человек
говорит, во сколько тренируется и что ему писать. Само решение «слать или молчать»
живёт в services/notifications.py, отправка — в workers/notifier.py.

Строка настроек заводится ЛЕНИВО, при первом сохранении. До тех пор GET отдаёт
значения по умолчанию из того же модуля, которым пользуется воркер, — так «я
ничего не менял» означает одно и то же по обе стороны.
"""
from fastapi import APIRouter

from database.orm_notify import orm_get_prefs, orm_save_prefs
from miniapp.db import Session
from miniapp.deps import ClientTz, CurrentUser
from miniapp.schemas import NotificationsIn
from services.notifications import (
    COMEBACK_AFTER_DAYS,
    IDLE_MINUTES,
    QUIET_FROM,
    QUIET_TO,
    hhmm,
    prefs_of,
)
from services.progress import usual_start_minutes

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


def prefs_json(prefs) -> dict:
    """
    Наружу время уходит строкой «18:30», а не минутами.

    Внутри минуты — по ним считается напоминание, — но в браузере это `input
    type="time"`, и он говорит ровно на этом языке. Переводить в одном месте
    дешевле, чем в обоих.
    """
    return {
        "enabled": prefs.enabled,
        "train_at": hhmm(prefs.train_at_minutes),
        "lead_minutes": prefs.lead_minutes,
        "remind_at": hhmm(max(0, prefs.train_at_minutes - prefs.lead_minutes)),
        "day_reminder": prefs.day_reminder,
        "unfinished": prefs.unfinished,
        "weekly": prefs.weekly,
        "missed": prefs.missed,
    }


@router.get("")
async def get_notifications(user: CurrentUser, session: Session, tz: ClientTz):
    """
    Настройки плюс то, что о них знает сервер.

    `usual` — во сколько человек обычно начинает по истории тренировок. Экран
    показывает это подсказкой под полем: угадывать за пользователя мы не беремся
    (время по умолчанию — 10:00 и остаётся таким), а вот показать ему его же
    привычку можем.

    `rules` — числа, зашитые в services/notifications.py. Они не настраиваются,
    но экран обязан их назвать: «мы не пишем ночью» без границ ночи — обещание,
    которое нечем проверить.
    """
    prefs = prefs_of(await orm_get_prefs(session, user.user_id))
    usual = await usual_start_minutes(session, user.user_id, tz)

    return {
        "ok": True,
        "settings": prefs_json(prefs),
        "usual": hhmm(usual) if usual is not None else None,
        "rules": {
            "quiet_from": hhmm(QUIET_FROM),
            "quiet_to": hhmm(QUIET_TO),
            "idle_minutes": IDLE_MINUTES,
            "comeback_days": COMEBACK_AFTER_DAYS,
        },
    }


@router.patch("")
async def update_notifications(body: NotificationsIn, user: CurrentUser, session: Session):
    """Правка. Приезжает только то, что реально поменяли, — остальное не трогаем."""
    data = body.model_dump(exclude_none=True)

    # Время приходит строкой, а хранится минутами: колонка одна, имя другое.
    if "train_at" in data:
        data["train_at_minutes"] = body.minutes()
        del data["train_at"]

    prefs = await orm_save_prefs(session, user.user_id, data)
    return {"ok": True, "settings": prefs_json(prefs)}
