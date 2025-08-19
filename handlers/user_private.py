import asyncio
import logging
import time
from typing import List

from aiogram import F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, StateFilter, CommandStart, or_f
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import ReplyKeyboardRemove
from sqlalchemy.ext.asyncio import AsyncSession

from database.orm_query import (
    orm_add_user,
    orm_update_user,
    orm_add_program,
    orm_get_user_by_id,
    orm_get_programs,
    orm_turn_on_off_program,
    orm_add_training_day,
    orm_get_exercises,
    orm_add_user_exercise,
    orm_update_user_exercise,
    orm_get_user_exercise,
    orm_delete_exercise,
    move_exercise_up,
    move_exercise_down,
    orm_delete_program,
    orm_get_exercise,
    orm_update_exercise_set,
    orm_delete_exercise_set,
    orm_add_exercise_set,
    orm_get_exercise_set,
    orm_get_exercise_sets,
    orm_add_set,
    orm_get_sets_by_session,
    orm_update_program,
    orm_update_exercise,
    orm_get_categories,
    orm_delete_user_exercise,
    orm_add_training_session,
    orm_get_program, orm_get_exercise_max_weight,
    orm_get_sets_for_exercise_in_previous_session, )
from handlers.menu_processing import get_menu_content
from kbds.inline import MenuCallBack, get_url_btns, error_btns, get_callback_btns
from kbds.reply import get_keyboard
from utils.separator import get_action_part

user_private_router = Router()
user_private_router.message.filter(F.chat.type == "private")


class AddUser(StatesGroup):
    user_id = State()
    name = State()
    weight = State()

    user_for_change = None


class AddTrainingProgram(StatesGroup):
    name = State()
    user_id = State()

    trp_for_change = None


class TrainingProcess(StatesGroup):
    rest = State()
    circular_rest = State()
    training_day_id = State()
    exercise_index = State()
    set_index = State()
    reps = State()
    change_reps = State()
    weight = State()
    change_weight = State()
    accept_results = State()
    choose_change = State()
    circular_current_round = State()
    circular_exercise_index = State()
    current_circular_exercise_id = State()


class AddExercise(StatesGroup):
    name = State()
    description = State()
    category_id = State()
    program_id = State()
    training_day_id = State()
    image = State()
    user_id = State()

    exercise_for_change = None


async def send_error_message(message: types.Message, error: Exception):
    logging.exception(f"Произошла ошибка: {error}")
    btns = {"Написать разработчику": "https://t.me/cg_skbid"}
    await message.answer(
        "Произошла ошибка, попробуйте позже.",
        reply_markup=get_url_btns(btns=btns, sizes=(1,)),
    )


"""
Регистрация пользователя
"""


@user_private_router.message(StateFilter(None), CommandStart())
async def send_welcome(message: types.Message, state: FSMContext, session: AsyncSession):
    """
    Вызывается при отправке команды /start
    Проверяет, зарегистрирован пользователь в системе или нет
    Если нет, начинается процесс регистрации
    :param message:
    :param state:
    :param session:
    :return:
    """
    start_time = time.monotonic()
    user_id = message.from_user.id
    try:
        user = await orm_get_user_by_id(session, user_id)
        if user:
            await message.answer(f"Вы уже зарегистрированы как {user.name}.")
            media, reply_markup = await get_menu_content(session, level=0, action="main")
            await message.answer_photo(photo=media.media, caption=media.caption, reply_markup=reply_markup)
        else:
            await message.answer("Привет, я твой виртуальный тренер. Давай тебя зарегистрируем. Напиши свое имя:")
            await state.set_state(AddUser.name)
            await state.update_data(user_id=int(user_id))
    except Exception as e:
        logging.exception(f"Ошибка в send_welcome: {e}")
        btns = {"Написать разработчику": "https://t.me/cg_skbid"}
        await message.answer("Произошла ошибка, попробуйте позже.",
                             reply_markup=get_url_btns(btns=btns, sizes=(1,)))
    finally:
        duration = time.monotonic() - start_time
        logging.info(f"Обработка send_welcome заняла {duration:.2f} секунд")


@user_private_router.message(StateFilter(AddUser.name.state, AddUser.weight.state), Command("cancel"))
@user_private_router.message(StateFilter(AddUser.name.state, AddUser.weight.state), F.text.casefold() == "отмена")
async def cancel_registration(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Действия отменены\n\nЧтобы продолжить регистрацию, воспользуйтесь командой /start")


@user_private_router.message(AddUser.name, F.text)
async def add_name(message: types.Message, state: FSMContext):
    """
    Записываем имя пользователя в память и предлагаем ввести вес
    :param message:
    :param state:
    :return:
    """
    await state.update_data(name=message.text)
    await message.answer(f"Отлично, {message.text}. Введите ваш вес:")
    await state.set_state(AddUser.weight)


@user_private_router.message(AddUser.weight, F.text)
async def add_weight(message: types.Message, state: FSMContext, session: AsyncSession):
    """
    Сохраняем вес в памяти и делаем запись в базе данных. Пользователь зарегистрирован
    :param message:
    :param state:
    :param session:
    :return:
    """
    try:
        weight = float(message.text.replace(',', '.'))
        await state.update_data(weight=weight)
    except ValueError:
        await message.answer("Пожалуйста, введите корректное числовое значение для веса.")
        return

    data = await state.get_data()
    try:
        user_for_change = data.get('user_for_change')
        if user_for_change:
            await orm_update_user(session, user_for_change.id, data)
        else:
            await orm_add_user(session, data)

        await message.answer("Прекрасно, вы зарегистрированы в системе!\nДля навигации используйте интерактивное меню.")
        await state.clear()
        media, reply_markup = await get_menu_content(session, level=0, action="main")
        await message.answer_photo(photo=media.media, caption=media.caption, reply_markup=reply_markup)

    except Exception as e:
        logging.exception(f"Ошибка при добавлении пользователя: {e}")
        btns = {"Написать разработчику": "https://t.me/cg_skbid"}
        await message.answer("Произошла ошибка, попробуйте позже.",
                             reply_markup=get_url_btns(btns=btns, sizes=(1,)))
        await state.clear()


"""
Добавление тренировочной программы
"""


@user_private_router.callback_query(StateFilter(None), F.data == "adding_program")
async def ask_program_name(callback: types.CallbackQuery, state: FSMContext):
    """
    Получая callback в виде "adding_program", предлагаем пользователю ввести название программы тренировок
    :param callback:
    :param state:
    :return:
    """
    await callback.message.answer("Введите название программы тренировок:")
    await state.set_state(AddTrainingProgram.name)
    await callback.answer()


@user_private_router.message(StateFilter(AddTrainingProgram.name.state), Command("cancel"))
@user_private_router.message(StateFilter(AddTrainingProgram.name.state), F.text.casefold() == "отмена")
async def cancel_training_program(message: types.Message, state: FSMContext, session: AsyncSession):
    await state.clear()
    await message.answer("Действия отменены")
    media, reply_markup = await get_menu_content(session, level=1, action="program", user_id=message.from_user.id)
    await message.answer_photo(photo=media.media, caption=media.caption, reply_markup=reply_markup)


@user_private_router.message(AddTrainingProgram.name, F.text)
async def add_training_program_name(message: types.Message, state: FSMContext, session: AsyncSession):
    """
    Сохраняем имя в память и делаем запись в базу данных
    :param message:
    :param state:
    :param session:
    :return:
    """
    start_time = time.monotonic()
    user_id = message.from_user.id

    if len(message.text) > 15:
        await message.answer("Пожалуйста, введите название короче 15 символов:")
        return

    await state.update_data(user_id=user_id, name=message.text)
    data = await state.get_data()
    try:
        training_program_for_change = data.get('training_program_for_change')
        user_programs = await orm_get_programs(session, user_id)

        if training_program_for_change:
            await orm_update_program(session, training_program_for_change.id, data)
        else:
            await orm_add_program(session, data)
            user_programs = await orm_get_programs(session, user_id)
            await orm_turn_on_off_program(session, user_id, user_programs[-1].id)
        for day in ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]:
            await orm_add_training_day(session, day_of_week=day, program_id=user_programs[-1].id)

    except Exception as e:
        logging.exception(f"Ошибка при добавлении программы: {e}")
        btns = {"Написать разработчику": "https://t.me/cg_skbid"}
        await message.answer("Произошла ошибка, попробуйте позже.",
                             reply_markup=get_url_btns(btns=btns, sizes=(1,)))
        await state.clear()
        return

    await message.answer("Готово!")
    media, reply_markup = await get_menu_content(session, level=1, action="program", user_id=user_id)
    await message.answer_photo(photo=media.media, caption=media.caption, reply_markup=reply_markup)
    await state.clear()

    duration = time.monotonic() - start_time
    logging.info(f"Обработка add_training_program_name заняла {duration:.2f} секунд")


