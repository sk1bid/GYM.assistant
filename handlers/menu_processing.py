from asyncio import gather
import time
import logging
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession
from database.orm_query import (
    orm_get_program,
    orm_get_programs,
    orm_get_training_day,
    orm_get_training_days,
    orm_get_exercises,
    orm_get_exercise,
    orm_get_banner,
    orm_get_user_by_id,
    orm_add_exercise,
    orm_get_admin_exercise,
    orm_get_categories,
    orm_get_admin_exercises_in_category,
    orm_get_category,
    orm_add_exercise_set,
    orm_get_exercise_sets,
    orm_turn_on_off_program,
    orm_get_user_exercises_in_category, orm_get_user_exercises, orm_get_user_exercise
)

from kbds.inline import (
    error_btns,
    get_user_programs_list,
    get_training_day_btns,
    get_profile_btns,
    get_schedule_btns,
    get_category_exercise_btns,
    get_category_btns,
    get_program_btns,
    get_trd_edit_btns,
    get_program_stgs_btns,
    get_edit_exercise_btns,
    get_exercise_settings_btns,
    get_training_process_btns,
    get_user_main_btns,
    get_custom_exercise_btns,
)
from utils.paginator import Paginator
from aiogram.types import InputMediaPhoto

from utils.separator import get_action_part

WEEK_DAYS_RU = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]


def exercises_in_program(user_exercises: list):
    if user_exercises:
        caption_text = "<b>Ваши упражнения:</b>\n\n" + "\n".join(
            [f"🔘 <b>{ex.name}</b>" for ex in user_exercises])
    else:
        caption_text = "<strong>Упражнений пока нет. Добавьте новое упражнение!</strong>"
    return caption_text


async def main_menu(session: AsyncSession):
    try:
        banner = await orm_get_banner(session, "main")
        banner_image = InputMediaPhoto(media=banner.image,
                                       caption=f"<strong>{banner.description}</strong>")
        kbds = get_user_main_btns()
        return banner_image, kbds
    except Exception as e:
        logging.exception(f"Ошибка в main_menu: {e}")
        error_image = InputMediaPhoto(
            media='https://postimg.cc/Ty7d15kq',
            caption="Ошибка при загрузке main_menu"
        )
        kbds = error_btns()
        return error_image, kbds


async def profile(session: AsyncSession, level: int, action: str, user_id: int):
    try:
        banner, user = await gather(
            orm_get_banner(session, action),
            orm_get_user_by_id(session, user_id)
        )
        banner_image = InputMediaPhoto(media=banner.image,
                                       caption=f"<strong>{banner.description}:\n {user.name} — вес:"
                                               f" {user.weight}</strong>")
        kbds = get_profile_btns(level=level)
        return banner_image, kbds
    except Exception as e:
        logging.exception(f"Ошибка в profile: {e}")
        error_image = InputMediaPhoto(
            media='https://postimg.cc/Ty7d15kq',
            caption="Ошибка при загрузке profile"
        )
        kbds = error_btns()
        return error_image, kbds


