from sqlalchemy.ext.asyncio import AsyncSession
from aiogram.types import InputMediaPhoto

from GYM_assistant.database.orm_query import (
    orm_get_program,
    orm_get_programs,
    orm_get_training_day,
    orm_get_training_days,
    orm_get_exercises,
    orm_get_exercise,
    orm_get_sets,
    orm_get_set,
    orm_delete_program,
    orm_update_set,
    orm_get_banner
)
from GYM_assistant.kbds.inline import (
    get_user_main_btns,
    get_user_programs_list,
    get_training_day_btns,
)

from GYM_assistant.utils.paginator import Paginator


async def main_menu(session, level, menu_name):
    banner = await orm_get_banner(session, menu_name)

    if banner is None:
        # If no banner is found, provide a default image or message
        image = InputMediaPhoto(media='https://postimg.cc/Ty7d15kq', caption='Привет, тестировщик :)')
    else:
        image = InputMediaPhoto(media=banner.image, caption=banner.description)

    kbds = get_user_main_btns(level=level)

    return image, kbds


async def programs_catalog(session, level, menu_name, user_id):
    banner = await orm_get_banner(session, menu_name)
    if banner is None:
        image = InputMediaPhoto(media='https://postimg.cc/Ty7d15kq', caption='Привет, тестировщик :)')
    else:
        image = InputMediaPhoto(media=banner.image, caption=banner.description)

    categories = await orm_get_programs(session, user_id=user_id)
    kbbs = get_user_programs_list(level=level, programs=categories)

    return image, kbbs


def pages(paginator: Paginator):
    btns = dict()
    if paginator.has_previous():
        btns["◀ Пред."] = "previous"

    if paginator.has_next():
        btns["След. ▶"] = "next"

    return btns


async def training_days(session, level, training_program, page):
    training_days_list = await orm_get_training_days(session, training_program)

    paginator = Paginator(training_days_list, page=page)
    training_day = paginator.get_page()[0]

    image = InputMediaPhoto(
        media=training_day.image,
        caption=f"<strong>{training_day.day_of_week}\
                </strong>\n{training_day.description}\n\
                <strong>День {paginator.page} из {paginator.pages}</strong>",
    )

    pagination_btns = pages(paginator)

    kbds = get_training_day_btns(
        level=level,
        user_program=training_program,
        page=page,
        pagination_btns=pagination_btns,
        training_day_id=training_day.id,
    )

    return image, kbds


async def get_menu_content(
        session: AsyncSession,
        level: int,
        menu_name: str,
        program_id: int | None = None,
        page: int | None = None,
        training_day_id: int | None = None,
        user_id: int | None = None,
):
    if level == 0:
        return await main_menu(session, level, menu_name)
    elif level == 1:
        return await programs_catalog(session, level, menu_name, user_id)
    elif level == 2:
        return await training_days(session, level, program_id, page)