"""
Добавление пользовательского упражнения
"""


# Добавление нового упражнения пользователем
@user_private_router.callback_query(
    StateFilter(None),
    or_f(
        MenuCallBack.filter(F.action == "add_u_excs"),
        MenuCallBack.filter(F.action == "shd/add_u_excs"),
    )
)
async def add_exercise_callback_handler(
        callback: types.CallbackQuery,
        callback_data: MenuCallBack,
        state: FSMContext
):
    """
    При нажатии на соответствующую кнопку, предлагаем пользователю назвать свое упражнение
    :param callback:
    :param callback_data:
    :param state:
    :return:
    """
    training_day_id = callback_data.training_day_id
    program_id = callback_data.program_id
    category_id = callback_data.category_id
    empty = callback_data.empty
    await callback.message.answer("Введите название упражнения:", reply_markup=types.ReplyKeyboardRemove())
    await callback.answer()

    user_id = callback.from_user.id
    if callback_data.action == "add_u_excs":
        origin = "program_settings"
    elif callback_data.action == "shd/add_u_excs":
        origin = "schedule"
    else:
        origin = "unknown"

    await state.update_data(
        training_day_id=training_day_id,
        program_id=program_id,
        category_id=category_id,
        user_id=user_id,
        origin=origin,
        empty=empty,
        circle_training=callback_data.circle_training,
    )
    await state.set_state(AddExercise.name)


@user_private_router.callback_query(StateFilter(None), or_f(
    MenuCallBack.filter(F.action == "change_u_excs"),
    MenuCallBack.filter(F.action == "shd/change_u_excs")
))
async def change_exercise_callback(callback: types.CallbackQuery, callback_data: MenuCallBack, state: FSMContext,
                                   session: AsyncSession):
    """
    Предлагает пользователю изменить упражнение
    :param callback:
    :param callback_data:
    :param state:
    :param session:
    :return:
    """
    exercise_id = callback_data.exercise_id
    training_day_id = callback_data.training_day_id
    program_id = callback_data.program_id
    category_id = callback_data.category_id
    empty = callback_data.empty

    exercise_for_change = await orm_get_user_exercise(session, exercise_id)
    AddExercise.exercise_for_change = exercise_for_change

    await state.update_data(training_day_id=training_day_id, program_id=program_id, category_id=category_id)
    await callback.answer()
    await callback.message.answer("Введите название упражнения:", reply_markup=types.ReplyKeyboardRemove())
    await state.set_state(AddExercise.name)


async def get_origin(message: types.Message, state: FSMContext, data, session: AsyncSession):
    """
    Функция определяет источник начала действий пользователя и выдает правильный ответ
    :param message:
    :param state:
    :param data:
    :param session:
    :return:
    """
    origin = data.get('origin')
    if origin == "schedule":
        action = "shd/custom_excs"
        level = 7
    elif origin == "program_settings":
        level = 7
        action = "custom_excs"
    else:
        action = "main"
        level = 0
    media, reply_markup = await get_menu_content(
        session=session,
        level=level,
        action=action,
        page=1,
        training_day_id=data.get("training_day_id"),
        category_id=data.get("category_id"),
        training_program_id=data.get("program_id"),
        user_id=data.get("user_id"),
        empty=data.get("empty"),
        circle_training=data.get("circle_training")
    )
    await message.answer_photo(photo=media.media, caption=media.caption, reply_markup=reply_markup)
    await state.clear()


@user_private_router.message(
    StateFilter(AddExercise.name.state, AddExercise.category_id.state, AddExercise.description.state),
    Command("cancel"))
@user_private_router.message(
    StateFilter(AddExercise.name.state, AddExercise.category_id.state, AddExercise.description.state),
    F.text.casefold() == "отмена")
