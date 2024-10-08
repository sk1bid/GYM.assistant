from email.mime import image

from sqlalchemy.ext.asyncio import AsyncSession
from aiogram.types import InputMediaPhoto
from aiogram import F, types

from database.orm_query import (
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
    orm_get_banner,
    orm_get_user_by_id, orm_add_training_day, orm_get_admin_exercises
)
from kbds.inline import (
    get_user_main_btns,
    get_user_programs_list,
    get_training_day_btns, get_profile_btns, get_schedule_btns, get_change_tr_day_btns,
)

from utils.paginator import Paginator


async def main_menu(session, level, menu_name):
    banner = await orm_get_banner(session, menu_name)

    if banner is None:
        # If no banner is found, provide a default image or message
        image = InputMediaPhoto(media='https://postimg.cc/Ty7d15kq', caption='Привет, тестировщик :) Это главное меню')
    else:
        image = InputMediaPhoto(media=banner.image, caption=banner.description)

    kbds = get_user_main_btns(level=level)

    return image, kbds


async def profile(session, level, menu_name, user_id):
    banner = await orm_get_banner(session, menu_name)
    user = await orm_get_user_by_id(session, user_id)

    if banner is None:
        # If no banner is found, provide a default image or message
        image = InputMediaPhoto(media='https://postimg.cc/Ty7d15kq',
                                caption=f'Привет, тестировщик :) Это твой профиль\n{user.name} - {user.weight} кг')
    else:
        image = InputMediaPhoto(media=banner.image, caption=banner.description)

    kbds = get_profile_btns(level=level)

    return image, kbds


async def schedule(session, level, menu_name):
    banner = await orm_get_banner(session, menu_name)

    if banner is None:
        # If no banner is found, provide a default image or message
        image = InputMediaPhoto(media='https://postimg.cc/Ty7d15kq',
                                caption=f'Привет, тестировщик :) Это твое расписание')
    else:
        image = InputMediaPhoto(media=banner.image, caption=banner.description)

    kbds = get_schedule_btns(level=level)

    return image, kbds


async def programs_catalog(session, level, menu_name, user_id):
    banner = await orm_get_banner(session, menu_name)
    if banner is None:
        image = InputMediaPhoto(media='https://postimg.cc/Ty7d15kq',
                                caption='Привет, тестировщик :) Это твой каталог программ')
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


async def training_days(session, level, training_program_id, page):
    program = await orm_get_program(session, training_program_id)
    training_days_list = await orm_get_training_days(session, training_program_id)
    if not training_days_list:
        print("111111111111111111")
        for day in ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]:
            await orm_add_training_day(session, day_of_week=day, program_id=training_program_id)
            training_days_list = await orm_get_training_days(session, training_program_id)
    paginator = Paginator(training_days_list, page=page)
    training_day = paginator.get_page()[0]

    exercises_str = "Упражнений не обнаружено"

    image = InputMediaPhoto(
        media='https://postimg.cc/Ty7d15kq',
        caption=f"<strong>Программа: {program.name}\n\n"
                f"День {paginator.page} из {paginator.pages} ({training_day.day_of_week})\n\n"
                f"{exercises_str}</strong>",
    )

    pagination_btns = pages(paginator)

    kbds = get_training_day_btns(
        level=level,
        user_program=training_program_id,
        page=page,
        pagination_btns=pagination_btns,
        training_day_id=training_day.id,
    )

    return image, kbds


async def exercises(session, level, training_program_id: int, training_day_id: int, page: int):
    user_exercises = await orm_get_exercises(session, training_day_id)
    admin_exercises = await orm_get_admin_exercises(session)
    print(admin_exercises[0].description)
    user_image = InputMediaPhoto(media='https://postimg.cc/Ty7d15kq',
                                 caption='')
    kbds = get_change_tr_day_btns(level=level, program_id=training_program_id, training_day_id=training_day_id,
                                  page=page,
                                  exercises=admin_exercises)
    return user_image, kbds


async def get_menu_content(
        session: AsyncSession,
        level: int,
        menu_name: str,
        training_program_id: int | None = None,
        exercise_id: int | None = None,
        page: int | None = None,
        training_day_id: int | None = None,
        user_id: int | None = None,
):
    print(level, menu_name, training_program_id, page, training_day_id, user_id)
    if level == 0:
        return await main_menu(session, level, menu_name)
    elif level == 1:
        if menu_name == "program":
            return await programs_catalog(session, level, menu_name, user_id)
        elif menu_name == "profile":
            return await profile(session, level, menu_name, user_id)
        elif menu_name == "schedule":
            return await schedule(session, level, menu_name)
    elif level == 2:
        return await training_days(session, level, training_program_id, page)
    elif level == 3:
        return await exercises(session, level, training_program_id, training_day_id, page)
