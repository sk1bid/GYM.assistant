"""Пользователь текущего запроса."""
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import Depends, Header

from database.models import User
from database.orm_notify import orm_remember_timezone
from database.orm_query import orm_add_user, orm_get_user_by_id
from miniapp.auth import TgUser
from miniapp.config import MAX_USER_NAME
from miniapp.db import Session
from services.clock import known_tz, resolve_tz


async def get_current_user(
    tg: TgUser, session: Session, x_timezone: str | None = Header(default=None)
) -> User:
    """
    Пользователь из БД, заведённый при первом входе.

    Регистрации как отдельного шага больше нет: имя приезжает в initData, вес
    ставим дефолтный и предлагаем поправить в профиле. Диалог «/start → имя → вес»
    в чате был нужен только потому, что другого способа спросить у бота не было.

    Заодно запоминаем пояс. Это единственное место, где он оседает в базе, и
    единственный способ его узнать: воркер напоминаний просыпается сам, без
    запроса клиента, а «сегодня вторник» и «через три часа» считаются по месту
    человека. Заголовок приходит с каждым запросом, поэтому переезд в другой
    часовой пояс лечится сам собой — первым же открытием приложения.
    """
    user = await orm_get_user_by_id(session, tg["id"])

    if user is None:
        await orm_add_user(session, {
            "user_id": tg["id"],
            "name": (tg.get("first_name") or "Атлет")[:MAX_USER_NAME],
            "weight": 75.0,
        })
        user = await orm_get_user_by_id(session, tg["id"])

    # Пишем только при расхождении: иначе UPDATE на каждое открытие экрана.
    # Мусорное имя зоны отбрасываем здесь же — в базе ему делать нечего, а
    # воркер потом не отличит его от «телефон ещё не сообщил».
    if known_tz(x_timezone) and user.timezone != x_timezone:
        await orm_remember_timezone(session, user.user_id, x_timezone)
        user.timezone = x_timezone

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def client_tz(x_timezone: str | None = Header(default=None)) -> ZoneInfo:
    """
    Часовой пояс пользователя из заголовка X-Timezone.

    Его шлёт фронт (Intl.DateTimeFormat) на каждом запросе — так «сегодня» считается
    в поясе конкретного юзера, а не сервера. Невалидное/пустое значение → дефолт.

    В базу зона попадает не здесь, а в `get_current_user`: там уже есть и сессия,
    и пользователь, которому её приписать.
    """
    return resolve_tz(x_timezone)


ClientTz = Annotated[ZoneInfo, Depends(client_tz)]
