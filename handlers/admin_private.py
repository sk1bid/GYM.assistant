from asyncio import sleep
from aiogram import F, Router, types
from aiogram.filters import Command, StateFilter, or_f, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.formatting import Text
from sqlalchemy.ext.asyncio import AsyncSession
from database.orm_query import (
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
    orm_update_program,
    orm_get_user_by_id,
    orm_get_exercises,
    orm_delete_exercise,
    orm_get_admin_exercises,
    orm_update_admin_exercise,
    orm_add_admin_exercise,
    orm_get_admin_exercise,
    orm_delete_admin_exercise,
    orm_get_info_pages, orm_get_categories,
)
from filters.chat_types import ChatTypeFilter, IsAdmin
from handlers.menu_processing import get_menu_content
from kbds.inline import MenuCallBack, get_callback_btns
from kbds.reply import get_keyboard

admin_router = Router()
admin_router.message.filter(ChatTypeFilter(["private"]), IsAdmin())

ADMIN_KB = get_keyboard(
    "Добавить упражнение",
    "Все упражнения",
    "Добавить/Изменить баннер",
    placeholder="Выберите действие",
    sizes=(2,),
)


@admin_router.message(Command("admin"))
async def admin_features(message: types.Message):
    await message.answer("Что хотите сделать?", reply_markup=ADMIN_KB)


@admin_router.message(F.text == 'Все упражнения')
async def admin_features(message: types.Message, session: AsyncSession):
    exercises = await orm_get_admin_exercises(session)

    if not exercises:
        await message.answer("Упражнения не найдены.")
        return

    # Формируем кнопки для всех упражнений
    btns = {exercise.name: f'exercise_{exercise.id}' for exercise in exercises}

    await message.answer(
        "Список упражнений:",
        reply_markup=get_callback_btns(btns=btns)
    )


@admin_router.callback_query(F.data.startswith('exercise_'))
async def starring_at_exercise(callback: types.CallbackQuery, session: AsyncSession):
    exercise_id = callback.data.split('_')[-1]
    exercise = await orm_get_admin_exercise(session, int(exercise_id))

    await callback.message.answer_photo(
        exercise.image,
        caption=f"<strong>{exercise.name}\n</strong>\n{exercise.description}\n",
        reply_markup=get_callback_btns(
            btns={
                "Удалить": f"delete_{exercise.id}",
                "Изменить": f"change_{exercise.id}",
            },
            sizes=(2,)
        ),
    )
    await callback.answer()


@admin_router.callback_query(F.data.startswith("delete_"))
async def delete_exercise_callback(callback: types.CallbackQuery, session: AsyncSession):
    exercise_id = callback.data.split("_")[-1]
    await orm_delete_admin_exercise(session, int(exercise_id))

    await callback.answer("Упражнение удалено")
    await callback.message.answer("Упражнение удалено!")


######################### FSM для добавления/изменения упражнений ###################

class AddExercise(StatesGroup):
    name = State()
    description = State()
    category_id = State()
    image = State()

    exercise_for_change = None

    texts = {
        "AddExercise:name": "Введите название упражнения:",
        "AddExercise:description": "Введите описание упражнения:",
        "AddExercise:category": "Выберете категорию заново",
        "AddExercise:image": "Прикрепите изображение упражнения:",
    }


class AddBanner(StatesGroup):
    image = State()


@admin_router.callback_query(StateFilter(None), F.data.startswith("change_"))
async def change_exercise_callback(
        callback: types.CallbackQuery, state: FSMContext, session: AsyncSession
):
    exercise_id = callback.data.split("_")[-1]
    exercise_for_change = await orm_get_admin_exercise(session, int(exercise_id))
    AddExercise.exercise_for_change = exercise_for_change

    await callback.answer()
    await callback.message.answer("Введите название упражнения:", reply_markup=types.ReplyKeyboardRemove())
    await state.set_state(AddExercise.name)


