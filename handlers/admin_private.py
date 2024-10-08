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
    orm_update_program, orm_get_user_by_id, orm_get_exercises, orm_delete_exercise, orm_get_admin_exercises,
    orm_update_admin_exercise, orm_add_admin_exercise, orm_get_admin_exercise, orm_delete_admin_exercise,

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
    print(exercise_id)
    exercise = await orm_get_admin_exercise(session, int(exercise_id))
    await callback.message.answer_photo(
        exercise.image,
        caption=f"<strong>{exercise.name}\
                </strong>\n{exercise.description}\n",
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


######################### FSM для дабавления/изменения упржнений админом ###################

class AddExercise(StatesGroup):
    # Шаги состояний
    name = State()
    description = State()
    image = State()

    exercise_for_change = None

    texts = {
        "AddExercise:name": "Введите название заново:",
        "AddExercise:description": "Введите описание заново:",
        "AddExercise:image": "Отправьте картинку заново",
    }


# Становимся в состояние ожидания ввода name
@admin_router.callback_query(StateFilter(None), F.data.startswith("change_"))
async def change_exercise_callback(
        callback: types.CallbackQuery, state: FSMContext, session: AsyncSession
):
    exercise_id = callback.data.split("_")[-1]

    exercise_for_change = await orm_get_admin_exercise(session, int(exercise_id))

    AddExercise.exercise_for_change = exercise_for_change

    await callback.answer()
    await callback.message.answer(
        "Введите название упражнения", reply_markup=types.ReplyKeyboardRemove()
    )
    await state.set_state(AddExercise.name)


# Становимся в состояние ожидания ввода name
@admin_router.message(StateFilter(None), F.text == "Добавить упражнение")
async def add_exercise(message: types.Message, state: FSMContext):
    await message.answer(
        "Введите название упражнения", reply_markup=types.ReplyKeyboardRemove()
    )
    await state.set_state(AddExercise.name)


# Хендлер отмены и сброса состояния должен быть всегда именно здесь,
# после того, как только встали в состояние номер 1 (элементарная очередность фильтров)
@admin_router.message(StateFilter("*"), Command("отмена"))
@admin_router.message(StateFilter("*"), F.text.casefold() == "отмена")
async def cancel_handler(message: types.Message, state: FSMContext) -> None:
    current_state = await state.get_state()
    if current_state is None:
        return
    if AddExercise.exercise_for_change:
        AddExercise.exercise_for_change = None
    await state.clear()
    await message.answer("Действия отменены", reply_markup=ADMIN_KB)


# Вернутся на шаг назад (на прошлое состояние)
@admin_router.message(StateFilter("*"), Command("назад"))
@admin_router.message(StateFilter("*"), F.text.casefold() == "назад")
async def back_step_handler(message: types.Message, state: FSMContext) -> None:
    current_state = await state.get_state()

    if current_state == AddExercise.name:
        await message.answer(
            'Предыдущего шага нет, или введите название товара или напишите "отмена"'
        )
        return

    previous = None
    for step in AddExercise.__all_states__:
        if step.state == current_state:
            await state.set_state(previous)
            await message.answer(
                f"Ок, вы вернулись к прошлому шагу \n {AddExercise.texts[previous.state]}"
            )
            return
        previous = step


@admin_router.message(AddExercise.name, F.text)
async def add_name(message: types.Message, state: FSMContext):
    if message.text == "." and AddExercise.exercise_for_change:
        await state.update_data(name=AddExercise.exercise_for_change.name)
    else:
        if 4 >= len(message.text) >= 150:
            await message.answer(
                "Название упражнения не должно превышать 150 символов\nили быть менее 5ти символов. \n Введите заново"
            )
            return

        await state.update_data(name=message.text)
    await message.answer("Введите описание упражнения")
    await state.set_state(AddExercise.description)


@admin_router.message(AddExercise.name)
async def add_name2(message: types.Message, state: FSMContext):
    await message.answer("Вы ввели не допустимые данные, введите текст описания упражнения")


@admin_router.message(AddExercise.description, F.text)
async def add_description(message: types.Message, state: FSMContext, session: AsyncSession):
    if message.text == "." and AddExercise.exercise_for_change:
        await state.update_data(description=AddExercise.exercise_for_change.description)
    else:
        if 4 >= len(message.text):
            await message.answer(
                "Слишком короткое описание. \n Введите заново"
            )
            return
        await state.update_data(description=message.text)
    await message.answer("Отлично, прикрепите изображение упражнения")
    await state.set_state(AddExercise.image)


@admin_router.message(AddExercise.description)
async def add_description2(message: types.Message, state: FSMContext):
    await message.answer("Вы ввели не допустимые данные, введите текст описания товара")


@admin_router.message(AddExercise.image, or_f(F.photo, F.text == "."))
async def add_image(message: types.Message, state: FSMContext, session: AsyncSession):
    if message.text and message.text == "." and AddExercise.exercise_for_change:
        await state.update_data(image=AddExercise.exercise_for_change.image)

    elif message.photo:
        await state.update_data(image=message.photo[-1].file_id)
    else:
        await message.answer("Отправьте фото упражнения")
        return
    data = await state.get_data()
    print(data)
    try:
        if AddExercise.exercise_for_change:
            await orm_update_admin_exercise(session, AddExercise.exercise_for_change.id, data)
        else:
            await orm_add_admin_exercise(session, data)
        await message.answer("Упражнение добавлено/изменено", reply_markup=ADMIN_KB)
        await state.clear()

    except Exception as e:
        await message.answer(
            f"Ошибка: \n{str(e)}\nОбратись к программеру, он опять денег хочет",
            reply_markup=ADMIN_KB,
        )
        await state.clear()

    AddExercise.product_for_change = None


@admin_router.message(AddExercise.image)
async def add_image2(message: types.Message, state: FSMContext):
    await message.answer("Отправьте фото упражнения")
