"""
Наполнение каталога пресетов.

В прототипе на пустой базе заводились три демо-упражнения и «Демо-программа».
Для боевого Mini App этого мало: пользователь открывает каталог и должен увидеть
осмысленный набор в каждой группе мышц, а программу собрать сам.

Запускается на старте, идемпотентно: упражнения добавляются только те, которых ещё нет.
"""
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from database.orm_query import (
    orm_add_admin_exercise,
    orm_get_admin_exercises,
    orm_get_categories,
)
from services.equipment import BARBELL, BODYWEIGHT, DUMBBELL, MACHINE, STACK

# (название, описание, категория, снаряд)
#
# Снаряд задан здесь, а не спрошен у пользователя: по названию пресета он очевиден,
# а от него зависит шаг веса на экране подхода (services/equipment.py). Отдельно
# отмечены упражнения со своим весом — там поля веса нет вовсе.
CATALOG = [
    # Грудь
    ("Жим штанги лёжа", "Базовое упражнение на грудь, трицепс и передние дельты", "Грудь", BARBELL),
    ("Жим гантелей лёжа", "Больше амплитуда, чем со штангой", "Грудь", DUMBBELL),
    ("Жим штанги на наклонной", "Акцент на верх груди", "Грудь", BARBELL),
    ("Разводка гантелей", "Изолирующее на грудь", "Грудь", DUMBBELL),
    ("Отжимания на брусьях", "Низ груди и трицепс", "Грудь", BODYWEIGHT),
    # Спина
    ("Подтягивания", "Базовое на широчайшие", "Спина", BODYWEIGHT),
    ("Тяга штанги в наклоне", "Базовое на толщину спины", "Спина", BARBELL),
    ("Становая тяга", "Базовое на всю заднюю цепь", "Спина", BARBELL),
    ("Тяга верхнего блока", "Широчайшие, альтернатива подтягиваниям", "Спина", STACK),
    ("Тяга горизонтального блока", "Середина спины", "Спина", STACK),
    # Ноги
    ("Приседания со штангой", "Базовое на квадрицепс и ягодицы", "Ноги", BARBELL),
    ("Жим ногами", "Квадрицепс без осевой нагрузки", "Ноги", MACHINE),
    ("Румынская тяга", "Бицепс бедра и ягодицы", "Ноги", BARBELL),
    ("Выпады с гантелями", "Квадрицепс, ягодицы, баланс", "Ноги", DUMBBELL),
    ("Подъём на носки", "Икроножные", "Ноги", MACHINE),
    # Дельты
    ("Жим штанги стоя", "Базовое на плечи", "Дельты", BARBELL),
    ("Жим гантелей сидя", "Передние и средние дельты", "Дельты", DUMBBELL),
    ("Махи гантелями в стороны", "Изолирующее на средние дельты", "Дельты", DUMBBELL),
    ("Махи в наклоне", "Задние дельты", "Дельты", DUMBBELL),
    # Руки
    ("Подъём штанги на бицепс", "Базовое на бицепс", "Руки", BARBELL),
    ("Подъём гантелей на бицепс", "Бицепс, с супинацией", "Руки", DUMBBELL),
    ("Французский жим", "Трицепс", "Руки", BARBELL),
    ("Разгибания на блоке", "Изолирующее на трицепс", "Руки", STACK),
    ("Молотки", "Брахиалис и предплечье", "Руки", DUMBBELL),
    # Пресс
    ("Скручивания", "Прямая мышца живота", "Пресс", BODYWEIGHT),
    ("Подъём ног в висе", "Низ пресса", "Пресс", BODYWEIGHT),
    ("Планка", "Статика на кор", "Пресс", BODYWEIGHT),
    ("Русские скручивания", "Косые мышцы живота", "Пресс", BODYWEIGHT),
    # Трапеции
    ("Шраги со штангой", "Трапеции", "Трапеции", BARBELL),
    ("Шраги с гантелями", "Трапеции, больше амплитуда", "Трапеции", DUMBBELL),
]