async def cancel_add_exercise(message: types.Message, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    await get_origin(message, state, data, session)


@user_private_router.message(AddExercise.name, F.text)
async def add_exercise_name(message: types.Message, state: FSMContext):
    """
    Сохраняем название упражнения, предлагаем пользователю ввести описание
    :param message:
    :param state:
    :return:
    """
    await state.update_data(name=message.text)
    await message.answer("Введите описание упражнения")
    await state.set_state(AddExercise.description)


@user_private_router.message(AddExercise.description)
async def add_exercise_description(
        message: types.Message,
        state: FSMContext,
        session: AsyncSession
):
    """
    Если добавляли упражнение в определенной категории, то сразу записываем его в базу
    Если добавляли упражнение в общем каталоге пользовательских упражнений,
    то предлагаем пользователю выбрать категорию
    :param message:
    :param state:
    :param session:
    :return:
    """
    await state.update_data(description=message.text)
    data = await state.get_data()
    try:
        if data.get("category_id"):
            if AddExercise.exercise_for_change:
                await orm_update_user_exercise(session, AddExercise.exercise_for_change.id, data)
            else:
                await orm_add_user_exercise(session, data)

            await message.answer("Упражнение добавлено/изменено")

            await get_origin(message, state, data, session)
        else:
            categories = await orm_get_categories(session, message.from_user.id)
            btns = {category.name: str(category.id) for category, _ in categories}
            await message.answer("Выберите категорию", reply_markup=get_callback_btns(btns=btns))
            await state.set_state(AddExercise.category_id)
    except Exception as e:
        logging.exception(f"Ошибка при добавлении упражнения: {e}")
        btns = {"Написать разработчику": "https://t.me/cg_skbid"}
        await message.answer(
            f"Ошибка: \n{str(e)}\nОбратитесь к администратору.",
            reply_markup=get_url_btns(btns=btns, sizes=(1,))
        )
        await state.clear()


@user_private_router.callback_query(AddExercise.category_id)
async def category_choice(callback: types.CallbackQuery, state: FSMContext,
                          session: AsyncSession):
    """
    Предлагаем выбрать категорию(группу мышц) для упражнения
    :param callback:
    :param state:
    :param session:
    :return:
    """
    categories = await orm_get_categories(session, callback.from_user.id)
    category_ids = [category.id for category, _ in categories]
    if int(callback.data) in category_ids:
        await callback.answer()
        await state.update_data(category_id=int(callback.data))
        data = await state.get_data()
        if AddExercise.exercise_for_change:
            await orm_update_user_exercise(session, AddExercise.exercise_for_change.id, data)
        else:
            await orm_add_user_exercise(session, data)

        await callback.message.answer("Упражнение добавлено/изменено")

        await get_origin(callback.message, state, data, session)
    else:
        await callback.message.answer('Выберите категорию из кнопок.')
        await callback.answer()


@user_private_router.message(AddExercise.category_id)
async def category_choice2(message: types.Message):
    await message.answer("'Выберите категорию из кнопок.'")


async def clicked_btn(callback_data: MenuCallBack, state: FSMContext, selected_id, clicked_id,
                      callback: types.CallbackQuery,
                      session: AsyncSession):
    """
    Определяет: нажал ли пользователь на кнопку или нет
    :param callback_data:
    :param state:
    :param selected_id:
    :param clicked_id:
    :param callback:
    :param session:
    :return:
    """
    new_selected_id = None if selected_id == clicked_id else clicked_id

    if get_action_part(callback_data.action) == "to_edit":
        await state.update_data(selected_exercise_id=new_selected_id)
    elif get_action_part(callback_data.action) == "to_del_prgm":
        await state.update_data(selected_program_id=new_selected_id)

    media, reply_markup = await get_menu_content(
        session,
        level=callback_data.level,
        action=callback_data.action,
        training_program_id=callback_data.program_id,
        exercise_id=new_selected_id,
        page=callback_data.page,
        training_day_id=callback_data.training_day_id,
        user_id=callback.from_user.id,
        category_id=callback_data.category_id,
        empty=callback_data.empty,
        circle_training=callback_data.circle_training,
    )

    try:
        await callback.message.edit_media(media=media, reply_markup=reply_markup)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            pass
        else:
            logging.warning(f"Ошибка при edit_media: {e}")

    await callback.answer()


@user_private_router.callback_query(MenuCallBack.filter())
async def user_menu(callback: types.CallbackQuery, callback_data: MenuCallBack, session: AsyncSession,
                    state: FSMContext):
    """
    Функция получает на вход данные и выводит информацию пользователю
    :param callback:
    :param callback_data:
    :param session:
    :param state:
    :return:
    """
    start_time = time.monotonic()
    try:
        action = callback_data.action
        logging.info(f"Получен callback от пользователя {callback.from_user.id}: {callback_data}")

        user_data = await state.get_data()
        selected_exercise_id = user_data.get("selected_exercise_id")
        selected_program_id = user_data.get("selected_program_id")

        if get_action_part(action) == "to_edit":
            await clicked_btn(session=session, callback_data=callback_data, state=state,
                              selected_id=selected_exercise_id, callback=callback,
                              clicked_id=callback_data.exercise_id)

        elif get_action_part(action).startswith("del"):
            if get_action_part(action).__contains__("custom"):
                await orm_delete_user_exercise(session, callback_data.exercise_id)
            else:
                await orm_delete_exercise(session, callback_data.exercise_id)
            await state.update_data(selected_exercise_id=None)

            media, reply_markup = await get_menu_content(
                session,
                level=callback_data.level,
                action="to_edit",
                training_program_id=callback_data.program_id,
                exercise_id=None,
                page=callback_data.page,
                training_day_id=callback_data.training_day_id,
                user_id=callback.from_user.id,
                category_id=callback_data.category_id,
                year=callback_data.year,
                month=callback_data.month,
                set_id=callback_data.set_id,
                empty=callback_data.empty,
                circle_training=callback_data.circle_training,
                session_number=callback_data.session_number,
                exercises_page=callback_data.exercises_page,
            )

            if action.startswith("shd/"):
                media, reply_markup = await get_menu_content(
                    session,
                    level=callback_data.level,
                    action="shd/to_edit",
                    training_program_id=callback_data.program_id,
                    exercise_id=None,
                    page=callback_data.page,
                    training_day_id=callback_data.training_day_id,
                    user_id=callback.from_user.id,
                    category_id=callback_data.category_id,
                    year=callback_data.year,
                    month=callback_data.month,
                    set_id=callback_data.set_id,
                    empty=callback_data.empty,
                    circle_training=callback_data.circle_training,
                    session_number=callback_data.session_number,
                    exercises_page=callback_data.exercises_page,
                )

            try:
                await callback.message.edit_media(media=media, reply_markup=reply_markup)
            except TelegramBadRequest as e:
                if "message is not modified" in str(e):
                    pass
                else:
                    logging.warning(f"Ошибка при edit_media: {e}")

            await callback.answer("Упражнение удалено.")

        elif get_action_part(action).startswith("mv"):
            if get_action_part(action) == "mv_up":
                await callback.answer(await move_exercise_up(session, callback_data.exercise_id))
            elif get_action_part(action) == "mv_down":
                await callback.answer(await move_exercise_down(session, callback_data.exercise_id))

            media, reply_markup = await get_menu_content(
                session,
                level=callback_data.level,
                action="to_edit",
                training_program_id=callback_data.program_id,
                exercise_id=callback_data.exercise_id,
                page=callback_data.page,
                training_day_id=callback_data.training_day_id,
                user_id=callback.from_user.id,
                category_id=callback_data.category_id,
                year=callback_data.year,
                month=callback_data.month,
                set_id=callback_data.set_id,
                empty=callback_data.empty,
                circle_training=callback_data.circle_training,
                session_number=callback_data.session_number,
                exercises_page=callback_data.exercises_page,
            )

            if action.startswith("shd/"):
                media, reply_markup = await get_menu_content(
                    session,
                    level=callback_data.level,
                    action="shd/to_edit",
                    training_program_id=callback_data.program_id,
                    exercise_id=callback_data.exercise_id,
                    page=callback_data.page,
                    training_day_id=callback_data.training_day_id,
                    user_id=callback.from_user.id,
                    category_id=callback_data.category_id,
                    year=callback_data.year,
                    month=callback_data.month,
                    set_id=callback_data.set_id,
                    empty=callback_data.empty,
                    circle_training=callback_data.circle_training,
                    session_number=callback_data.session_number,
                    exercises_page=callback_data.exercises_page,
                )
            try:
                await callback.message.edit_media(media=media, reply_markup=reply_markup)
            except TelegramBadRequest as e:
                if "message is not modified" in str(e):
                    pass
                else:
                    logging.warning(f"Ошибка при edit_media: {e}")

        elif get_action_part(action) == "to_del_prgm":
            await clicked_btn(session=session, callback_data=callback_data, state=state,
                              selected_id=selected_program_id, callback=callback, clicked_id=callback_data.program_id)

        elif get_action_part(action) == "prgm_del":
            await orm_delete_program(session, callback_data.program_id)
            await state.update_data(selected_program_id=None)

            media, reply_markup = await get_menu_content(
                session,
                level=callback_data.level,
                action="program",
                training_program_id=None,
                exercise_id=None,
                page=callback_data.page,
                training_day_id=callback_data.training_day_id,
                user_id=callback.from_user.id,
                category_id=callback_data.category_id,
                year=callback_data.year,
                month=callback_data.month,
                set_id=callback_data.set_id,
                empty=callback_data.empty,
                circle_training=callback_data.circle_training,
                session_number=callback_data.session_number,
                exercises_page=callback_data.exercises_page,
            )
            try:
                await callback.message.edit_media(media=media, reply_markup=reply_markup)
            except TelegramBadRequest as e:
                if "message is not modified" in str(e):
                    pass
                else:
                    logging.warning(f"Ошибка при edit_media: {e}")

            await callback.answer("Программа удалена.")

        elif get_action_part(action).startswith("➕") or get_action_part(action).startswith("➖"):
            parts = get_action_part(action).split("_")
            operation = parts[0]  # "➕" или "➖"
            increment = int(parts[1])
            field = parts[2]
            set_id = callback_data.set_id

            user_exercise = await orm_get_exercise(session, callback_data.exercise_id)
            user_exercise_set = await orm_get_exercise_set(session, set_id)

            if field == "reps":
                new_reps = (
                    user_exercise_set.reps + increment
                    if operation == "➕"
                    else max(1, user_exercise_set.reps - increment)
                )
                await orm_update_exercise_set(session, set_id, new_reps)

            elif field == "sets":
                new_sets = (
                    user_exercise.base_sets + increment
                    if operation == "➕"
                    else max(1, user_exercise.base_sets - increment)
                )
                await orm_update_exercise(session, callback_data.exercise_id, {"sets": new_sets})

                # if operation == "➕":
                #     await orm_add_exercise_set(session, callback_data.exercise_id, user_exercise.base_reps)
                # elif operation == "➖":
                #     exercise_sets = await orm_get_exercise_sets(session, callback_data.exercise_id)
                #     if len(exercise_sets) > 1:
                #         last_set_id = exercise_sets[-1].id
                #         await orm_delete_exercise_set(session, last_set_id)

            media, reply_markup = await get_menu_content(
                session,
                level=callback_data.level,
                action=f"{operation}_{field}",
                training_program_id=callback_data.program_id,
                exercise_id=callback_data.exercise_id,
                page=callback_data.page,
                training_day_id=callback_data.training_day_id,
                user_id=callback.from_user.id,
                category_id=callback_data.category_id,
                year=callback_data.year,
                month=callback_data.month,
                set_id=callback_data.set_id,
                empty=callback_data.empty,
                circle_training=callback_data.circle_training,
                session_number=callback_data.session_number,
                exercises_page=callback_data.exercises_page,
            )
            if action.startswith("shd/"):
                media, reply_markup = await get_menu_content(
                    session,
                    level=callback_data.level,
                    action=f"shd/{operation}_{field}",
                    training_program_id=callback_data.program_id,
                    exercise_id=callback_data.exercise_id,
                    page=callback_data.page,
                    training_day_id=callback_data.training_day_id,
                    user_id=callback.from_user.id,
                    category_id=callback_data.category_id,
                    year=callback_data.year,
                    month=callback_data.month,
                    set_id=callback_data.set_id,
                    circle_training=callback_data.circle_training,
                    session_number=callback_data.session_number,
                    exercises_page=callback_data.exercises_page,
                )
            try:
                await callback.message.edit_media(media=media, reply_markup=reply_markup)
            except TelegramBadRequest as e:
                if "message is not modified" in str(e):
                    pass
                else:
                    logging.warning(f"Ошибка при edit_media: {e}")

            await callback.answer()

        elif get_action_part(action) == "training_process":
            media, reply_markup = await get_menu_content(
                session,
                level=callback_data.level,
                action=action,
                training_program_id=callback_data.program_id,
                exercise_id=callback_data.exercise_id,
                page=callback_data.page,
                training_day_id=callback_data.training_day_id,
                user_id=callback.from_user.id,
                category_id=callback_data.category_id,
                year=callback_data.year,
                month=callback_data.month,
                set_id=callback_data.set_id,
                empty=callback_data.empty,
                session_number=callback_data.session_number,
                exercises_page=callback_data.exercises_page,
            )
            try:
                await callback.message.edit_media(media=media, reply_markup=reply_markup)
            except TelegramBadRequest as e:
                if "message is not modified" in str(e):
                    pass
                else:
                    logging.warning(f"Ошибка при edit_media: {e}")

            await callback.answer()
            await handle_start_training_process(callback, callback_data, state, session)

        elif get_action_part(action) == "finish_training":
            try:
                await callback.answer()
                media, reply_markup = await get_menu_content(
                    session,
                    level=callback_data.level,
                    action=action,
                    training_program_id=callback_data.program_id,
                    exercise_id=callback_data.exercise_id,
                    page=callback_data.page,
                    training_day_id=callback_data.training_day_id,
                    user_id=callback.from_user.id,
                    category_id=callback_data.category_id,
                    year=callback_data.year,
                    month=callback_data.month,
                    set_id=callback_data.set_id,
                    empty=callback_data.empty,
                    circle_training=callback_data.circle_training,
                    session_number=callback_data.session_number,
                    exercises_page=callback_data.exercises_page,
                )
                try:
                    await callback.message.edit_media(media=media, reply_markup=reply_markup)
                except TelegramBadRequest as e:
                    if "message is not modified" in str(e):
                        pass
                    else:
                        logging.warning(f"Ошибка при edit_media: {e}")

                user_data = await state.get_data()
                bot_message_id = user_data.get('bot_message_id')
                if bot_message_id:
                    try:
                        await callback.message.bot.delete_message(chat_id=callback.message.chat.id,
                                                                  message_id=bot_message_id)
                    except TelegramBadRequest as e:
                        if "message to delete not found" in str(e):
                            pass
                        else:
                            logging.warning(f"Не удалось удалить сообщение бота: {e}")
                rest_message_id = user_data.get("rest_message_id")
                if rest_message_id:
                    try:
                        await callback.message.bot.delete_message(chat_id=callback.message.chat.id,
                                                                  message_id=rest_message_id)
                    except TelegramBadRequest as e:
                        if "message to delete not found" not in str(e):
                            logging.warning(f"Не удалось удалить сообщение об отдыхе: {e}")
                await state.clear()
                await state.update_data(rest_ended=True)
            except Exception as e:
                logging.warning(f"Не удалось удалить сообщение бота: {e}")

        else:
            # Все остальные действия — просто обновляем меню
            media, reply_markup = await get_menu_content(
                session,
                level=callback_data.level,
                action=action,
                training_program_id=callback_data.program_id,
                exercise_id=callback_data.exercise_id,
                page=callback_data.page,
                training_day_id=callback_data.training_day_id,
                user_id=callback.from_user.id,
                category_id=callback_data.category_id,
                year=callback_data.year,
                month=callback_data.month,
                set_id=callback_data.set_id,
                empty=callback_data.empty,
                circle_training=callback_data.circle_training,
                session_number=callback_data.session_number,
                exercises_page=callback_data.exercises_page,
            )
            await state.update_data(selected_exercise_id=None, selected_program_id=None)
            try:
                await callback.message.edit_media(media=media, reply_markup=reply_markup)
            except TelegramBadRequest as e:
                if "message is not modified" in str(e):
                    pass
                else:
                    logging.warning(f"Ошибка при edit_media: {e}")
            await callback.answer()

    except Exception as e:
        if "message is not modified" in str(e):
            pass  # Игнорируем
        else:
            await callback.message.answer(f"Произошла ошибка, попробуйте позже. {e}",
                                          reply_markup=error_btns())
    finally:
        duration = time.monotonic() - start_time
        logging.info(f"Обработка user_menu заняла {duration:.2f} секунд")


"""
Тренировочный процесс
"""


@user_private_router.callback_query(MenuCallBack.filter())
async def handle_start_training_process(
        callback: types.CallbackQuery,
        callback_data: MenuCallBack,
        state: FSMContext,
        session: AsyncSession,
):
    """
    Инициализирует процесс тренировки
    :param callback:
    :param callback_data:
    :param state:
    :param session:
    :return:
    """
    user_id = callback.from_user.id

    try:
        new_session = await orm_add_training_session(
            session,
            {
                "user_id": user_id,
                "note": "Запуск тренировки",
            },
        )

        user = await orm_get_user_by_id(session, user_id)
        if not user:
            await callback.message.answer("Пользователь не найден.")
            await state.clear()
            return

        training_program = await orm_get_program(session, user.actual_program_id)
        if not training_program:
            await callback.message.answer("Тренировочная программа не найдена.")
            await state.clear()
            return

        circular_rounds = training_program.circular_rounds
        rest_between_exercise = training_program.rest_between_exercise
        circular_rest_between_rounds = training_program.circular_rest_between_rounds
        circular_rest_between_exercise = training_program.circular_rest_between_exercise
        rest_between_set = training_program.rest_between_set

        training_session_id = str(new_session.id)
        await state.set_state(TrainingProcess.exercise_index)
        await state.update_data(
            training_session_id=training_session_id,
            exercise_index=0,
            set_index=1,
            training_day_id=callback_data.training_day_id,
            circular_rounds=circular_rounds,
            rest_between_exercise=rest_between_exercise,
            rest_between_set=rest_between_set,
            circular_rest_between_rounds=circular_rest_between_rounds,
            circular_rest_between_exercise=circular_rest_between_exercise,
            rest_ended=False,
            user_id=user_id,
        )

        exercises = await orm_get_exercises(session, callback_data.training_day_id)
        if not exercises:
            await callback.answer("Нет упражнений в этом тренировочном дне.")
            await state.clear()
            return

        blocks = group_exercises_into_blocks(exercises)
        if not blocks:
            await callback.answer("Нет упражнений.")
            await state.clear()
            return

        await state.update_data(blocks=[[ex.id for ex in block] for block in blocks], block_index=0)

        bot_msg = await callback.message.answer("Подготовка к тренировке...")
        await asyncio.sleep(1)
        await state.update_data(bot_message_id=bot_msg.message_id)

        await process_current_block(callback.message, state, session)
        await callback.answer()
    except Exception as e:
        await send_error_message(callback.message, e)
        await state.clear()


def group_exercises_into_blocks(exercises: List) -> List[List]:
    """
    Группирует упражнения на блоки с обычными и круговыми упражнениями
    :param exercises:
    :return:
    """
    blocks = []
    i = 0
    while i < len(exercises):
        ex = exercises[i]
        if ex.circle_training:
            c_block = [ex]
            i += 1
            while i < len(exercises) and exercises[i].circle_training:
                c_block.append(exercises[i])
                i += 1
            blocks.append(c_block)
        else:
            s_block = [ex]
            i += 1
            while i < len(exercises) and not exercises[i].circle_training:
                s_block.append(exercises[i])
                i += 1
            blocks.append(s_block)
    return blocks


async def process_current_block(
        message: types.Message, state: FSMContext, session: AsyncSession
):
    """
    Активирует соответствующий блок упражнений
    """
    data = await state.get_data()
    blocks = data.get("blocks", [])
    block_index = data.get("block_index", 0)

    if block_index >= len(blocks):
        await finish_training(message, state, session)
        return

    ex_ids = blocks[block_index]
    ex_objs = []
    is_circuit = True

    for ex_id in ex_ids:
        ex_obj = await orm_get_exercise(session, ex_id)
        if not ex_obj:
            logging.warning(f"Exercise ID {ex_id} not found.")
            continue
        ex_objs.append(ex_obj)
        if not ex_obj.circle_training:
            is_circuit = False

    if is_circuit:
        await state.update_data(
            standard_ex_ids=None,
            standard_ex_idx=None,
            set_index=None,
            circuit_ex_ids=[ex.id for ex in ex_objs],
            circuit_ex_idx=0,
            circuit_round=1,
        )
        logging.info(f"go to circuit")
        await start_circuit_block(message, state, session, ex_objs)
    else:
        await state.update_data(
            circuit_ex_ids=None,
            circuit_ex_idx=None,
            circuit_round=None,
            standard_ex_ids=[ex.id for ex in ex_objs],
            standard_ex_idx=0,
            set_index=1,
        )
        logging.info(f"go to standart")
        await start_standard_block(message, state, session, ex_objs)


async def first_result_message(session: AsyncSession, user_id, next_ex):
    set_list = await orm_get_sets_for_exercise_in_previous_session(
        session,
        next_ex.id,
        current_session_id=None  # Текущая сессия ещё не создана
    )
    prev_sets = ""
    if len(set_list) > next_ex.base_sets:
        set_list = set_list[-next_ex.base_sets:]
    if set_list:
        for i in range(next_ex.base_sets):
            prev_sets += f"----------------------------------------\n"
            if len(set_list) > i:
                prev_sets += (
                    f"<strong>{set_list[i].updated.strftime('%d-%m')}"
                    f" 🦾: {set_list[i].weight} кг/блок,"
                    f" 🧮: {set_list[i].repetitions} раз\n</strong>"
                )
            else:
                prev_sets += f"<strong>Подход {i + 1}: еще не выполнен\n</strong>"

    if prev_sets == "":
        prev_sets = "----------------------------------------\n<strong>Результаты не обнаружены</strong>\n"

    max_weight = await orm_get_exercise_max_weight(session, user_id, next_ex.id)
    text = (
        f"Упражнение: <strong>{next_ex.name}</strong>\n\n"
        f"Рекорд поднятого веса:\n<strong>{int(max_weight)} кг/блок за подход</strong>\n\n"
        f"Результаты прошлой тренировки:\n{prev_sets}"
        f"----------------------------------------\n\n"
        f"Подход <strong>1 из {next_ex.base_sets}</strong> \nВведите вес снаряда:"
    )
    logging.info(f"frm ex id: {next_ex.name}")
    return text


async def result_message_after_set(session: AsyncSession, user_id, next_ex, set_index, session_id):
    current_sets = await orm_get_sets_by_session(session, next_ex.id, session_id)  # получаем данные текущей тренировки
    set_list = await orm_get_sets_for_exercise_in_previous_session(
        session,
        next_ex.id,
        current_session_id=session_id  # Исключаем текущую сессию из поиска
    )
    if len(set_list) > next_ex.base_sets:
        set_list = set_list[-next_ex.base_sets:]
    prev_sets = ""
    if set_list:
        for i in range(next_ex.base_sets):
            flag = False
            prev_sets += f"----------------------------------------\n"
            if len(set_list) > i:
                prev_sets += (
                    f"{set_list[i].updated.strftime('%d-%m')}"
                    f" 🦾: {set_list[i].weight} кг/блок,"
                    f" 🧮: {set_list[i].repetitions} раз\n"
                )
            elif len(current_sets) > len(set_list) and len(current_sets) > i:
                prev_sets += (f"<strong>Подход {i + 1} 👇\n"
                              f"🦾: {current_sets[i].weight} кг/блок\n"
                              f"🧮: {current_sets[i].repetitions} повтр.\n</strong>")
                flag = True

            else:
                prev_sets += f"<strong>Подход {i + 1}: еще не выполнен\n</strong>"
                flag = True
            if len(current_sets) > i and flag is False:
                if current_sets[i].weight > set_list[i].weight:
                    weight_factor = f"💹+{current_sets[i].weight - set_list[i].weight:.1f}"
                elif current_sets[i].weight == set_list[i].weight:
                    weight_factor = "👌"
                else:
                    weight_factor = f"📉{current_sets[i].weight - set_list[i].weight:.1f}"

                if current_sets[i].repetitions > set_list[i].repetitions:
                    reps_factor = f"💹+{current_sets[i].repetitions - set_list[i].repetitions}"
                elif current_sets[i].repetitions == set_list[i].repetitions:
                    reps_factor = "👌"
                else:
                    reps_factor = f"📉{current_sets[i].repetitions - set_list[i].repetitions}"
                prev_sets += (f"<strong>Подход {i + 1} 👇\n"
                              f"🦾: {current_sets[i].weight} кг/блок {weight_factor}\n"
                              f"🧮: {current_sets[i].repetitions} повтр. {reps_factor}\n</strong>")

    else:
        prev_sets = "----------------------------------------\n<strong>Результаты не обнаружены</strong>\n"
    max_weight = await orm_get_exercise_max_weight(session, user_id, next_ex.id)
    text = (
        f"Упражнение: <strong>{next_ex.name}</strong>\n\n"
        f"Рекорд поднятого веса:\n<strong>{int(max_weight)} кг/блок за подход</strong>\n\n"
        f"Результаты прошлой тренировки:\n{prev_sets}"
        f"----------------------------------------\n"
        f"Подход <strong>{set_index} из {next_ex.base_sets}</strong> \nВведите вес снаряда:"
    )
    logging.info(f"rmas ex id: {next_ex.name}, session_id: {session_id}")
    return text


async def start_standard_block(
        message: types.Message, state: FSMContext, session: AsyncSession, ex_objs: List
):
    """
    Старт блока стандартных упражнений
    """
    data = await state.get_data()
    bot_msg_id = data.get("bot_message_id")
    user_id = data.get("user_id")
    if not ex_objs:
        await message.answer("Нет упражнений в этом блоке.")
        await move_to_next_block_in_day(message, state, session)
        return

    current_ex = ex_objs[0]
    text = await first_result_message(session, user_id, current_ex)

    try:
        await message.bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=bot_msg_id,
            text=text,
        )
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            pass
        else:
            logging.warning(f"Ошибка при edit_message_text: {e}")

    current_sets = {"weight": [], "repetitions": []}
    await state.update_data(current_exercise_id=current_ex.id, current_sets=current_sets)
    await state.set_state(TrainingProcess.weight)


