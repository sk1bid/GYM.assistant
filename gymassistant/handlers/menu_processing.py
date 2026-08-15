import asyncio
import logging
import time
from asyncio import gather
from datetime import date

from aiogram.types import InputMediaPhoto
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Set
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
    orm_get_exercise_sets,
    orm_turn_on_off_program,
    orm_get_user_exercises_in_category, orm_get_user_exercises, orm_get_user_exercise,
    orm_get_training_sessions_by_user, orm_get_training_session
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
    get_sessions_results_btns,
    get_exercises_result_btns, )
from utils.paginator import Paginator
from utils.separator import get_action_part
from utils.temporary_storage import retrieve_data_temporarily
from utils import globals

WEEK_DAYS_RU = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]


def exercises_in_program(user_exercises: list, circle_training: bool = False):
    """
    Форматирует список упражнений с разделителями для круговой тренировки и
    отображает сообщение о режиме круговой тренировки, если он активен

    :param user_exercises: Список упражнений
    :param circle_training: Флаг, указывающий, активна ли круговая тренировка
    :return:
    """
    caption_text = "<b>Ваши упражнения:</b>\n\n"

    if not user_exercises:
        if circle_training:
            caption_text += (
                "<strong>Вы находитесь в режиме круговой тренировки. Добавьте круговые упражнения.</strong>"
            )
        else:
            caption_text += "<strong>Упражнений пока нет. Добавьте новое упражнение!</strong>"
        return caption_text

    # Группируем упражнения в блоки
    blocks = []
    current_block = []
    current_block_type = None

    for ex in user_exercises:
        ex_type = 'circular' if ex.circle_training else 'standard'
        if ex_type != current_block_type:
            if current_block:
                blocks.append((current_block_type, current_block))
            current_block = [ex]
            current_block_type = ex_type
        else:
            current_block.append(ex)
    if current_block:
        blocks.append((current_block_type, current_block))

    for block_type, exercises in blocks:
        if block_type == 'circular':
            caption_text += "<strong>Круговая тренировка:</strong>\n"
            for ex in exercises:
                caption_text += f"🔄 <b>{ex.name}</b>\n"
            caption_text += "<strong>Конец круговой тренировки</strong>\n"
        else:
            for ex in exercises:
                caption_text += f"🔘 <b>{ex.name}</b>\n"

    if circle_training:
        caption_text += "\n<strong>Вы находитесь в режиме круговой тренировки. Добавьте круговые упражнения.</strong>"

    return caption_text


def pages(paginator: Paginator, program_name: str):
    btns = {}
    if paginator.has_previous():
        btns["◀ Пред."] = f"p_{program_name}"
    if paginator.has_next():
        btns["След. ▶"] = f"n_{program_name}"
    return btns


"""
Главное меню
"""


async def main_menu(session: AsyncSession):
    """
    Отображает главное меню
    :param session:
    :return:
    """
    try:
        banner = await orm_get_banner(session, "main")

        if not banner or not banner.image:
            logging.warning("Баннеры не загружены или отсутствует поле image")

            error_image = InputMediaPhoto(
                media=globals.error_pic,
                caption="❗️Баннеры ещё не загружены. Обратитесь к администратору."
            )
            kbds = error_btns()
            return error_image, kbds

        banner_image = InputMediaPhoto(
            media=banner.image,
            caption=f"<strong>{banner.description or ''}</strong>"
        )
        kbds = get_user_main_btns()
        return banner_image, kbds

    except Exception as e:
        logging.exception(f"Ошибка в main_menu: {e}")
        error_image = InputMediaPhoto(
            media=globals.error_pic,
            caption="⚠️ Ошибка при загрузке меню."
        )
        kbds = error_btns()
        return error_image, kbds



"""
Функции профиля
"""


async def profile(session: AsyncSession, level: int, action: str, user_id: int):
    """
    Отображает профиль пользователя
    :param session:
    :param level: уровень меню(1)
    :param action: название действия
    :param user_id: Telegram ID
    :return:
    """
    try:
        banner  = await orm_get_banner(session, action)
            
        user = await orm_get_user_by_id(session, user_id)
        banner_image = InputMediaPhoto(media=banner.image,
                                       caption=f"<strong>{banner.description}:\n {user.name} — вес:"
                                               f" {user.weight}</strong>")
        kbds = get_profile_btns(level=level)
        return banner_image, kbds
    except Exception as e:
        logging.exception(f"Ошибка в profile: {e}")
        error_image = InputMediaPhoto(
            media=globals.error_pic,
            caption="Ошибка при загрузке profile"
        )
        kbds = error_btns()
        return error_image, kbds


