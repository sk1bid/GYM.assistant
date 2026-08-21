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
#
# Это ЕДИНСТВЕННЫЙ источник правды о каталоге: имя, категория и снаряд. Всё, что
# разошлось с этим списком на живой базе, догоняется при старте (см. seed_catalog).
CATALOG = [
    # Грудь
    ("Жим штанги лёжа", "Базовое упражнение на грудь, трицепс и передние дельты", "Грудь", BARBELL),
    ("Жим гантелей лёжа", "Больше амплитуда, чем со штангой", "Грудь", DUMBBELL),
    ("Жим штанги на наклонной", "Акцент на верх груди", "Грудь", BARBELL),
    ("Жим гантелей в наклоне", "Верх груди, амплитуда больше, чем со штангой", "Грудь", DUMBBELL),
    ("Разводка гантелей лёжа", "Изолирующее на грудь", "Грудь", DUMBBELL),
    ("Сведение рук в кроссовере", "Изолирующее на грудь, натяжение по всей амплитуде", "Грудь", STACK),
    ("Брусья", "Низ груди и трицепс", "Грудь", BODYWEIGHT),
    # Спина
    ("Подтягивания", "Базовое на широчайшие", "Спина", BODYWEIGHT),
    ("Тяга штанги в наклоне", "Базовое на толщину спины", "Спина", BARBELL),
    ("Тяга Т-грифа", "Толщина спины, поясница нагружена меньше", "Спина", BARBELL),
    ("Тяга гантели в наклоне к поясу", "Широчайшие, по одной стороне", "Спина", DUMBBELL),
    ("Становая тяга", "Базовое на всю заднюю цепь", "Спина", BARBELL),
    ("Тяга верхнего блока", "Широчайшие, альтернатива подтягиваниям", "Спина", STACK),
    ("Тяга горизонтального блока", "Середина спины", "Спина", STACK),
    ("Гиперэкстензия", "Разгибатели спины и ягодицы", "Спина", BODYWEIGHT),
    # Ноги
    ("Приседания со штангой", "Базовое на квадрицепс и ягодицы", "Ноги", BARBELL),
    ("Жим ногами", "Квадрицепс без осевой нагрузки", "Ноги", MACHINE),
    ("Болгарские приседания", "Квадрицепс и ягодицы, по одной ноге", "Ноги", DUMBBELL),
    ("Румынская тяга", "Бицепс бедра и ягодицы", "Ноги", BARBELL),
    ("Выпады с гантелями", "Квадрицепс, ягодицы, баланс", "Ноги", DUMBBELL),
    ("Разгибания ног в тренажёре", "Изолирующее на квадрицепс", "Ноги", MACHINE),
    ("Сгибания ног лёжа в тренажёре", "Изолирующее на бицепс бедра", "Ноги", MACHINE),
    ("Подъём на носки", "Икроножные", "Ноги", MACHINE),
    # Дельты
    ("Жим штанги стоя", "Базовое на плечи", "Дельты", BARBELL),
    ("Жим гантелей сидя", "Передние и средние дельты", "Дельты", DUMBBELL),
    ("Махи гантелями в стороны", "Изолирующее на средние дельты", "Дельты", DUMBBELL),
    ("Махи в наклоне", "Задние дельты", "Дельты", DUMBBELL),
    # Руки
    ("Подъём штанги на бицепс", "Базовое на бицепс", "Руки", BARBELL),
    ("Подъём гантелей на бицепс", "Бицепс, с супинацией", "Руки", DUMBBELL),
    ("Молотки", "Брахиалис и предплечье", "Руки", DUMBBELL),
    ("Французский жим", "Трицепс", "Руки", BARBELL),
    ("Разгибания из-за головы на блоке", "Длинная головка трицепса в растяжении", "Руки", STACK),
    ("Разгибания на блоке", "Изолирующее на трицепс", "Руки", STACK),
    ("Жим узким хватом", "Трицепс, базовое", "Руки", BARBELL),
    # Пресс
    ("Скручивания", "Прямая мышца живота", "Пресс", BODYWEIGHT),
    ("Скручивания на наклонной скамье", "Прямая мышца живота, амплитуда больше", "Пресс", BODYWEIGHT),
    ("Пресс в тренажёре", "Прямая мышца живота, с весом", "Пресс", STACK),
    ("Подъём ног в висе", "Низ пресса", "Пресс", BODYWEIGHT),
    ("Планка", "Статика на кор", "Пресс", BODYWEIGHT),
    ("Русские скручивания", "Косые мышцы живота", "Пресс", BODYWEIGHT),
    # Трапеции
    ("Шраги со штангой", "Трапеции", "Трапеции", BARBELL),
    ("Шраги с гантелями", "Трапеции, больше амплитуда", "Трапеции", DUMBBELL),
]

