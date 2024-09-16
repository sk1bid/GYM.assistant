from aiogram.types import InlineKeyboardButton
from aiogram.filters.callback_data import CallbackData
from aiogram.utils.keyboard import InlineKeyboardBuilder


class MenuCallBack(CallbackData, prefix="menu"):
    level: int
    menu_name: str
    page: int = 1
    day_id: int | None = None
    exercise_id: int | None = None
    set_id: int | None = None
    program_id: int | None = None


def get_user_main_btns(*, level: int, sizes: tuple[int] = (1,)):
    keyboard = InlineKeyboardBuilder()
    btns = {
        "Расписание": "schedule",
        "Программа тренировок": "program",
        "Профиль": "profile"
    }
    for text, menu_name in btns.items():
        if menu_name == 'schedule' or menu_name == 'program' or menu_name == 'profile':
            keyboard.add(InlineKeyboardButton(text=text,
                                              callback_data=MenuCallBack(level=level + 1, menu_name=menu_name).pack()))

    return keyboard.adjust(*sizes).as_markup()


def get_user_programs_list(*, level: int, programs: list, sizes: tuple[int] = (1,)):
    keyboard = InlineKeyboardBuilder()

    for c in programs:
        keyboard.add(InlineKeyboardButton(text=c.name,
                                          callback_data=MenuCallBack(level=level + 1, menu_name=c.name,
                                                                     category=c.id).pack()))
    keyboard.add(InlineKeyboardButton(text='Назад',
                                      callback_data=MenuCallBack(level=level - 1, menu_name='main').pack()))
    keyboard.add(InlineKeyboardButton(text='Добавить программу',
                                      callback_data=MenuCallBack(level=level + 1, menu_name='adding_program').pack()))
    return keyboard.adjust(*sizes).as_markup()


def get_training_day_btns(
        *,
        level: int,
        user_program: int,
        page: int,
        pagination_btns: dict,
        training_day_id: int,
        sizes: tuple[int] = (2, 1)
):
    keyboard = InlineKeyboardBuilder()

    keyboard.add(InlineKeyboardButton(text="Назад",
                                      callback_data=MenuCallBack(level=level - 1, menu_name='program').pack()))
    keyboard.add(InlineKeyboardButton(text="Редактировать день",
                                      callback_data=MenuCallBack(level=level + 2, menu_name='training_day',
                                                                 day_id=training_day_id).pack()))

    keyboard.adjust(*sizes)

    row = []
    for text, menu_name in pagination_btns.items():
        if menu_name == "next":
            row.append(InlineKeyboardButton(text=text,
                                            callback_data=MenuCallBack(
                                                level=level,
                                                menu_name=menu_name,
                                                program_id=user_program,
                                                page=page + 1).pack()))

        elif menu_name == "previous":
            row.append(InlineKeyboardButton(text=text,
                                            callback_data=MenuCallBack(
                                                level=level,
                                                menu_name=menu_name,
                                                program_id=user_program,
                                                page=page - 1).pack()))

    return keyboard.row(*row).as_markup()


def get_callback_btns(
        *,
        btns: dict[str, str],
        sizes: tuple[int] = (2,)):
    keyboard = InlineKeyboardBuilder()

    for text, data in btns.items():
        keyboard.add(InlineKeyboardButton(text=text, callback_data=data))

    return keyboard.adjust(*sizes).as_markup()


def get_url_btns(
        *,
        btns: dict[str, str],
        sizes: tuple[int] = (2,)):
    keyboard = InlineKeyboardBuilder()

    for text, url in btns.items():
        keyboard.add(InlineKeyboardButton(text=text, url=url))

    return keyboard.adjust(*sizes).as_markup()


# Создать микс из CallBack и URL кнопок
def get_inlineMix_btns(
        *,
        btns: dict[str, str],
        sizes: tuple[int] = (2,)):
    keyboard = InlineKeyboardBuilder()

    for text, value in btns.items():
        if '://' in value:
            keyboard.add(InlineKeyboardButton(text=text, url=value))
        else:
            keyboard.add(InlineKeyboardButton(text=text, callback_data=value))

    return keyboard.adjust(*sizes).as_markup()