async def schedule(session: AsyncSession, level: int, action: str, training_day_id: int, user_id: int):
    try:
        banner, user_data = await gather(
            orm_get_banner(session, "schedule"),
            orm_get_user_by_id(session, user_id)
        )
        user_program = user_data.actual_program_id
        if user_program:
            today = date.today()
            trd_list = await orm_get_training_days(session, user_data.actual_program_id)
            day_of_week_to_id = {td.day_of_week.strip().lower(): td.id for td in trd_list}
            weekday_index = today.weekday()
            day_of_week_rus = WEEK_DAYS_RU[weekday_index].strip().lower()
            user_training_day_id = day_of_week_to_id.get(day_of_week_rus)
            if training_day_id is None:
                training_day_id = user_training_day_id
            user_trd = await orm_get_training_day(session, training_day_id)

            if user_trd is None:
                banner_image = InputMediaPhoto(
                    media=banner.image,
                    caption="Тренировочный день не найден."
                )
                kbds = get_schedule_btns(
                    level=level,
                    year=today.year,
                    month=today.month,
                    action=action,
                    training_day_id=training_day_id,
                    first_exercise_id=None,
                    active_program=user_program,
                    day_of_week_to_id=day_of_week_to_id,
                )
                return banner_image, kbds

            user_exercises = await orm_get_exercises(session, training_day_id)
            if not user_exercises:
                exercises_caption = "Нет упражнений на сегодня."
            else:
                exercises_caption = exercises_in_program(user_exercises)

            banner_image = InputMediaPhoto(
                media=banner.image,
                caption=f"{user_trd.day_of_week}\n\n{exercises_caption}"
            )

            first_exercise_id = user_exercises[0].id if user_exercises else None

            kbds = get_schedule_btns(
                level=level,
                year=today.year,
                month=today.month,
                action=action,
                training_day_id=training_day_id,
                first_exercise_id=first_exercise_id,
                active_program=user_program,
                day_of_week_to_id=day_of_week_to_id
            )

            return banner_image, kbds
        else:
            banner_image = InputMediaPhoto(
                media=banner.image,
                caption=f"{banner.description}\n\nНе обнаружена программа тренировок\nСоздайте её прямо сейчас!"
            )
            kbds = get_schedule_btns(
                level=level,
                year=None,
                month=None,
                action=action,
                training_day_id=None,
                first_exercise_id=None,
                active_program=None,
            )
            return banner_image, kbds

    except Exception as e:
        logging.exception(f"Ошибка в schedule: {e}")
        error_image = InputMediaPhoto(
            media='https://postimg.cc/Ty7d15kq',
            caption="Ошибка при загрузке schedule"
        )
        kbds = error_btns()
        return error_image, kbds


async def training_process(session: AsyncSession, level: int, training_day_id: int):
    try:
        banner = await orm_get_banner(session, "training_process")
        user_exercises = await orm_get_exercises(session, training_day_id)
        exercises_list = exercises_in_program(user_exercises)
        banner_image = InputMediaPhoto(media=banner.image, caption=banner.description + "\n\n" + exercises_list)
        kbds = get_training_process_btns(level=level, training_day_id=training_day_id)
        return banner_image, kbds
    except Exception as e:
        logging.exception(f"Ошибка в training_process: {e}")
        error_image = InputMediaPhoto(
            media='https://postimg.cc/Ty7d15kq',
            caption="Ошибка при загрузке training_process"
        )
        kbds = error_btns()
        return error_image, kbds


async def programs_catalog(session: AsyncSession, level: int, action: str, user_id: int):
    try:
        banner, programs = await gather(
            orm_get_banner(session, action),
            orm_get_programs(session, user_id=user_id)
        )
        user_data = await orm_get_user_by_id(session, user_id)
        banner_image = InputMediaPhoto(media=banner.image, caption=banner.description)

        kbbs = get_user_programs_list(level=level, programs=programs, active_program_id=user_data.actual_program_id)
        return banner_image, kbbs
    except Exception as e:
        logging.exception(f"Ошибка в programs_catalog: {e}")
        error_image = InputMediaPhoto(
            media='https://postimg.cc/Ty7d15kq',
            caption="Ошибка при загрузке programs_catalog"
        )
        kbds = error_btns()
        return error_image, kbds


def pages(paginator: Paginator, program_name: str):
    btns = {}
    if paginator.has_previous():
        btns["◀ Пред."] = f"previous_{program_name}"
    if paginator.has_next():
        btns["След. ▶"] = f"next_{program_name}"
    return btns


async def program(session: AsyncSession, level: int, training_program_id: int, user_id: int):
    try:
        user_program = await orm_get_program(session, training_program_id)
        banner = await orm_get_banner(session, "user_program")
        user_data = await orm_get_user_by_id(session, user_id)
        indicator = "🟢" if user_data.actual_program_id == user_program.id else "🔴"
        banner_image = InputMediaPhoto(media=banner.image,
                                       caption=f"<strong>{banner.description + user_program.name + ' ' + indicator}</strong>")
        kbds = get_program_btns(level=level, user_program_id=training_program_id)
        return banner_image, kbds
    except Exception as e:
        logging.exception(f"Ошибка в program: {e}")
        error_image = InputMediaPhoto(
            media='https://postimg.cc/Ty7d15kq',
            caption="Ошибка при загрузке program"
        )
        kbds = error_btns()
        return error_image, kbds