EQUIPMENT_BY_NAME = {name: equipment for name, _, _, equipment in CATALOG}
CATEGORY_BY_NAME = {name: category for name, _, category, _ in CATALOG}

# Категории, которые надо переименовать в уже заведённых базах.
#
# «Трап.» приехало из бота и вылезало прямо в каталог: среди «Грудь», «Спина»,
# «Ноги» одна группа выглядела обрезанной, то есть читалась как съехавшая вёрстка,
# а не как название. Правим здесь, а не миграцией: orm_create_categories заполняет
# таблицу только пока она пуста, поэтому на живых базах список категорий иначе
# не меняется вовсе.
#
# «Грудные» и «Трапеция» — та же история, но последствия были хуже простой
# опечатки. CATALOG ищет категорию ПО ИМЕНИ, и незнакомое имя молча пропускает
# упражнение целиком (`category not in categories`), поэтому на прод не заехал
# ни один пресет груди и «Шраги со штангой». Год каталог выглядел наполненным,
# а половина груди в нём просто отсутствовала.
RENAMED_CATEGORIES = {
    "Трап.": "Трапеции",
    "Трапеция": "Трапеции",
    "Грудные": "Грудь",
}

# Пресеты-двойники: одно упражнение приехало из бота и из нового каталога под
# разными именами. Ключ — имя в базе, значение — каноническое имя из CATALOG.
#
# Половина пар отличается одной буквой «ё» («Жим штанги лежа» против «лёжа»), и
# это же было причиной, по которой снаряд им никогда не проставлялся: сверка идёт
# по имени, а строки разные. Вторая половина — просто разные формулировки одного
# движения.
#
# Историю слияние не задевает: подходы висят на exercise, а name и description
# лежат там снимком. Меняется только то, на какую карточку каталога смотрит
# упражнение дня.
MERGED_EXERCISES = {
    "Жим штанги лежа": "Жим штанги лёжа",
    "Жим штанги в наклоне": "Жим штанги на наклонной",
    "Присед со штангой": "Приседания со штангой",
    "Жим ногами в тренажёре": "Жим ногами",
    "Подъёмы на носки": "Подъём на носки",
    "Подъем штанги на бицепс": "Подъём штанги на бицепс",
    "Молотки гантелями": "Молотки",
    "Разгибания рук на верхнем блоке": "Разгибания на блоке",
    "Горизонтальная тяга блока": "Тяга горизонтального блока",
    "Тяга верхнего блока к груди": "Тяга верхнего блока",
    "Скручивания на полу": "Скручивания",
    "Разведение гантелей": "Махи гантелями в стороны",
    "Пресс в тренажере": "Пресс в тренажёре",
}