async def training_results(session: AsyncSession, level: int, user_id: int, page: int):
    """
    Отображает список выполненных тренировок пользователем
    :param session:
    :param level: уровень(2)
    :param user_id: Telegram ID
    :param page: Номер страницы для пагинации
    :return:
    """
    try:

        banner = await orm_get_banner(session, "training_stats")
        user = await orm_get_user_by_id(session, user_id)
        

        all_sessions = await orm_get_training_sessions_by_user(session, user_id)

        if not all_sessions:
            banner_image = InputMediaPhoto(
                media=banner.image,
                caption=f"<strong>{banner.description}\n\nНет ни одной тренировки</strong>"
            )
            kbds = get_sessions_results_btns(
                level=level,
                page=page, sessions=[], pagination_btns={})
            return banner_image, kbds

        paginator = Paginator(array=all_sessions, page=page, per_page=5)
        current_page_data = paginator.get_page()

        caption = (
            f"<strong>Ваши тренировки\n"
            f"Страница {paginator.page}/{paginator.pages}\n\n"
            f"{banner.description}</strong>"
        )
        banner_image = InputMediaPhoto(
            media=banner.image,
            caption=caption
        )

        pagination_btns = pages(paginator, "t")
        kbds = get_sessions_results_btns(
            level=level,
            page=page,
            pagination_btns=pagination_btns,
            sessions=current_page_data
        )
        return banner_image, kbds

    except Exception as e:
        logging.exception(f"Ошибка в training_results_by_session: {e}")
        error_image = InputMediaPhoto(
            media=globals.error_pic,
            caption="Ошибка при загрузке списка тренировочных сессий"
        )
        kbds = error_btns()
        return error_image, kbds


async def show_result(session: AsyncSession, level: int, page: int, session_page: int, session_number: str):
    """
    Показывает результат выполненной тренировки
    :param session:
    :param level: уровень меню(3)
    :param page: номер страницы для всех тренировок
    :param session_page: номер страницы для упражнений в данной тренировке
    :param session_number: ключ от хранилища (в оперативной памяти)
    :return:
    """
    try:
        banner = await orm_get_banner(session, "training_stats")

        if session_number:

            session_id = retrieve_data_temporarily(session_number)

            session_data = await orm_get_training_session(session, session_id)
            if not session_data:
                banner_image = InputMediaPhoto(
                    media=banner.image,
                    caption="<strong>Данные по тренировке не найдены</strong>"
                )
                kbds = error_btns()
                return banner_image, kbds

            all_sets_query = await session.execute(
                select(Set).where(Set.training_session_id == session_data.id)
            )
            all_sets = all_sets_query.scalars().all()

            exercises_map = {}
            for s_obj in all_sets:
                ex_obj = await orm_get_exercise(session, s_obj.exercise_id)

                if ex_obj.id not in exercises_map:
                    exercises_map[ex_obj.id] = {
                        "exercise": ex_obj,
                        "sets": []
                    }
                exercises_map[ex_obj.id]["sets"].append(s_obj)

            exercise_items = list(exercises_map.items())
            paginator = Paginator(array=exercise_items, page=page, per_page=2)
            current_page_data = paginator.get_page()

            result_message = (
                f"<strong>Результаты вашей тренировки\n"
                f"Страница {paginator.page}/{paginator.pages}</strong>"
            )

            if not current_page_data:

                result_message += "\n\nУпражнений на этой странице нет."
            else:

                for idx, (ex_id, data_dict) in enumerate(current_page_data, start=1):
                    ex = data_dict["exercise"]
                    sets_for_ex = data_dict["sets"]

                    result_message += f"\n\n👉<strong>Упражнение</strong>: {ex.name}"
                    if sets_for_ex:
                        for s_i, s in enumerate(sets_for_ex, start=1):
                            result_message += (
                                f"\nПодход <strong>{s_i}</strong>: "
                                f"<strong>{s.repetitions}</strong> повтор.,"
                                f" вес: <strong>{s.weight}</strong> кг/блок"
                            )
                    else:
                        result_message += "\n   Нет данных о подходах."

            banner_image = InputMediaPhoto(
                media=banner.image,
                caption=result_message
            )

            exercise_pagination_btns = pages(paginator, "d")

            kbds = get_exercises_result_btns(
                level=level,
                session_number=session_number,
                pagination_btns=exercise_pagination_btns,
                page=page, session_page=session_page,
            )

        else:

            banner_image = InputMediaPhoto(
                media=banner.image,
                caption="<strong>Тренировка не обнаружена</strong>"
            )
            kbds = error_btns()

        return banner_image, kbds

    except Exception as e:
        logging.exception(f"Ошибка в show_result: {e}")
        error_image = InputMediaPhoto(
            media=globals.error_pic,
            caption="Ошибка при загрузке результатов тренировки"
        )
        kbds = error_btns()
        return error_image, kbds


