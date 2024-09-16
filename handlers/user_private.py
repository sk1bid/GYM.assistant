from aiogram import F, Router, types
from aiogram.filters import Command, StateFilter, or_f, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from sqlalchemy.ext.asyncio import AsyncSession
from GYM_assistant.database.orm_query import (
    orm_add_user,
    orm_update_user,
    orm_change_banner_image,
    orm_add_program,
    orm_add_training_day,
    orm_add_exercise,
    orm_add_set,
    orm_get_program,
    orm_get_training_day,
    orm_get_exercise,
    orm_get_set,
    orm_delete_program,
    orm_delete_training_day,
    orm_update_set,
    orm_update_exercise,
    orm_update_program, orm_get_user_by_id,

)

from GYM_assistant.filters.chat_types import ChatTypeFilter
from GYM_assistant.handlers.menu_processing import get_menu_content
from GYM_assistant.kbds.inline import MenuCallBack, get_callback_btns

user_private_router = Router()
user_private_router.message.filter(ChatTypeFilter(["private"]))


class AddUser(StatesGroup):
    user_id = State()
    name = State()
    weight = State()

    user_for_change = None

    texts = {
        "AddUser:name": "Введите ваше имя заново:",
        "AddUser:weight": "Введите ваш вес заново:",
    }


@user_private_router.message(StateFilter(None), CommandStart())
async def send_welcome(message: types.Message, state: FSMContext, session: AsyncSession):
    user_id = message.from_user.id

    user = await orm_get_user_by_id(session, user_id)

    if user:
        await message.answer(f"Вы уже зарегистрированы как {user.name}.")
        media, reply_markup = await get_menu_content(session, level=0, menu_name="main")

        await message.answer_photo(media.media, caption=media.caption, reply_markup=reply_markup)
    else:
        await message.answer("Привет, я твой виртуальный тренер. Давай тебя зарегистрируем. "
                             "Напиши свое имя:")
        await state.set_state(AddUser.name)
        await state.update_data(user_id=int(user_id))


@user_private_router.message(StateFilter("*"), Command("отмена"))
@user_private_router.message(StateFilter("*"), F.text.casefold() == "отмена")
async def cancel_handler(message: types.Message, state: FSMContext) -> None:
    current_state = await state.get_state()
    if current_state is None:
        return
    if AddUser.user_for_change:
        AddUser.user_for_change = None
    await state.clear()
    await message.answer("Действия отменены")



@user_private_router.message(StateFilter("*"), Command("назад"))
@user_private_router.message(StateFilter("*"), F.text.casefold() == "назад")
async def back_step_handler(message: types.Message, state: FSMContext) -> None:
    current_state = await state.get_state()
    print(current_state)
    if current_state == AddUser.name:
        await message.answer(
            'Предыдущего шага нет, или введите ваше имя или напишите "отмена"'
        )
        return

    previous = None
    for step in AddUser.__all_states__:
        if step.state == current_state:
            await state.set_state(previous)
            await message.answer(
                f"Ок, вы вернулись к прошлому шагу\n{AddUser.texts[previous]}"
            )
            return
        previous = step


@user_private_router.message(AddUser.name, F.text)
async def add_name(message: types.Message, state: FSMContext):
    if message.text == "." and AddUser.user_for_change:
        await state.update_data(name=AddUser.user_for_change.name)
    else:
        await state.update_data(name=message.text)
    await message.answer(f"Отлично, {message.text}. Введите ваш вес:")
    await state.set_state(AddUser.weight)


@user_private_router.message(AddUser.name)
async def add_wrong_name(message: types.Message):
    await message.answer("Вы ввели не допустимые данные, введите текст ")


@user_private_router.message(AddUser.weight, F.text)
async def add_weight(message: types.Message, state: FSMContext, session: AsyncSession):
    if message.text == "." and AddUser.user_for_change:
        await state.update_data(weight=float(AddUser.user_for_change.weight))
    else:
        await state.update_data(weight=float(message.text))

    data = await state.get_data()
    try:
        if AddUser.user_for_change:
            await orm_update_user(session, AddUser.user_for_change.id, data)
        else:
            await orm_add_user(session, data)
        await message.answer("Прекрасно, вы зарегистрированы в системе!\nДля навигации используйте"
                             " интерактивное меню, советую начать с настройки программы тренировок")
        await state.clear()
    except Exception as e:
        await message.answer(
            f"Ошибка: \n{str(e)}\nЧто-то сломалось, напиши Артему в лс")
        await state.clear()
    AddUser.product_for_change = None

    media, reply_markup = await get_menu_content(session, level=0, menu_name="main")

    await message.answer_photo(media.media, caption=media.caption, reply_markup=reply_markup)


@user_private_router.callback_query(MenuCallBack.filter())
async def user_menu(callback: types.CallbackQuery, callback_data: MenuCallBack, session: AsyncSession):
    media, reply_markup = await get_menu_content(
        session,
        level=callback_data.level,
        menu_name=callback_data.menu_name,
        program_id=callback_data.program_id,
        page=callback_data.page,
        user_id=callback.from_user.id,
    )

    await callback.message.edit_media(media=media, reply_markup=reply_markup)
    await callback.answer()