async def program_settings(session: AsyncSession, level: int, training_program_id: int, action: str, user_id: int):
    try:
        user_program = await orm_get_program(session, training_program_id)
        user_data = await orm_get_user_by_id(session, user_id)
        active_program = True if user_data.actual_program_id else False

        # Если действия переключают программу
        if action == "turn_on_prgm":
            await orm_turn_on_off_program(session, user_id=user_id, program_id=training_program_id)
            active_program = True
        elif action == "turn_off_prgm":
            await orm_turn_on_off_program(session, user_id=user_id, program_id=None)
            active_program = False

        banner = await orm_get_banner(session, "user_program")
        indicator = "🟢" if active_program else "🔴"
        banner_image = InputMediaPhoto(
            media=banner.image,
            caption=f"<strong>{banner.description + user_program.name + ' ' + indicator}</strong>"
        )
        kbds = get_program_stgs_btns(level=level, user_program_id=training_program_id, action=action,
                                     active_program=active_program)
        return banner_image, kbds
    except Exception as e:
        logging.exception(f"Ошибка в program_settings: {e}")
        error_image = InputMediaPhoto(
            media='https://postimg.cc/Ty7d15kq',
            caption="Ошибка при загрузке programs_settings"
        )
        kbds = error_btns()
        return error_image, kbds


async def training_days(session, level: int, training_program_id: int, page: int):
    try:
        user_program, training_days_list = await gather(
            orm_get_program(session, training_program_id),
            orm_get_training_days(session, training_program_id)
        )
        banner = await orm_get_banner(session, "user_program")

        paginator = Paginator(training_days_list, page=page)
        training_day = paginator.get_page()[0]
        user_exercises = await orm_get_exercises(session, training_day.id)
        caption_text = exercises_in_program(user_exercises)
        image = InputMediaPhoto(
            media=banner.image,
            caption=(
                f"<strong>{banner.description + user_program.name}\n\n"
                f" День {paginator.page} из {paginator.pages} ({training_day.day_of_week})\n\n"
                f"{caption_text}</strong>"
            )
        )
        pagination_btns = pages(paginator, user_program.name)

        kbds = get_training_day_btns(
            level=level,
            user_program_id=training_program_id,
            program=user_program,
            page=page,
            training_day_id=training_day.id,
            pagination_btns=pagination_btns
        )

        return image, kbds
    except Exception as e:
        logging.exception(f"Ошибка в training_days: {e}")
        error_image = InputMediaPhoto(
            media='https://postimg.cc/Ty7d15kq',
            caption="Ошибка при загрузке training_days"
        )
        kbds = error_btns()
        return error_image, kbds


async def edit_training_day(session: AsyncSession, level: int, training_program_id: int, page: int,
                            training_day_id: int, action: str):
    try:
        user_exercises = await orm_get_exercises(session, training_day_id)
        banner = await orm_get_banner(session, "user_program")
        training_day = await orm_get_training_day(session, training_day_id)
        caption_text = exercises_in_program(user_exercises)
        empty_list = not user_exercises

        user_image = InputMediaPhoto(
            media=banner.image,
            caption=f"<strong>{training_day.day_of_week}\n\n{caption_text}</strong>",
        )

        kbds = get_trd_edit_btns(level=level, program_id=training_program_id, page=page,
                                 training_day_id=training_day_id,
                                 empty_list=empty_list, action=action)

        return user_image, kbds
    except Exception as e:
        logging.exception(f"Ошибка в edit_training_day: {e}")
        error_image = InputMediaPhoto(
            media='https://postimg.cc/Ty7d15kq',
            caption="Ошибка при загрузке edit_training_day"
        )
        kbds = error_btns()
        return error_image, kbds