"""
Расписание тренировок
"""


async def schedule(session: AsyncSession, level: int, action: str, training_day_id: int, user_id: int):
    """
    Показывает расписание пользователя
    Изначально этот блок меню показывает текущую неделю
    Также можно её развернуть в полный календарь месяца
    На каждый день можно нажать и настроить его
    Здесь идет запуск тренировки
    :param session:
    :param level: уровень(1)
    :param action: название действия
    :param training_day_id:
    :param user_id: Telegram ID
    :return:
    """
    try:
        banner = await orm_get_banner(session, "schedule")
            
        user_data = await orm_get_user_by_id(session, user_id)
        
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
            media=globals.error_pic,
            caption="Ошибка при загрузке schedule"
        )
        kbds = error_btns()
        return error_image, kbds


async def training_process(session: AsyncSession, level: int, training_day_id: int):
    """
    Показывает информационное сообщение во время тренировки пользователя
    Здесь можно завершить тренировку досрочно
    :param session:
    :param level: уровень меню(2)
    :param training_day_id:
    :return:
    """
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
            media=globals.error_pic,
            caption="Ошибка при загрузке training_process"
        )
        kbds = error_btns()
        return error_image, kbds


"""
Программа тренировок
"""


async def programs_catalog(session: AsyncSession, level: int, action: str, user_id: int):
    """
    Показывает список из программ тренировок пользователя
    :param session:
    :param level: уровень(1)
    :param action: название действия
    :param user_id: Telegram ID
    :return:
    """
    try:
        banner = await orm_get_banner(session, action)
            
        programs = await    orm_get_programs(session, user_id=user_id)
        user_data = await orm_get_user_by_id(session, user_id)
        banner_image = InputMediaPhoto(media=banner.image, caption=banner.description)

        kbbs = get_user_programs_list(level=level, programs=programs, active_program_id=user_data.actual_program_id)
        return banner_image, kbbs
    except Exception as e:
        logging.exception(f"Ошибка в programs_catalog: {e}")
        error_image = InputMediaPhoto(
            media=globals.error_pic,
            caption="Ошибка при загрузке programs_catalog"
        )
        kbds = error_btns()
        return error_image, kbds


async def program(session: AsyncSession, level: int, training_program_id: int, user_id: int):
    """
    Показывает настройки выбранной программы тренировок
    Программу можно включить/выключить, также удалить
    Из этого меню можно перейти к настройке тренировочных дней
    :param session:
    :param level: уровень(2)
    :param training_program_id:
    :param user_id: Telgram ID
    :return:
    """
    try:
        user_program = await orm_get_program(session, training_program_id)
        banner = await orm_get_banner(session, "user_program")
        user_data = await orm_get_user_by_id(session, user_id)
        indicator = "🟢" if user_data.actual_program_id == user_program.id else "🔴"
        banner_image = InputMediaPhoto(media=banner.image,
                                       caption=f"<strong>{banner.description + user_program.name + ' ' + indicator}"
                                               f"</strong>")
        kbds = get_program_btns(level=level, user_program_id=training_program_id)
        return banner_image, kbds
    except Exception as e:
        logging.exception(f"Ошибка в program: {e}")
        error_image = InputMediaPhoto(
            media=globals.error_pic,
            caption="Ошибка при загрузке program"
        )
        kbds = error_btns()
        return error_image, kbds