async def seed_catalog(session: AsyncSession) -> None:
    """
    Приводит каталог к CATALOG: досыпает недостающее, схлопывает двойников,
    чинит категории и снаряды.

    Порядок шагов не случаен. Категории идут первыми, потому что по их именам
    ищутся упражнения; слияние двойников — до досыпки, иначе канонические имена
    добавились бы ТРЕТЬИМИ карточками рядом с обеими старыми.

    Описания существующих пресетов не трогаем — их мог поменять админ. Имя,
    категорию и снаряд трогаем: руками их выставить негде, значит источник
    правды один — CATALOG.
    """
    await _rename_categories(session)

    categories = {c.name: c.id for c, _ in await orm_get_categories(session, user_id=0)}
    presets = await _merge_duplicates(session, await orm_get_admin_exercises(session))
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
        presets = await orm_get_admin_exercises(session)

    await _sync_cards(session, presets, categories)


async def _rename_categories(session: AsyncSession) -> None:
    """Переименовывает группы мышц, разошедшиеся с text_for_db."""
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


async def _merge_duplicates(session: AsyncSession, presets):
    """
    Схлопывает пресеты-двойники и возвращает актуальный список карточек.

    Два случая:

    1. Канонической карточки ещё нет — просто переименовываем старую. Так
       чинится «ё»: карточка та же самая, id тот же, ничего не переезжает.
    2. Обе есть — упражнения дня перецепляем на каноническую, старую удаляем.
       Порядок обязателен: у exercise.admin_exercise_id стоит ON DELETE CASCADE,
       и удаление карточки ДО перецепки снесло бы упражнения вместе с подходами.
    """
    by_name = {preset.name: preset for preset in presets}
    renamed = merged = 0

    for old_name, new_name in MERGED_EXERCISES.items():
        old = by_name.get(old_name)
        if old is None or old_name == new_name:
            continue

        canonical = by_name.get(new_name)
        if canonical is None:
            old.name = new_name
            by_name[new_name] = by_name.pop(old_name)
            renamed += 1
            continue

        await session.execute(
            text("UPDATE exercise SET admin_exercise_id = :new WHERE admin_exercise_id = :old"),
            {"new": canonical.id, "old": old.id},
        )
        await session.delete(old)
        by_name.pop(old_name)
        merged += 1

    if renamed or merged:
        await session.commit()
        logging.info("пресеты: переименовано %s, схлопнуто двойников %s", renamed, merged)
        return await orm_get_admin_exercises(session)

    return presets


async def _sync_cards(session: AsyncSession, presets, categories: dict) -> None:
    """
    Проставляет карточкам снаряд и категорию, а упражнениям дня — снимок снаряда.

    Три разных случая, и все про базы, заведённые до появления колонок:

    1. Пресеты каталога получили от миграции 'other', то есть шаг 2.5 на блоке
       и на гантелях. Догоняем их по названию — миграцией было бы нельзя, каталог
       наполняется кодом, и на разных контурах у одного упражнения разные id.
    2. Категория карточки разошлась с CATALOG: «Жим гантелей лёжа» лежал
       в «Прессе», «Становая тяга» — в «Ногах». В каталоге это выглядит поломкой,
       а поиск по группе мышц такое упражнение не находит.
    3. Упражнения, УЖЕ разложенные по дням, держат снимок снаряда (см. models.py).
       У старых строк там NULL — снимаем снимок с карточки каталога.

    Все проходы идемпотентны: второй запуск не находит работы.
    """
    stale = [
        preset for preset in presets
        if (want := EQUIPMENT_BY_NAME.get(preset.name)) is not None and preset.equipment != want
    ]
    for preset in stale:
        preset.equipment = EQUIPMENT_BY_NAME[preset.name]
    if stale:
        logging.info("снаряд проставлен пресетам: %s", len(stale))

    misfiled = [
        preset for preset in presets
        if (want := categories.get(CATEGORY_BY_NAME.get(preset.name))) is not None
        and preset.category_id != want
    ]
    for preset in misfiled:
        preset.category_id = categories[CATEGORY_BY_NAME[preset.name]]
    if misfiled:
        logging.info("категория поправлена пресетам: %s", len(misfiled))

    if stale or misfiled:
        await session.commit()

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