async def show_categories(session: AsyncSession, level: int, training_program_id: int, training_day_id: int, page: int,
                          action: str, user_id: int):
    try:
        user_exercises = await orm_get_exercises(session, training_day_id)
        user_data = await orm_get_user_by_id(session, user_id)
        user_name = user_data.name
        user_custom_exercises = await orm_get_user_exercises(session, user_id)
        categories = await orm_get_categories(session, user_id)

        user_program = await orm_get_program(session, training_program_id)
        banner = await orm_get_banner(session, "user_program")
        caption_text = exercises_in_program(user_exercises)

        user_image = InputMediaPhoto(
            media=banner.image,
            caption=f"<strong>{banner.description + user_program.name}\n\n{caption_text}\n\n"
                    f"Выберите категорию упражнений</strong>",
        )

        kbds = get_category_btns(
            level=level,
            program_id=training_program_id,
            training_day_id=training_day_id,
            page=page,
            categories=categories,
            action=action,
            user_name=user_name,
            len_custom=len(user_custom_exercises),
        )

        return user_image, kbds
    except Exception as e:
        logging.exception(f"Ошибка в show_categories: {e}")
        error_image = InputMediaPhoto(
            media='https://postimg.cc/Ty7d15kq',
            caption="Ошибка при загрузке show_categories"
        )
        kbds = error_btns()
        return error_image, kbds


async def show_exercises_in_category(session: AsyncSession, level: int, exercise_id: int, training_day_id: int,
                                     page: int, action: str, training_program_id: int, category_id: int, user_id: int,
                                     empty: bool):
    try:
        banner = await orm_get_banner(session, "user_program")
        category = await orm_get_category(session, category_id)
        user_program = await orm_get_program(session, training_program_id)
        admin_exercises = await orm_get_admin_exercises_in_category(session, category_id)
        user_exercises = await orm_get_exercises(session, training_day_id)
        user_custom_exercises = await orm_get_user_exercises_in_category(session, category_id, user_id)

        # Если action начинается на "add_..." - добавляем упражнение из админских или пользовательских в список
        if get_action_part(action).startswith("add_"):
            if exercise_id:
                if "custom" in get_action_part(action):
                    exercise = await orm_get_user_exercise(session, exercise_id)
                    exercise_type = 'user'
                else:
                    exercise = await orm_get_admin_exercise(session, exercise_id)
                    exercise_type = 'admin'

                if exercise:
                    # Подготовка данных для добавления упражнения
                    add_data = {
                        "name": exercise.name,
                        "description": exercise.description,
                    }

                    if exercise_type == 'admin':
                        add_data['admin_exercise_id'] = exercise.id
                    elif exercise_type == 'user':
                        add_data['user_exercise_id'] = exercise.id

                    # Добавление упражнения с указанием типа
                    await orm_add_exercise(session, add_data, training_day_id, exercise_type)
                    user_exercises = await orm_get_exercises(session, training_day_id)

                    # Добавление сетов для нового упражнения
                    for _ in range(user_exercises[-1].base_sets):
                        await orm_add_exercise_set(session, user_exercises[-1].id, user_exercises[-1].base_reps)

        if not empty and category_id:
            caption_text = exercises_in_program(user_exercises)

            user_image = InputMediaPhoto(
                media=banner.image,
                caption=f"<strong>{banner.description + user_program.name}\n\n{caption_text}\n\n"
                        f"Упражнения в категории: {category.name}</strong>",
            )
            kbds = get_category_exercise_btns(level=level,
                                              program_id=training_program_id,
                                              training_day_id=training_day_id,
                                              page=page,
                                              template_exercises=admin_exercises,
                                              user_exercises=user_custom_exercises, actual_exercises=user_exercises,
                                              action=action, category_id=category_id, empty=empty)

        else:
            user_custom_exercises = await orm_get_user_exercises(session, user_id)
            caption_text = exercises_in_program(user_exercises)
            if user_custom_exercises:
                user_image = InputMediaPhoto(
                    media=banner.image,
                    caption=f"<strong>{banner.description + user_program.name}\n\n{caption_text}\n\n"
                            f"Пользовательские упражнения:</strong>",
                )
            else:
                user_image = InputMediaPhoto(
                    media=banner.image,
                    caption=f"<strong>{banner.description + user_program.name}\n\n{caption_text}\n\n"
                            f"Пользовательские упражнения:\n\n"
                            f"{exercises_in_program(user_custom_exercises)}</strong>",
                )
            kbds = get_category_exercise_btns(level=level,
                                              program_id=training_program_id,
                                              training_day_id=training_day_id,
                                              page=page,
                                              user_exercises=user_custom_exercises,
                                              category_id=None,
                                              action=action, empty=empty, actual_exercises=user_exercises)

        return user_image, kbds
    except Exception as e:
        logging.exception(f"Ошибка в show_exercises_in_category: {e}")
        error_image = InputMediaPhoto(
            media='https://postimg.cc/Ty7d15kq',
            caption="Ошибка при загрузке show_exercises_in_category"
        )
        kbds = error_btns()
        return error_image, kbds