async def program_settings(session: AsyncSession, level: int, training_program_id: int, action: str, user_id: int):
    """
    Показывает меню настройки программы тренировок
    :param session:
    :param level: уровень(3)
    :param training_program_id:
    :param action: название действия
    :param user_id: Telegram ID
    :return:
    """
    try:
        user_program = await orm_get_program(session, training_program_id)
        user_data = await orm_get_user_by_id(session, user_id)
        active_program = True if user_data.actual_program_id == user_program.id else False

        if action == "turn_on_prgm":
            await orm_turn_on_off_program(session, user_id=user_id, program_id=training_program_id)
            active_program = True
        elif action == "turn_off_prgm":
            await orm_turn_on_off_program(session, user_id=user_id, program_id=None)
            active_program = False

        banner = await orm_get_banner(session, "user_program")
        indicator = "🟢" if user_data.actual_program_id == user_program.id else "🔴"
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
            media=globals.error_pic,
            caption="Ошибка при загрузке programs_settings"
        )
        kbds = error_btns()
        return error_image, kbds


"""
Тренировочный день
"""


async def training_days(session, level: int, training_program_id: int, page: int):
    """
    Показывает тренировочные дни (в виде пагинации, от понедельника до воскресенья)
    :param session:
    :param level: уровень меню(3)
    :param training_program_id:
    :param page: номер страницы для пагинации
    :return:
    """
    try:
        user_program = await orm_get_program(session, training_program_id)
        training_days_list = await orm_get_training_days(session, training_program_id)
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
            media=globals.error_pic,
            caption="Ошибка при загрузке training_days"
        )
        kbds = error_btns()
        return error_image, kbds


async def edit_training_day(session: AsyncSession, level: int, training_program_id: int, page: int,
                            training_day_id: int, action: str):
    """
    Показывает кнопки для добавления и редактирования упражнений в тренировочном дне
    :param session:
    :param level: уровень(4)
    :param training_program_id:
    :param page: страница для пагинации
    :param training_day_id:
    :param action: название действия
    :return:
    """
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
            media=globals.error_pic,
            caption="Ошибка при загрузке edit_training_day"
        )
        kbds = error_btns()
        return error_image, kbds


"""
Добавление упражнений
"""


async def show_categories(session: AsyncSession, level: int, training_program_id: int, training_day_id: int, page: int,
                          action: str, user_id: int, circle_training: bool):
    """
    Показывает категории упражнений для добавления в тренировочный день
    :param session:
    :param level: уровень(5)
    :param training_program_id:
    :param training_day_id:
    :param page: страница для пагинации
    :param action: название действия
    :param user_id: Telegram ID
    :param circle_training: флаг(Упражнение для круговой тренировки или для Обычной)
    :return:
    """
    try:
        user_exercises = await orm_get_exercises(session, training_day_id)
        user_data = await orm_get_user_by_id(session, user_id)
        user_name = user_data.name
        user_custom_exercises = await orm_get_user_exercises(session, user_id)
        categories = await orm_get_categories(session, user_id)

        user_program = await orm_get_program(session, training_program_id)
        banner = await orm_get_banner(session, "user_program")
        caption_text = exercises_in_program(user_exercises, circle_training)

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
            circle_training=circle_training,
        )

        return user_image, kbds
    except Exception as e:
        logging.exception(f"Ошибка в show_categories: {e}")
        error_image = InputMediaPhoto(
            media=globals.error_pic,
            caption="Ошибка при загрузке show_categories"
        )
        kbds = error_btns()
        return error_image, kbds