async def process_standard_after_set(
        message: types.Message, state: FSMContext, session: AsyncSession
):
    """
    Продолжение блока стандартных упражнений
    """
    data = await state.get_data()
    bot_msg_id = data["bot_message_id"]
    ex_id = data["current_exercise_id"]
    set_index = data.get("set_index", 1)
    standard_ex_ids = data.get("standard_ex_ids", [])
    standard_ex_idx = data.get("standard_ex_idx", 0)
    rest_between_set = data.get("rest_between_set")
    session_id = data.get("training_session_id")
    user_id = data.get("user_id")

    ex_obj = await orm_get_exercise(session, ex_id)
    total_sets = ex_obj.base_sets if ex_obj else 3
    if set_index < total_sets:
        set_index += 1
        await state.update_data(set_index=set_index)
        rest_text = (f"Подход <strong>{set_index - 1}</strong> завершён! Отдых <strong>{rest_between_set // 60}"
                     f"</strong> мин...")

        try:
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=bot_msg_id,
                text=rest_text,
            )
        except TelegramBadRequest as e:
            if "message is not modified" in str(e):
                pass
            else:
                logging.warning(f"Ошибка при edit_message_text: {e}")

        await state.set_state(TrainingProcess.circular_rest)

        await asyncio.create_task(
            handle_rest_period(message, state, rest_between_set))

        text = await result_message_after_set(session, user_id, ex_obj, set_index, session_id)

        try:
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=bot_msg_id,
                text=text,
            )
        except TelegramBadRequest as e:
            if "message is not modified" in str(e):
                pass
            else:
                logging.warning(f"Ошибка при edit_message_text: {e}")
        await state.set_state(TrainingProcess.weight)

    else:
        standard_ex_idx += 1
        if standard_ex_idx < len(standard_ex_ids):
            await state.update_data(standard_ex_idx=standard_ex_idx, set_index=1)
            next_ex_id = standard_ex_ids[standard_ex_idx]
            next_ex = await orm_get_exercise(session, next_ex_id)
            if not next_ex:
                await message.answer("Следующее упражнение не найдено.")
                await move_to_next_block_in_day(message, state, session)
                return
            await state.update_data(current_exercise_id=next_ex.id)
            text = await first_result_message(session, user_id, next_ex)
            try:
                await message.bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=bot_msg_id,
                    text=text,
                )
            except TelegramBadRequest as e:
                if "message is not modified" in str(e):
                    pass
                else:
                    logging.warning(f"Ошибка при edit_message_text: {e}")

            await state.set_state(TrainingProcess.weight)
        else:
            await move_to_next_block_in_day(message, state, session)