async def edit_exercises(session: AsyncSession, level: int, exercise_id: int, training_day_id: int,
                         page: int, action: str, training_program_id: int):
    try:
        user_exercises = await orm_get_exercises(session, training_day_id)
        banner = await orm_get_banner(session, "user_program")
        user_image = InputMediaPhoto(
            media=banner.image,
            caption="<strong>Чтобы изменить упражнение, выберите его из списка:</strong>",
        )

        kbds = get_edit_exercise_btns(level=level, program_id=training_program_id, user_exercises=user_exercises,
                                      page=page, exercise_id=exercise_id,
                                      action=action,
                                      training_day_id=training_day_id)

        return user_image, kbds
    except Exception as e:
        logging.exception(f"Ошибка в edit_exercises: {e}")
        error_image = InputMediaPhoto(
            media='https://postimg.cc/Ty7d15kq',
            caption="Ошибка при загрузке edit_exercises"
        )
        kbds = error_btns()
        return error_image, kbds


async def exercise_settings(session: AsyncSession, level: int, exercise_id: int, training_day_id: int,
                            page: int, action: str, training_program_id: int):
    try:
        user_exercise = await orm_get_exercise(session, exercise_id)
        banner = await orm_get_banner(session, "user_program")
        base_ex_sets = await orm_get_exercise_sets(session, exercise_id)
        user_image = InputMediaPhoto(
            media=banner.image,
            caption="<strong>Добавьте нужное вам количество подходов и повторений</strong>",
        )

        kbds = get_exercise_settings_btns(level=level, action=action, program_id=training_program_id,
                                          page=page, exercise_id=exercise_id,
                                          training_day_id=training_day_id, user_exercise=user_exercise,
                                          base_ex_sets=base_ex_sets)

        return user_image, kbds
    except Exception as e:
        logging.exception(f"Ошибка в exercise_settings: {e}")
        error_image = InputMediaPhoto(
            media='https://postimg.cc/Ty7d15kq',
            caption="Ошибка при загрузке exercise_settings"
        )
        kbds = error_btns()
        return error_image, kbds


async def custom_exercises(session: AsyncSession, level: int, training_day_id: int,
                           page: int, action: str, training_program_id: int, category_id: int, user_id: int,
                           empty: bool, exericse_id: int):
    try:
        if empty is False and category_id:
            custom_user_exercises = await orm_get_user_exercises_in_category(session, category_id, user_id)
            user_category = await orm_get_category(session, category_id)
            banner = await orm_get_banner(session, "user_program")
            if custom_user_exercises:

                user_image = InputMediaPhoto(
                    media=banner.image,
                    caption=f"<strong>Пользовательские упражнения ({user_category.name})</strong>\n\n"
                            f"<strong>Чтобы изменить упражнение, выберите его из списка:</strong>"
                )
            else:
                user_image = InputMediaPhoto(
                    media=banner.image,
                    caption=f"<strong>Пользовательские упражнения ({user_category.name})</strong>\n\n"
                            f"<strong>{exercises_in_program(custom_user_exercises)}</strong>"
                )

            kbds = get_custom_exercise_btns(level=level, action=action, program_id=training_program_id, page=page,
                                            training_day_id=training_day_id, category_id=category_id, empty=empty,
                                            user_exercises=custom_user_exercises, exercise_id=exericse_id)
        else:
            custom_user_exercises = await orm_get_user_exercises(session, user_id)
            banner = await orm_get_banner(session, "user_program")
            if custom_user_exercises:

                user_image = InputMediaPhoto(
                    media=banner.image,
                    caption=f"<strong>Пользовательские упражнения: </strong>\n\n"
                            f"<strong>Чтобы изменить упражнение, выберите его из списка:</strong>")
            else:
                user_image = InputMediaPhoto(
                    media=banner.image,
                    caption=f"<strong>Пользовательские упражнения: </strong>\n\n"
                            f"<strong>{exercises_in_program(custom_user_exercises)}</strong>")

            kbds = get_custom_exercise_btns(level=level, action=action, program_id=training_program_id, page=page,
                                            training_day_id=training_day_id, category_id=category_id, empty=empty,
                                            user_exercises=custom_user_exercises, exercise_id=exericse_id)

        return user_image, kbds

    except Exception as e:
        logging.exception(f"Ошибка в custom_exercises: {e}")
        error_image = InputMediaPhoto(
            media='https://postimg.cc/Ty7d15kq',
            caption="Ошибка при загрузке custom_exercises"
        )
        kbds = error_btns()
        return error_image, kbds