async def show_exercises_in_category(session: AsyncSession, level: int, exercise_id: int, training_day_id: int,
                                     page: int, action: str, training_program_id: int, category_id: int, user_id: int,
                                     empty: bool, circle_training: bool):
    """
    Показывает пользовательские и предустановленные упражнения в выбранной категории
    :param session:
    :param level: уровень(6)
    :param exercise_id: ID выбранного упражнения
    :param training_day_id:
    :param page: номер страницы для пагинации
    :param action: название действия
    :param training_program_id:
    :param category_id: ID выбранной категории
    :param user_id: Telegram ID
    :param empty: Флаг указывающий на принадлежность категории к Пользовательским упражнениям
    :param circle_training: Флаг указывающий на тип упражнения
    :return:
    """
    try:
        banner = await orm_get_banner(session, "user_program")
        category = await orm_get_category(session, category_id)
        user_program = await orm_get_program(session, training_program_id)
        admin_exercises = await orm_get_admin_exercises_in_category(session, category_id)
        user_exercises = await orm_get_exercises(session, training_day_id)
        user_custom_exercises = await orm_get_user_exercises_in_category(session, category_id, user_id)
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
                        "circle_training": circle_training,
                        "equipment": exercise.equipment,
                    }

                    if exercise_type == 'admin':
                        add_data['admin_exercise_id'] = exercise.id
                    elif exercise_type == 'user':
                        add_data['user_exercise_id'] = exercise.id

                    # Добавление упражнения с указанием типа
                    await orm_add_exercise(session, add_data, training_day_id, exercise_type)
                    user_exercises = await orm_get_exercises(session, training_day_id)

        if not empty and category_id:
            caption_text = exercises_in_program(user_exercises, circle_training)

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
                                              action=action, category_id=category_id, empty=empty,
                                              circle_training=circle_training)

        else:
            user_custom_exercises = await orm_get_user_exercises(session, user_id)
            caption_text = exercises_in_program(user_exercises, circle_training)
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
                                              action=action, empty=empty, actual_exercises=user_exercises,
                                              circle_training=circle_training)

        return user_image, kbds
    except Exception as e:
        logging.exception(f"Ошибка в show_exercises_in_category: {e}")
        error_image = InputMediaPhoto(
            media=globals.error_pic,
            caption="Ошибка при загрузке show_exercises_in_category"
        )
        kbds = error_btns()
        return error_image, kbds


async def edit_exercises(session: AsyncSession, level: int, exercise_id: int, training_day_id: int,
                         page: int, action: str, training_program_id: int):
    """
    Показывает настройки выбранного упражнения: можно переместить вверх/вниз, удалить и настроить кол-во подходов и повторений
    :param session:
    :param level: уровень(5)
    :param exercise_id: ID выбранного упражнения
    :param training_day_id:
    :param page: номер страницы для пагинации
    :param action: название действия
    :param training_program_id:
    :return:
    """
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
            media=globals.error_pic,
            caption="Ошибка при загрузке edit_exercises"
        )
        kbds = error_btns()
        return error_image, kbds


async def exercise_settings(session: AsyncSession, level: int, exercise_id: int, training_day_id: int,
                            page: int, action: str, training_program_id: int):
    """
    Показывает кнопки для настройки каждого подхода для выбранного упражнения
    :param session:
    :param level: уровень(6)
    :param exercise_id: ID выбранного упражнения
    :param training_day_id:
    :param page: номер страницы для пагинации
    :param action: название действия
    :param training_program_id:
    :return:
    """
    try:
        user_exercise = await orm_get_exercise(session, exercise_id)
        banner = await orm_get_banner(session, "user_program")
        base_ex_sets = user_exercise.base_sets
        user_image = InputMediaPhoto(
            media=banner.image,
            caption="<strong>Добавьте нужное вам количество подходов и повторений</strong>",
        )

        kbds = get_exercise_settings_btns(level=level, action=action, program_id=training_program_id,
                                          page=page, exercise_id=exercise_id,
                                          training_day_id=training_day_id, user_exercise=user_exercise.name,
                                          base_ex_sets=base_ex_sets)

        return user_image, kbds
    except Exception as e:
        logging.exception(f"Ошибка в exercise_settings: {e}")
        error_image = InputMediaPhoto(
            media=globals.error_pic,
            caption="Ошибка при загрузке exercise_settings"
        )
        kbds = error_btns()
        return error_image, kbds