async def start_circuit_block(
        message: types.Message, state: FSMContext, session: AsyncSession, ex_objs: List
):
    """
    Начало кругового блока упражнений
    """
    data = await state.get_data()
    bot_msg_id = data.get("bot_message_id")
    user_id = data.get("user_id")
    if not ex_objs:
        await message.answer("Нет упражнений в этом блоке.")
        await move_to_next_block_in_day(message, state, session)
        return

    current_ex = ex_objs[0]
    text = await first_result_message(session, user_id, current_ex)

    try:
        await message.bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=bot_msg_id,
            text=text,
        )
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            pass
        else:
            logging.warning(f"Ошибка при edit_message_text: {e}")

    await state.update_data(current_exercise_id=current_ex.id)
    await state.set_state(TrainingProcess.weight)


async def process_circuit_after_set(
        message: types.Message,
        state: FSMContext,
        session: AsyncSession
):
    """
    Продолжение кругового блока упражнений
    """

    data = await state.get_data()
    bot_msg_id = data["bot_message_id"]
    c_ex_ids = data["circuit_ex_ids"]
    c_idx = data.get("circuit_ex_idx")
    c_round = data.get("circuit_round")
    circular_rounds = data.get("circular_rounds")
    circular_rest_between_rounds = data.get("circular_rest_between_rounds")
    circular_rest_between_exercise = data.get("circular_rest_between_exercise")
    session_id = data.get("training_session_id")
    c_idx += 1
    user_id = data.get("user_id")
    if c_idx < len(c_ex_ids):
        if circular_rest_between_exercise > 0:
            rest_text = (
                f"Отдых <strong>{circular_rest_between_exercise}</strong> сек. перед следующим упражнением..."
            )
            try:
                await message.bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=bot_msg_id,
                    text=rest_text,
                )
            except TelegramBadRequest as e:
                if "message is not modified" in str(e):
                    pass
                else:
                    logging.warning(f"Ошибка при edit_message_text: {e}")

            await state.set_state(TrainingProcess.circular_rest)
            await asyncio.create_task(
                handle_rest_period(message, state, circular_rest_between_exercise)
            )

        await state.update_data(circuit_ex_idx=c_idx)
        next_ex_id = c_ex_ids[c_idx]
        next_ex = await orm_get_exercise(session, next_ex_id)
        if not next_ex:
            await message.answer("Следующее упражнение не найдено.")
            await move_to_next_block_in_day(message, state, session)
            return
        await state.update_data(current_exercise_id=next_ex_id)
        text = await result_message_after_set(session, user_id, next_ex, c_round, session_id)
        try:
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=bot_msg_id,
                text=text,
            )
        except TelegramBadRequest as e:
            if "message is not modified" in str(e):
                pass
            else:
                logging.warning(f"Ошибка при edit_message_text: {e}")
        await state.set_state(TrainingProcess.weight)

    else:
        if c_round < circular_rounds:
            c_round += 1
            await state.update_data(circuit_round=c_round)
            rest_text = (
                f"Круг <strong>{c_round - 1}</strong> завершён! Отдых <strong>{circular_rest_between_rounds // 60}"
                f"</strong> мин..."
            )
            try:
                await message.bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=bot_msg_id,
                    text=rest_text,
                )
            except TelegramBadRequest as e:
                if "message is not modified" in str(e):
                    pass
                else:
                    logging.warning(f"Ошибка при edit_message_text: {e}")

            await state.set_state(TrainingProcess.circular_rest)
            await asyncio.create_task(
                handle_rest_period(message, state, circular_rest_between_rounds)
            )
            c_idx = 0
            next_ex_id = c_ex_ids[c_idx]
            await state.update_data(circuit_ex_idx=c_idx)
            next_ex = await orm_get_exercise(session, next_ex_id)
            if not next_ex:
                await message.answer("Следующее упражнение не найдено.")
                await move_to_next_block_in_day(message, state, session)
                return
            await state.update_data(current_exercise_id=next_ex.id)

            text = await result_message_after_set(session, user_id, next_ex, c_round, session_id)
            try:
                await message.bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=bot_msg_id,
                    text=text,
                )
            except TelegramBadRequest as e:
                if "message is not modified" in str(e):
                    pass
                else:
                    logging.warning(f"Ошибка при edit_message_text: {e}")
            await state.set_state(TrainingProcess.weight)
        else:
            await move_to_next_block_in_day(message, state, session)