@admin_router.message(StateFilter(None), F.text == "Добавить упражнение")
async def add_exercise(message: types.Message, state: FSMContext):
    await message.answer("Введите название упражнения:", reply_markup=types.ReplyKeyboardRemove())
    await state.set_state(AddExercise.name)


@admin_router.message(StateFilter(AddExercise.name.state, AddExercise.category_id.state, AddExercise.description.state,
                                  AddExercise.image.state, AddBanner.image), Command("отмена"))
@admin_router.message(StateFilter(AddExercise.name.state, AddExercise.category_id.state, AddExercise.description.state,
                                  AddExercise.image.state, AddBanner.image), F.text.casefold() == "отмена")
async def cancel_handler(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Действия отменены", reply_markup=ADMIN_KB)


@admin_router.message(AddExercise.name, F.text)
async def add_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer("Введите описание упражнения")
    await state.set_state(AddExercise.description)


@admin_router.message(AddExercise.description, F.text)
async def add_description(message: types.Message, state: FSMContext, session: AsyncSession):
    await state.update_data(description=message.text)
    categories = await orm_get_categories(session)
    btns = {category.name: str(category.id) for category, _ in categories}
    await message.answer("Выберите категорию", reply_markup=get_callback_btns(btns=btns))
    await state.set_state(AddExercise.category_id)


@admin_router.callback_query(AddExercise.category_id)
async def category_choice(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    categories = await orm_get_categories(session)
    category_ids = [category.id for category, _ in categories]
    if int(callback.data) in category_ids:
        await callback.answer()
        await state.update_data(category=callback.data)
        await callback.message.answer("Прикрепите изображение упражнения")
        await state.set_state(AddExercise.image)
    else:
        await callback.message.answer('Выберите категорию из кнопок.')
        await callback.answer()


# Ловим любые некорректные действия, кроме нажатия на кнопку выбора категории
@admin_router.message(AddExercise.category_id)
async def category_choice2(message: types.Message, state: FSMContext):
    await message.answer("'Выберите категорию из кнопок.'")


@admin_router.message(AddExercise.image, or_f(F.photo, F.text == "."))
async def add_image(message: types.Message, state: FSMContext, session: AsyncSession):
    if message.photo:
        await state.update_data(image=message.photo[-1].file_id)
    else:
        await message.answer("Отправьте фото упражнения")
        return

    data = await state.get_data()
    try:
        if AddExercise.exercise_for_change:
            await orm_update_admin_exercise(session, AddExercise.exercise_for_change.id, data)
        else:
            await orm_add_admin_exercise(session, data)

        await message.answer("Упражнение добавлено/изменено", reply_markup=ADMIN_KB)
        await state.clear()
    except Exception as e:
        await message.answer(f"Ошибка: \n{str(e)}\nОбратитесь к администратору.", reply_markup=ADMIN_KB)
        await state.clear()
    AddExercise.exercise_for_change = None


################# Изменение/добавление баннеров ############################


@admin_router.message(StateFilter(None), F.text == 'Добавить/Изменить баннер')
async def add_image2(message: types.Message, state: FSMContext, session: AsyncSession):
    pages_names = [page.name for page in await orm_get_info_pages(session)]
    await message.answer(f"Отправьте фото баннера.\nУкажите, для какой страницы: {', '.join(pages_names)}")
    await state.set_state(AddBanner.image)


@admin_router.message(AddBanner.image, F.photo)
async def add_banner(message: types.Message, state: FSMContext, session: AsyncSession):
    image_id = message.photo[-1].file_id
    for_page = message.caption.strip()

    pages_names = [page.name for page in await orm_get_info_pages(session)]
    if for_page not in pages_names:
        await message.answer(f"Введите корректное название страницы: {', '.join(pages_names)}")
        return

    await orm_change_banner_image(session, for_page, image_id)
    await message.answer("Баннер добавлен/изменен.", reply_markup=ADMIN_KB)
    await state.clear()