async def custom_exercises(session: AsyncSession, level: int, training_day_id: int,
                           page: int, action: str, training_program_id: int, category_id: int, user_id: int,
                           empty: bool, exercise_id: int, circle_training: bool):
    """
    Показывает список пользовательских упражнений с возможностью удалить выбранное упражнение
    :param session:
    :param level: уровень(7)
    :param training_day_id:
    :param page: номер страницы для пагинации
    :param action: название действия
    :param training_program_id:
    :param category_id: ID выбранной категории
    :param user_id: Telegram ID
    :param empty: Флаг указывающий на принадлежность категории к Пользовательским упражнениям
    :param exercise_id: ID выбранного упражнения
    :param circle_training: Флаг указывающий на тип упражнения
    :return:
    """
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
                                            user_exercises=custom_user_exercises, exercise_id=exercise_id,
                                            circle_training=circle_training)
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
                                            user_exercises=custom_user_exercises, exercise_id=exercise_id,
                                            circle_training=circle_training)

        return user_image, kbds

    except Exception as e:
        logging.exception(f"Ошибка в custom_exercises: {e}")
        error_image = InputMediaPhoto(
            media=globals.error_pic,
            caption="Ошибка при загрузке custom_exercises"
        )
        kbds = error_btns()
        return error_image, kbds


"""
Мета-функция (Получает данные со всех функций и организовывает навигацию)
"""


async def get_menu_content(session: AsyncSession, level: int, action: str, training_program_id: int = None,
                           exercise_id: int = None, page: int = None, training_day_id: int = None, user_id: int = None,
                           category_id: int = None, month: int = None, year: int = None, set_id: int = None,
                           empty: bool = False, circle_training: bool = False, session_number: str = None,
                           exercises_page: int = None):
    start_time = time.monotonic()
    try:

        if level == 0:
            return await main_menu(session)

        elif level == 1:
            if action == "program":
                return await programs_catalog(session, level, action, user_id)
            elif action == "profile":
                return await profile(session, level, action, user_id)
            elif action in ["schedule", "month_schedule", "t_day"]:
                return await schedule(session, level, action, training_day_id, user_id)

        elif level == 2:
            if action == "training_process":
                return await training_process(session, level, training_day_id)
            if action == "trd_sts" or action.startswith("n_t") or action.startswith("p_t"):
                return await training_results(session, level, user_id, page)
            return await program(session, level, training_program_id, user_id)

        elif level == 3:
            if action in ["prg_stg", "turn_on_prgm", "turn_off_prgm"] or action.startswith(
                    "to_del_prgm") or action.startswith("prgm_del"):
                return await program_settings(session, level, training_program_id, action, user_id)
            if action == "t_d" or action.startswith("n_d") or action.startswith("p_d"):
                return await show_result(session, level, exercises_page, page, session_number)
            return await training_days(session, level, training_program_id, page)

        elif level == 4:
            # Редактирование тренировочного дня
            return await edit_training_day(session, level, training_program_id, page, training_day_id, action)

        elif level == 5:
            if action in ["edit_excs", "shd/edit_excs", "to_edit", "shd/to_edit",
                          "del", "shd/del", "mv", "shd/mv"]:
                return await edit_exercises(session, level, exercise_id, training_day_id, page, action,
                                            training_program_id)
            else:
                return await show_categories(session, level, training_program_id, training_day_id, page, action,
                                             user_id, circle_training)

        elif level == 6:
            if action in ["ex_stg", "shd/ex_stg"] or action.startswith("➕") or action.startswith(
                    "➖") or action.startswith("shd/➕") or action.startswith("shd/➖"):
                return await exercise_settings(session, level, exercise_id, training_day_id, page, action,
                                               training_program_id)
            return await show_exercises_in_category(session, level, exercise_id, training_day_id, page, action,
                                                    training_program_id, category_id, user_id, empty, circle_training)

        elif level == 7:
            return await custom_exercises(session, level, training_day_id, page, action,
                                          training_program_id, category_id, user_id, empty, exercise_id,
                                          circle_training)

        else:
            logging.warning(f"Неизвестный уровень меню: {level}")
            return (InputMediaPhoto(media=globals.error_pic,
                                    caption="Ошибка: неизвестный уровень меню"),
                    error_btns())
    except Exception as e:
        logging.exception(f"Ошибка в get_menu_content: {e}")
        return (InputMediaPhoto(media=globals.error_pic,
                                caption="Ошибка при загрузке меню"),
                error_btns())
    finally:
        end_time = time.monotonic()
        duration = end_time - start_time
        logging.info(f"get_menu_content для action='{action}', level={level} заняла {duration:.2f} секунд")