async def handle_rest_period(
        message: types.Message,
        state: FSMContext,
        rest_duration: int
):
    """
    Начинает процесс отдыха между подходами и кругами
    :param message:
    :param state:
    :param rest_duration:
    :return:
    """
    data = await state.get_data()
    if "rest_ended" not in data:
        await state.update_data(rest_ended=False)

    time_left = rest_duration
    sleep_step = 1

    while time_left > 0:
        data = await state.get_data()
        if data.get("rest_ended", False):
            break

        rest_message_id = data.get("rest_message_id")
        if rest_message_id:
            try:
                await message.bot.delete_message(chat_id=message.chat.id, message_id=rest_message_id)
            except TelegramBadRequest as e:
                if "message to delete not found" not in str(e):
                    logging.warning(f"Не удалось удалить сообщение об отдыхе: {e}")

        minutes = time_left // 60
        if minutes > 1:
            new_rest_text = f"Отдыхайте еще <strong>{minutes}</strong> мин..."
        elif minutes == 1:
            new_rest_text = "Отдыхайте еще <strong>1</strong> минуту..."
        else:
            new_rest_text = "Отдых завершен!"
        try:
            rest_msg = await message.answer(new_rest_text, reply_markup=get_keyboard("🏄‍♂️ Закончить отдых"))
            await state.update_data(rest_message_id=rest_msg.message_id)
        except TelegramBadRequest as e:
            logging.warning(f"Ошибка при отправке rest-сообщения: {e}")

        chunk = min(60, time_left)
        slept = 0
        while slept < chunk:
            await asyncio.sleep(sleep_step)
            slept += sleep_step
            data = await state.get_data()
            if data.get("rest_ended", False):
                break

        if data.get("rest_ended", False):
            break

        time_left -= chunk

    data = await state.get_data()
    await state.update_data(rest_ended=False)
    rest_message_id = data.get("rest_message_id")
    if rest_message_id:
        try:
            await message.bot.delete_message(chat_id=message.chat.id, message_id=rest_message_id)
        except TelegramBadRequest as e:
            if "message to delete not found" not in str(e):
                logging.warning(f"Не удалось удалить последнее сообщение об отдыхе: {e}")

    if data.get("rest_ended", False):
        return
    end_rest_message = await message.answer("Отдых завершен!\n\nАвтоудаление через 5 секунд",
                                            reply_markup=ReplyKeyboardRemove())
    await asyncio.sleep(5)
    if end_rest_message:
        try:
            await end_rest_message.delete()
        except TelegramBadRequest as e:
            if "message to delete not found" not in str(e):
                logging.warning(f"Не удалось удалить последнее сообщение об отдыхе: {e}")