EQUIPMENT_BY_NAME = {name: equipment for name, _, _, equipment in CATALOG}

# Категории, которые надо переименовать в уже заведённых базах.
#
# «Трап.» приехало из бота и вылезало прямо в каталог: среди «Грудь», «Спина»,
# «Ноги» одна группа выглядела обрезанной, то есть читалась как съехавшая вёрстка,
# а не как название. Правим здесь, а не миграцией: orm_create_categories заполняет
# таблицу только пока она пуста, поэтому на живых базах список категорий иначе
# не меняется вовсе.
RENAMED_CATEGORIES = {"Трап.": "Трапеции"}


async def seed_catalog(session: AsyncSession) -> None:
    """
    Досыпает в каталог недостающие пресеты и приводит снаряды в соответствие с кодом.

    Названия и описания существующих пресетов не трогаем — их мог поменять админ.
    Снаряд трогаем: руками его выставить негде, значит источник правды один — CATALOG.
    """
    # orm_get_categories отдаёт пары (категория, счётчик) — счётчик нам не нужен.
    known = {c.name: c for c, _ in await orm_get_categories(session, user_id=0)}

    renamed = [
        category for old, new in RENAMED_CATEGORIES.items()
        if (category := known.get(old)) is not None and new not in known
    ]
    for category in renamed:
        category.name = RENAMED_CATEGORIES[category.name]
    if renamed:
        await session.commit()
        logging.info("категории переименованы: %s", len(renamed))

    categories = {c.name: c.id for c, _ in await orm_get_categories(session, user_id=0)}
    presets = await orm_get_admin_exercises(session)
    existing = {e.name for e in presets}

    added = 0
    for name, description, category, equipment in CATALOG:
        if name in existing or category not in categories:
            continue
        await orm_add_admin_exercise(session, {
            "name": name,
            "description": description,
            "category": categories[category],
            "equipment": equipment,
        })
        added += 1

    if added:
        logging.info("каталог пополнен: %s упражнений", added)

    await _sync_equipment(session, presets)


async def _sync_equipment(session: AsyncSession, presets) -> None:
    """
    Проставляет снаряд там, где его ещё нет.

    Два разных случая, и оба про базы, заведённые до появления колонки:

    1. Пресеты каталога получили от миграции 'other', то есть шаг 2.5 на блоке
       и на гантелях. Догоняем их по названию — миграцией было бы нельзя, каталог
       наполняется кодом, и на разных контурах у одного упражнения разные id.
    2. Упражнения, УЖЕ разложенные по дням, держат снимок снаряда (см. models.py).
       У старых строк там NULL — снимаем снимок с карточки каталога.

    Оба прохода идемпотентны: второй запуск не находит работы.
    """
    stale = [
        preset for preset in presets
        if (want := EQUIPMENT_BY_NAME.get(preset.name)) is not None and preset.equipment != want
    ]
    for preset in stale:
        preset.equipment = EQUIPMENT_BY_NAME[preset.name]
    if stale:
        await session.commit()
        logging.info("снаряд проставлен пресетам: %s", len(stale))

    # Снимок на упражнениях дня. Коррелированный подзапрос, а не UPDATE ... FROM:
    # он одинаково работает и на Postgres, и на SQLite тестов.
    filled = 0
    for table, link in (("admin_exercises", "admin_exercise_id"), ("user_exercises", "user_exercise_id")):
        result = await session.execute(text(
            f"UPDATE exercise SET equipment = "
            f"(SELECT c.equipment FROM {table} c WHERE c.id = exercise.{link}) "
            f"WHERE equipment IS NULL AND {link} IS NOT NULL"
        ))
        filled += result.rowcount or 0

    if filled:
        await session.commit()
        logging.info("снаряд проставлен упражнениям в днях: %s", filled)
