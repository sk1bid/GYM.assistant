from aiogram import F, types, Router
from aiogram.filters import Command, or_f, StateFilter
from aiogram.utils.formatting import as_list, as_marked_section, Bold
from aiogram.fsm.state import State, StatesGroup
from GYM_assistant.filters.chat_types import ChatTypeFilter
from aiogram.fsm.context import FSMContext

user_private_router = Router()
user_private_router.message.filter(ChatTypeFilter(['private']))


# План регистрации
class Registration(StatesGroup):
    name = State()
    weight = State()
    training_experience = State()

    texts = {
        'Registration:name': 'Введите имя заново:',
        'Registration:weight': 'Введите вес заново:',
        'Registration:training_experience': 'Введите кол-во лет заново:'
    }


# Приветственное сообщение
@user_private_router.message(or_f(Command("start"), Command("register")), State(None))
async def start_cmd(message: types.Message, state: FSMContext):
    await message.answer(
        "Привет, я виртуальный тренер. Давай тебя зарегистрируем. Как мне к тебе обращаться?")
    await state.set_state(Registration.name)


# Обработка функции отмены ввода данных
@user_private_router.message(StateFilter('*'), or_f(Command("cancel"), F.text.casefold() == "отмена"))
async def cancel_handler(message: types.Message, state: FSMContext) -> None:
    current_state = await state.get_state()
    if current_state is None:
        return

    await state.clear()
    await message.answer("Действия отменены")
    await message.answer(
        "Привет, я виртуальный тренер. Давай тебя зарегистрируем. Как мне к тебе обращаться?")
    await state.set_state(Registration.name)


# Обработка функции возвращения на шаг назад
@user_private_router.message(StateFilter('*'), or_f(Command('back'), F.text.casefold() == "назад"))
async def back_step_handler(message: types.Message, state: FSMContext) -> None:
    current_state = await state.get_state()

    if current_state == Registration.name:
        await message.answer("Предыдущего шага нет, введите своё имя или напишите 'отмена'")
        return

    previous = None
    for step in Registration.__all_states__:
        if step.state == current_state:
            await state.set_state(previous)
            await message.answer(f"Вы вернулись к предыдущему шагу\n{Registration.texts[previous.state]}")
            return
        previous = step


# Запоминаем имя пользователя и спрашиваем его вес
@user_private_router.message(Registration.name, F.text)
async def add_name(message: types.Message, state: FSMContext):
    if len(message.text) > 10:
        await message.answer("Ваше имя не должно превышать 10 символов.\nВведите имя заново:")
        return

    await state.update_data(name=message.text)
    await message.answer(
        f"Приятно познакомиться, {message.text}!\nВведите ваш текущий вес, целое значение в килограммах:")
    await state.set_state(Registration.weight)


# Обработка некорректных данных
@user_private_router.message(Registration.name)
async def incrt_name(message: types.Message):
    await message.answer("Вы ввели недопустимые данные, введите ваше имя:")


# Запоминаем вес пользователя и спрашиваем его стаж тренировок
@user_private_router.message(Registration.weight, F.text)
async def add_weight(message: types.Message, state: FSMContext):
    try:
        float(message.text)
    except ValueError:
        await message.answer("Введите корректное значение веса, в килограммах. Пример: '56', '56.1'")
        return

    await state.update_data(weight=message.text)
    await message.answer(f"{message.text} кг, запомнил! Введите ваш стаж тренировок, в годах")
    await state.set_state(Registration.training_experience)


# Обработка некорректных данных
@user_private_router.message(Registration.weight)
async def incrt_weight(message: types.Message):
    await message.answer("Вы ввели недопустимые данные, введите ваш вес:")


# Запоминаем стаж
@user_private_router.message(Registration.training_experience, F.text)
async def add_training_experience(message: types.Message, state: FSMContext):
    try:
        int(message.text)
    except ValueError:
        await message.answer("Введите корректное значение стажа, в годах, целое значение")
        return

    await state.update_data(training_experience=message.text)
    await message.answer("Вы успешно зарегистрированы!")
    data = await state.get_data()
    await message.answer(str(data))
    await state.clear()


# Обработка некорректных данных
@user_private_router.message(Registration.training_experience)
async def incrt_training_experience(message: types.Message):
    await message.answer("Вы ввели недопустимые данные, введите ваш стаж, целое значение в годах:")