async def get_menu_content(session: AsyncSession, level: int, action: str, training_program_id: int = None,
                           exercise_id: int = None, page: int = None, training_day_id: int = None, user_id: int = None,
                           category_id: int = None, month: int = None, year: int = None, set_id: int = None,
                           empty: bool = False):
    start_time = time.monotonic()
    try:
        # В этом коде мы исходим из того, что action теперь используется вместо menu_name.
        if level == 0:
            # Главный экран
            return await main_menu(session)

        elif level == 1:
            if action == "program":
                return await programs_catalog(session, level, action, user_id)
            elif action == "profile":
                return await profile(session, level, action, user_id)
            elif action in ["schedule", "month_schedule", "t_day"]:
                return await schedule(session, level, action, training_day_id, user_id)

        elif level == 2:
            # Программа или процесс тренировки
            if action == "training_process":
                return await training_process(session, level, training_day_id)
            return await program(session, level, training_program_id, user_id)

        elif level == 3:
            # Настройки программы или дни тренировок
            if action in ["prg_stg", "turn_on_prgm", "turn_off_prgm"] or action.startswith(
                    "to_del_prgm") or action.startswith("prgm_del"):
                return await program_settings(session, level, training_program_id, action, user_id)
            return await training_days(session, level, training_program_id, page)

        elif level == 4:
            # Редактирование тренировочного дня
            return await edit_training_day(session, level, training_program_id, page, training_day_id, action)

        elif level == 5:
            # Либо редактирование упражнений, либо выбор категории
            if action in ["edit_excs", "shd/edit_excs", "to_edit", "shd/to_edit",
                          "del", "shd/del", "mv", "shd/mv"]:
                return await edit_exercises(session, level, exercise_id, training_day_id, page, action,
                                            training_program_id)
            else:
                return await show_categories(session, level, training_program_id, training_day_id, page, action,
                                             user_id)

        elif level == 6:
            # Настройки упражнения или список упражнений в категории
            if action in ["ex_stg", "shd/ex_stg"] or action.startswith("➕") or action.startswith(
                    "➖") or action.startswith("shd/➕") or action.startswith("shd/➖"):
                return await exercise_settings(session, level, exercise_id, training_day_id, page, action,
                                               training_program_id)
            return await show_exercises_in_category(session, level, exercise_id, training_day_id, page, action,
                                                    training_program_id, category_id, user_id, empty)

        elif level == 7:
            # Пользовательские упражнения
            return await custom_exercises(session, level, training_day_id, page, action,
                                          training_program_id, category_id, user_id, empty, exercise_id)

        else:
            logging.warning(f"Неизвестный уровень меню: {level}")
            return (InputMediaPhoto(media='https://postimg.cc/Ty7d15kq',
                                    caption="Ошибка: неизвестный уровень меню"),
                    error_btns())
    except Exception as e:
        logging.exception(f"Ошибка в get_menu_content: {e}")
        return (InputMediaPhoto(media='https://postimg.cc/Ty7d15kq',
                                caption="Ошибка при загрузке меню"),
                error_btns())
    finally:
        end_time = time.monotonic()
        duration = end_time - start_time
        logging.info(f"get_menu_content для action='{action}', level={level} заняла {duration:.2f} секунд")