@user_private_router.message(StateFilter(TrainingProcess.rest.state, TrainingProcess.circular_rest.state))
async def handle_rest_messages(message: types.Message, state: FSMContext):
    """
    Обработка сообщений во время и окончания отдыха
    :param message:
    :param state:
    :return:
    """
    if message.text == "🏄‍♂️ Закончить отдых":
        await state.update_data(rest_ended=True)
        data = await state.get_data()
        rest_message_id = data.get("rest_message_id")
        if rest_message_id:
            try:
                await message.bot.delete_message(chat_id=message.chat.id, message_id=rest_message_id)
            except TelegramBadRequest as e:
                if "message to delete not found" not in str(e):
                    logging.warning(f"Не удалось удалить последнее сообщение об отдыхе: {e}")

        end_message = await message.answer(
            "Отдых закончен!\n\nАвтоудаление через 5 секунд",
            reply_markup=ReplyKeyboardRemove()
        )
        await message.delete()
        await asyncio.sleep(5)
        await end_message.delete()
        await state.update_data(rest_ended=False)

    else:
        message_rest = await message.reply(
            "Пожалуйста, дождитесь окончания отдыха.\n\nАвтоудаление сообщения через 5 секунд..."
        )
        await asyncio.sleep(5)
        await message.delete()
        await message_rest.delete()


async def move_to_next_block_in_day(
        message: types.Message, state: FSMContext, session: AsyncSession
):
    """
    Перемещает нас в следующий блок в порядке упражнений
    :param message:
    :param state:
    :param session:
    :return:
    """
    data = await state.get_data()
    blocks = data.get("blocks", [])
    block_index = data.get("block_index", 0) + 1
    await state.update_data(block_index=block_index)

    if block_index >= len(blocks):
        await finish_training(message, state, session)
    else:
        await process_current_block(message, state, session)


async def finish_training(
        message: types.Message, state: FSMContext, session: AsyncSession
):
    """
    Процесс завершения тренировки
    :param message:
    :param state:
    :param session:
    :return:
    """
    data = await state.get_data()
    bot_msg_id = data.get("bot_message_id")
    if bot_msg_id:
        try:
            await message.bot.delete_message(chat_id=message.chat.id, message_id=bot_msg_id)
        except TelegramBadRequest as e:
            if "message to delete not found" in str(e):
                pass
            else:
                logging.warning(f"Failed to delete bot message: {e}")

    result_message = "Тренировка завершена! Отличная работа!\n\nОзнакомиться с результатами можно в профиле👽"
    result_message += "\n\nДля завершения тренировки нажмите на кнопку в главном сообщении 👆"
    bot_msg = await message.answer(result_message)
    await state.clear()
    await state.update_data(bot_message_id=bot_msg.message_id)


"""
Ввод данных о подходе
"""


@user_private_router.message(TrainingProcess.weight)
async def process_weight_input(
        message: types.Message, state: FSMContext):
    """
    Сохраняет кол-во повторений и предлагает ввести вес снаряда
    """

    try:
        weight = float(message.text.replace(",", "."))
        if weight < 0:
            raise ValueError("Weight cannot be negative.")
    except ValueError:
        error_message = await message.reply("Ошибка: введите положительное значение веса снаряда")
        await asyncio.sleep(3)
        await message.delete()
        await error_message.delete()
        return

    await message.delete()
    await state.update_data(weight=weight)
    data = await state.get_data()
    bot_msg_id = data.get("bot_message_id")

    try:
        await message.bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=bot_msg_id,
            text="Введите кол-во повторений:",
        )
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            pass
        else:
            logging.warning(f"Ошибка при edit_message_text: {e}")

    await state.set_state(TrainingProcess.reps)


@user_private_router.message(TrainingProcess.reps, F.text)
async def process_reps_input(
        message: types.Message, state: FSMContext, session: AsyncSession
):
    """
    Получаем кол-во повторений и предлагаем проверить на правильность введенных данных
    :param message:
    :param state:
    :param session:
    :return:
    """
    try:
        reps = int(message.text)
        if reps <= 0:
            raise ValueError("Reps must be positive.")
    except ValueError:
        error_message = await message.reply("Ошибка: введите положительное целое число повторений")
        await asyncio.sleep(3)
        await message.delete()
        await error_message.delete()
        return

    await message.delete()

    data = await state.get_data()
    weight = data.get("weight")
    ex_id = data.get("current_exercise_id")
    bot_msg_id = data.get("bot_message_id")
    await state.update_data(reps=reps)
    user_exercise = await orm_get_exercise(session, ex_id)

    try:
        await message.bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=bot_msg_id,
            text="Ожидание ввода пользователя...",
        )
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            pass
        else:
            logging.warning(f"Ошибка при edit_message_text: {e}")

    accept_message = await message.answer(f"<strong>{user_exercise.name}</strong>\n\n"
                                          f"Результат:"
                                          f"\nВес: <strong>{weight} кг/блок;</strong>"
                                          f" Повторения: <strong>{reps}</strong>\n\n",
                                          reply_markup=get_keyboard("✏️ Изменить",
                                                                    "✅ Продолжить тренировку"))
    await state.update_data(accept_message_id=accept_message.message_id)
    await state.set_state(TrainingProcess.accept_results)


@user_private_router.message(TrainingProcess.accept_results)
async def accept_results(message: types.Message, state: FSMContext, session: AsyncSession):
    """
    
    :param message:
    :param state:
    :param session:
    :return:
    """
    if message.text == "✅ Продолжить тренировку":

        data = await state.get_data()
        reps = data.get("reps")
        ex_id = data.get("current_exercise_id")
        training_session_id = data.get("training_session_id")

        try:
            set_data = {
                "exercise_id": ex_id,
                "weight": data["weight"],
                "repetitions": reps,
                "training_session_id": training_session_id,
            }
            await orm_add_set(session, set_data)
            await message.bot.delete_message(message.chat.id, data["accept_message_id"])
            await message.delete()
            if data.get("standard_ex_ids"):
                await process_standard_after_set(message, state, session)
            elif data.get("circuit_ex_ids"):
                await process_circuit_after_set(message, state, session)
            else:
                await message.answer("Ошибка: не найдено ни standard_ex_ids, ни circuit_ex_ids.")
                await state.clear()
        except Exception as e:
            await send_error_message(message, e)
            await state.clear()
    elif message.text == "✏️ Изменить":

        choose_message = await message.answer("Выберите что нужно изменить:",
                                              reply_markup=get_keyboard(
                                                  "🏋 Вес", "🔢 Повторения",
                                                  placeholder="Что нужно изменить?"))
        await message.delete()
        await state.update_data(choose_message_id=choose_message.message_id)
        await state.set_state(TrainingProcess.choose_change)


@user_private_router.message(TrainingProcess.choose_change)
async def choose_change(message: types.Message, state: FSMContext):
    await message.delete()
    data = await state.get_data()
    choose_message_id = data.get("choose_message_id")
    await message.bot.delete_message(chat_id=message.chat.id,
                                     message_id=choose_message_id)

    if message.text == "🔢 Повторения":
        try:

            enter_message = await message.answer("Введите кол-во повторений:", reply_markup=ReplyKeyboardRemove())
            await state.update_data(enter_message_id=enter_message.message_id)

        except TelegramBadRequest as e:
            logging.warning(f"Ошибка при редактировании сообщения: {e}")

        await state.set_state(TrainingProcess.change_reps)
    elif message.text == "🏋 Вес":
        await state.set_state(TrainingProcess.change_weight)
        try:

            enter_message = await message.answer("Введите вес снаряда (в кг или блоках):",
                                                 reply_markup=ReplyKeyboardRemove())
            await state.update_data(enter_message_id=enter_message.message_id)

        except TelegramBadRequest as e:
            if "message is not modified" in str(e):
                pass
            else:
                logging.warning(f"Ошибка при edit_message_text: {e}")


@user_private_router.message(TrainingProcess.change_reps)
async def process_change_reps_input(
        message: types.Message, state: FSMContext, session: AsyncSession):
    try:
        reps = int(message.text)
        if reps <= 0:
            raise ValueError("Reps must be positive.")
    except ValueError:
        error_message = await message.reply("Ошибка: введите положительное целое число повторений")
        await asyncio.sleep(3)
        await message.delete()
        await error_message.delete()
        return

    await message.delete()

    await state.update_data(reps=reps)
    data = await state.get_data()
    accept_message_id = data.get("accept_message_id")
    reps = data.get("reps")
    weight = data.get("weight")
    ex_id = data.get("current_exercise_id")
    enter_message_id = data.get("enter_message_id")
    user_exercise = await orm_get_exercise(session, ex_id)

    await message.bot.delete_message(chat_id=message.chat.id, message_id=enter_message_id)
    await message.bot.delete_message(chat_id=message.chat.id, message_id=accept_message_id)
    accept_message = await message.answer(f"<strong>{user_exercise.name}</strong>\n\n"
                                          f"Результат:"
                                          f"\nВес: <strong>{weight} кг/блок;</strong>"
                                          f" Повторения:<strong>{reps}</strong>\n\n",
                                          reply_markup=get_keyboard("✏️ Изменить",
                                                                    "✅ Продолжить тренировку"))
    await state.update_data(accept_message_id=accept_message.message_id)
    await state.set_state(TrainingProcess.accept_results)


@user_private_router.message(TrainingProcess.change_weight)
async def process_change_weight_input(
        message: types.Message, state: FSMContext, session: AsyncSession):
    try:
        weight = float(message.text.replace(",", "."))
        if weight < 0:
            raise ValueError("Weight cannot be negative.")
    except ValueError:
        error_message = await message.reply("Ошибка: вес >= 0.")
        await asyncio.sleep(3)
        await message.delete()
        await error_message.delete()
        return

    await message.delete()

    await state.update_data(weight=weight)
    data = await state.get_data()
    accept_message_id = data.get("accept_message_id")
    reps = data.get("reps")
    weight = data.get("weight")
    ex_id = data.get("current_exercise_id")
    enter_message_id = data.get("enter_message_id")
    user_exercise = await orm_get_exercise(session, ex_id)

    await message.bot.delete_message(chat_id=message.chat.id, message_id=enter_message_id)
    await message.bot.delete_message(chat_id=message.chat.id, message_id=accept_message_id)
    accept_message = await message.answer(f"<strong>{user_exercise.name}</strong>\n\n"
                                          f"Результат:"
                                          f"\nВес: <strong>{weight} кг/блок;</strong>"
                                          f" Повторения:<strong>{reps}</strong>\n\n",
                                          reply_markup=get_keyboard("✏️ Изменить",
                                                                    "✅ Продолжить тренировку"))
    await state.update_data(accept_message_id=accept_message.message_id)
    await state.set_state(TrainingProcess.accept_results)
