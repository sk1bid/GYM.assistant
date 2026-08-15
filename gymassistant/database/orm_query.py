from sqlalchemy import select, update, delete, func, union_all, and_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    User,
    Banner,
    TrainingProgram,
    TrainingDay,
    Exercise,
    Set,
    AdminExercises,
    ExerciseCategory,
    UserExercises, TrainingSession
)
from services.equipment import OTHER

async def _one(session: AsyncSession, stmt):
    res = await session.execute(stmt)
    return res.unique().scalars().first()

async def _all(session: AsyncSession, stmt):
    res = await session.execute(stmt)
    return res.scalars().all() 

"""
Работа с изображениями
"""


async def orm_add_banner_description(session: AsyncSession, data: dict):
    """
    Добавляет в бд названия словарей
    :param session:
    :param data: Словарь (название уровня| его описание)
    :return:
    """
    for name, description in data.items():
        query = select(Banner).where(Banner.name == name)
        result = await session.execute(query)
        banner = result.scalars().first()
        if banner:
            banner.description = description
        else:
            session.add(Banner(name=name, description=description))
    await session.commit()


async def orm_change_banner_image(session: AsyncSession, name: str, image: str):
    """
    Изменяет изображение для определенной страницы
    :param session:
    :param name: имя страницы/уровня
    :param image:
    :return:
    """
    query = update(Banner).where(Banner.name == name).values(image=image)
    await session.execute(query)
    await session.commit()


async def orm_get_banner(session: AsyncSession, page: str):
    """
    Получаем баннер(изображение + описание)
    :param session:
    :param page:
    :return:
    """
    stmt = select(Banner).where(Banner.name == page).limit(1)
    return await _one(session, stmt)


async def orm_get_info_pages(session: AsyncSession):
    """
    Получаем имена баннеров(уровень) для добавления картинок через админ панель
    :param session:
    :return:
    """
    query = select(Banner)
    result = await session.execute(query)
    return result.scalars().all()


"""
Программы тренировок
"""


async def orm_add_program(session: AsyncSession, data: dict):
    """
    Добавляем программу тренировок
    :param session:
    :param data: название программы и id пользователя(tg_id)
    :return:
    """
    obj = TrainingProgram(
        name=data['name'],
        user_id=data['user_id'],
    )
    session.add(obj)
    await session.commit()


async def orm_update_program(session: AsyncSession, program_id: int, data: dict):
    """
    Обновляем созданную программу тренировок
    :param session:
    :param program_id:
    :param data: имя программы тренировок
    :return:
    """
    query = (
        update(TrainingProgram)
        .where(TrainingProgram.id == program_id)
        .values(name=data["name"])
    )
    await session.execute(query)
    await session.commit()


async def orm_get_programs(session: AsyncSession, user_id: int):
    """
    Получаем все программы пользователя
    :param session:
    :param user_id: Telegram ID
    :return:
    """
    query = select(TrainingProgram).filter(TrainingProgram.user_id == user_id)
    result = await session.execute(query)
    return result.scalars().all()


async def orm_get_program(session: AsyncSession, program_id: int):
    """
    Получаем определенную программу пользователя, по id программы
    :param session:
    :param program_id:
    :return:
    """
    stmt = select(TrainingProgram).where(TrainingProgram.id == program_id).limit(1)
    return await _one(session, stmt)

async def orm_delete_program(session: AsyncSession, program_id: int):
    """
    Удаляем программу пользователя
    :param session:
    :param program_id:
    :return:
    """
    query = delete(TrainingProgram).where(TrainingProgram.id == program_id)
    await session.execute(query)
    await session.commit()


"""
Тренировочные дни
"""


async def orm_add_training_day(session: AsyncSession, day_of_week: str, program_id: int):
    """
    Добавляем день недели
    :param session:
    :param day_of_week: строковое значение дня недели (Понедельник, Вт, ...)
    :param program_id:
    :return:
    """
    obj = TrainingDay(
        training_program_id=program_id,
        day_of_week=day_of_week,
    )
    session.add(obj)
    await session.commit()


async def orm_get_training_day(session: AsyncSession, training_day_id: int):
    """
    Получаем день недели по его id
    :param session:
    :param training_day_id:
    :return:
    """
    stmt = select(TrainingDay).where(TrainingDay.id == training_day_id).limit(1)
    return await _one(session, stmt)


async def orm_get_training_days(session: AsyncSession, training_program_id: int):
    """
    Получаем все дни пользователя
    :param session:
    :param training_program_id:
    :return:
    """
    query = select(TrainingDay).filter(TrainingDay.training_program_id == training_program_id)
    result = await session.execute(query)
    return result.scalars().all()


async def orm_delete_training_day(session: AsyncSession, training_day_id: int):
    """
    Удаляем тренировочный день
    :param session:
    :param training_day_id:
    :return:
    """
    query = delete(TrainingDay).where(TrainingDay.id == training_day_id)
    await session.execute(query)
    await session.commit()


"""
Упражнения
"""


async def orm_add_exercise(session: AsyncSession, data: dict, training_day_id: int, exercise_type: str):
    """
    Добавляем новое упражнение в базу данных
    :param session:
    :param data:
    :param training_day_id:
    :param exercise_type: user(пользовательское) или admin(предустановленное)
    """

    result = await session.execute(
        select(func.max(Exercise.position))
        .where(Exercise.training_day_id == training_day_id)
    )
    max_position = result.scalar()
    if max_position is None:
        max_position = -1

    admin_exercise_id = None
    user_exercise_id = None

    if exercise_type == 'admin':
        if 'admin_exercise_id' not in data or data['admin_exercise_id'] is None:
            raise ValueError("admin_exercise_id должен быть указан для администраторских упражнений.")
        admin_exercise_id = data['admin_exercise_id']
    elif exercise_type == 'user':
        if 'user_exercise_id' not in data or data['user_exercise_id'] is None:
            raise ValueError("user_exercise_id должен быть указан для пользовательских упражнений.")
        user_exercise_id = data['user_exercise_id']
    else:
        raise ValueError("Неверный тип упражнения. Должно быть 'admin' или 'user'.")

    obj = Exercise(
        training_day_id=training_day_id,
        name=data['name'],
        description=data['description'],
        position=max_position + 1,
        admin_exercise_id=admin_exercise_id,
        user_exercise_id=user_exercise_id,
        circle_training=data.get('circle_training', False),
        # Снимок с карточки каталога — как name и description строкой выше.
        equipment=data.get('equipment'),
    )

    session.add(obj)
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        raise e


async def orm_get_exercises(session: AsyncSession, training_day_id: int):
    """
    Получаем все упражнения, использованными пользователем в определенный день
    :param session:
    :param training_day_id:
    :return:
    """
    query = select(Exercise).where(Exercise.training_day_id == training_day_id).order_by(Exercise.position)
    result = await session.execute(query)
    return result.scalars().all()


async def orm_get_circular_exercises(session: AsyncSession, training_day_id: int):
    """
    Получаем все круговые упражнения, использованными пользователем в определенный день
    :param session:
    :param training_day_id:
    :return:
    """
    query = (
        select(Exercise)
        .where(and_(Exercise.training_day_id == training_day_id, Exercise.circle_training.is_(True)))
        .order_by(Exercise.position)
    )
    result = await session.execute(query)
    return result.scalars().all()


async def orm_get_standard_exercises(session: AsyncSession, training_day_id: int):
    """
    Получаем все стандартные упражнения, использованными пользователем в определенный день
    :param session:
    :param training_day_id:
    :return:
    """
    query = (
        select(Exercise)
        .where(and_(Exercise.training_day_id == training_day_id, Exercise.circle_training.is_(False)))
        .order_by(Exercise.position)
    )
    result = await session.execute(query)
    return result.scalars().all()


async def orm_get_exercise(session: AsyncSession, exercise_id: int):
    """
    Получаем определенное упражнение по его id
    :param session:
    :param exercise_id:
    :return:
    """
    stmt = select(Exercise).where(Exercise.id == exercise_id).limit(1)
    return await _one(session, stmt)


async def orm_update_exercise(session: AsyncSession, exercise_id: int, data: dict):
    """
    Обновляем информацию об упражнении, найденном по его id
    :param session:
    :param exercise_id:
    :param data: Возможные значения: имя, описание, базовое кол-во повторений, базовое кол-во подходов, id дня,
     круговое упражнение или нет(bool)
    :return:
    """
    update_data = {}

    if 'name' in data:
        update_data['name'] = data['name']
    if 'description' in data:
        update_data['description'] = data['description']
    if 'reps' in data:
        update_data['base_reps'] = data['reps']
    if 'sets' in data:
        update_data['base_sets'] = data['sets']
    if 'training_day_id' in data:
        update_data['training_day_id'] = data['training_day_id']
    if 'circle_training' in data:
        update_data['circle_training'] = data['circle_training']
    if 'weight_step' in data:
        # Ноль приходит вместо NULL — так клиент сбрасывает шаг обратно на снаряд.
        update_data['weight_step'] = data['weight_step'] or None

    # Управление типом упражнения при обновлении
    if 'exercise_type' in data:
        exercise_type = data['exercise_type']
        if exercise_type == 'admin':
            if 'admin_exercise_id' not in data or data['admin_exercise_id'] is None:
                raise ValueError("admin_exercise_id должен быть указан для администраторских упражнений.")
            update_data['admin_exercise_id'] = data['admin_exercise_id']
            update_data['user_exercise_id'] = None
        elif exercise_type == 'user':
            if 'user_exercise_id' not in data or data['user_exercise_id'] is None:
                raise ValueError("user_exercise_id должен быть указан для пользовательских упражнений.")
            update_data['user_exercise_id'] = data['user_exercise_id']
            update_data['admin_exercise_id'] = None
        else:
            raise ValueError("Неверный тип упражнения. Должно быть 'admin' или 'user'.")

    query = (
        update(Exercise)
        .where(Exercise.id == exercise_id)
        .values(**update_data)
        .execution_options(synchronize_session="fetch")
    )
    await session.execute(query)
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        raise e


async def orm_delete_exercise(session: AsyncSession, exercise_id: int):
    """
    Удаляем упражнение из базы
    :param session:
    :param exercise_id:
    :return:
    """
    query = delete(Exercise).where(Exercise.id == exercise_id)
    await session.execute(query)
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        raise e


async def move_exercise_up(session: AsyncSession, exercise_id: int):
    """
    Поднимаем порядок упражнения в тренировочном дне
    :param session:
    :param exercise_id:
    :return:
    """
    exercise = await session.get(Exercise, exercise_id)
    if not exercise:
        return "Упражнение не найдено."

    exercises_result = await session.execute(
        select(Exercise)
        .where(Exercise.training_day_id == exercise.training_day_id)
        .order_by(Exercise.position)
    )
    exercises = exercises_result.scalars().all()

    index = next((i for i, e in enumerate(exercises) if e.id == exercise_id), None)
    if index is None:
        return "Упражнение не найдено в списке."

    if index == 0:
        return "Упражнение уже на первой позиции."

    previous_exercise = exercises[index - 1]
    exercise.position, previous_exercise.position = previous_exercise.position, exercise.position

    session.add_all([exercise, previous_exercise])
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        print("Ошибка при перемещении вверх:", e)
        return "Ошибка при перемещении вверх."

    return "Упражнение перемещено вверх."


async def move_exercise_down(session: AsyncSession, exercise_id: int):
    """
    Опускаем порядок упражнения в тренировочном дне
    :param session:
    :param exercise_id:
    :return:
    """
    exercise = await session.get(Exercise, exercise_id)
    if not exercise:
        return "Упражнение не найдено."

    exercises_result = await session.execute(
        select(Exercise)
        .where(Exercise.training_day_id == exercise.training_day_id)
        .order_by(Exercise.position)
    )
    exercises = exercises_result.scalars().all()

    index = next((i for i, e in enumerate(exercises) if e.id == exercise_id), None)
    if index is None:
        return "Упражнение не найдено в списке."

    if index == len(exercises) - 1:
        return "Упражнение уже на последней позиции."

    next_exercise = exercises[index + 1]
    exercise.position, next_exercise.position = next_exercise.position, exercise.position

    session.add_all([exercise, next_exercise])
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        print("Ошибка при перемещении вниз:", e)
        return "Ошибка при перемещении вниз."

    return "Упражнение перемещено вниз."


"""
Шаблонные подходы
"""


async def orm_add_exercise_set(session: AsyncSession, exercise_id: int, reps: int):
    """
    Добавляем шаблонный подход к упражнению
    :param session:
    :param exercise_id:
    :param reps: кол-во повторений
    :return:
    """
    from database.models import ExerciseSet
    obj = ExerciseSet(
        exercise_id=exercise_id,
        reps=reps,
    )
    session.add(obj)
    await session.commit()


async def orm_get_exercise_set(session: AsyncSession, exercise_set_id: int):
    """
    Получаем шаблонный подход по его id
    :param session:
    :param exercise_set_id:
    :return:
    """
    from database.models import ExerciseSet
    query = select(ExerciseSet).where(ExerciseSet.id == exercise_set_id)
    result = await session.execute(query)
    return result.scalar()


async def orm_get_exercise_sets(session: AsyncSession, exercise_id: int):
    """
    Получаем все шаблонные подходы для упражнения пользователя
    :param session:
    :param exercise_id:
    :return:
    """
    from database.models import ExerciseSet
    query = (
        select(ExerciseSet)
        .where(ExerciseSet.exercise_id == exercise_id)
        .order_by(ExerciseSet.id)
    )
    result = await session.execute(query)
    return result.scalars().all()


async def orm_delete_exercise_set(session: AsyncSession, exercise_set_id: int):
    """
    Удаляем из базы шаблонный подход
    :param session:
    :param exercise_set_id:
    :return:
    """
    from database.models import ExerciseSet
    stmt = select(ExerciseSet).where(ExerciseSet.id == exercise_set_id).limit(1)
    return await _one(session, stmt)


async def orm_update_exercise_set(session: AsyncSession, exercise_set_id: int, reps: int):
    """
    Обновляем информацию о шаблонном подходе
    :param session:
    :param exercise_set_id:
    :param reps: кол-во поторений
    :return:
    """
    from database.models import ExerciseSet
    query = (
        update(ExerciseSet)
        .where(ExerciseSet.id == exercise_set_id)
        .values(reps=reps)
    )
    await session.execute(query)
    await session.commit()


"""
Подходы
"""


async def orm_add_set(session: AsyncSession, data: dict):
    """
    Добавляем уже отработанный подход
    :param session:
    :param data: вес, повторения, uuid тренировки
    :return:
    """
    obj = Set(
        exercise_id=data['exercise_id'],
        weight=data['weight'],
        repetitions=data['repetitions'],
        training_session_id=data['training_session_id'],
    )
    session.add(obj)
    await session.commit()


async def orm_get_sets(session: AsyncSession, exercise_id: int):
    """
    Получаем отработанные подходы для упражнения пользователя
    :param session:
    :param exercise_id:
    :return:
    """
    query = select(Set).where(Set.exercise_id == exercise_id)
    result = await session.execute(query)
    return result.scalars().all()


async def orm_get_set(session: AsyncSession, set_id: int):
    """
    Получаем определенный отработанный подход
    :param session:
    :param set_id:
    :return:
    """
    stmt = select(Set).where(Set.id == set_id).limit(1)
    return await _one(session, stmt)


async def orm_get_sets_by_session(session: AsyncSession, exercise_id: int, training_session_id: str):
    """
    Получаем отработанные подходы для определенной тренировки
    :param session:
    :param exercise_id:
    :param training_session_id:
    :return:
    """
    result = await session.execute(
        select(Set)
        .where(Set.exercise_id == exercise_id)
        .where(Set.training_session_id == training_session_id)
        .order_by(Set.id)
    )
    return result.scalars().all()


async def orm_get_all_sets_by_user_id_grouped_by_date(session: AsyncSession, user_id: int):
    """
    Получает все отработанные подходы для заданного user_id, сгруппированные по дате
    :param session:
    :param user_id: Telegram ID
    :return:
    """
    from sqlalchemy import func, cast, Date

    result = await session.execute(
        select(
            cast(Set.created, Date).label("set_date"),
            func.array_agg(Set.id).label("set_ids")
        )
        .join(Exercise, Set.exercise_id == Exercise.id)
        .join(TrainingDay, Exercise.training_day_id == TrainingDay.id)
        .join(TrainingProgram, TrainingDay.training_program_id == TrainingProgram.id)
        .join(User, TrainingProgram.user_id == User.user_id)
        .where(User.user_id == user_id)
        .group_by(cast(Set.created, Date))
        .order_by(cast(Set.created, Date))
    )
    return result.all()


async def orm_get_exercise_max_record(
        session: AsyncSession,
        user_id: int,
        exercise_id: int
):
    """
    Получает максимальное значение (вес * повторения) для конкретного упражнения пользователя,
    используя связь с TrainingSession и возвращая 0, если подходов не найдено.
    """
    stmt = (
        select(func.coalesce(func.max(Set.weight * Set.repetitions), 0))
        .join(Exercise, Set.exercise_id == Exercise.id)
        .join(TrainingSession, Set.training_session_id == TrainingSession.id)
        .where(
            TrainingSession.user_id == user_id,
            Exercise.id == exercise_id
        )
    )
    return await _one(session, stmt)


async def orm_get_exercise_max_weight(
        session: AsyncSession,
        user_id: int,
        exercise_id: int
):
    """
    Получает максимальный вес для конкретного упражнения пользователя,
    используя связь с TrainingSession и возвращая 0, если подходов не найдено.
    """
    stmt = (
        select(func.coalesce(func.max(Set.weight), 0))
        .join(Exercise, Set.exercise_id == Exercise.id)
        .join(TrainingSession, Set.training_session_id == TrainingSession.id)
        .where(
            TrainingSession.user_id == user_id,
            Exercise.id == exercise_id
        )
    )
    return await _one(session, stmt)


async def orm_get_sets_for_exercise_in_previous_session(
    session: AsyncSession,
    exercise_id: int,
    current_session_id: int | None = None
):
    """
    Получает подходы из последней завершённой сессии, исключая текущую (если указана).
    """

    subquery = (
        select(TrainingSession.id)
        .join(Set, TrainingSession.id == Set.training_session_id)
        .where(
            Set.exercise_id == exercise_id,
            # Исключаем текущую сессию, если она передана
            TrainingSession.id != current_session_id if current_session_id else True
        )
        .order_by(TrainingSession.date.desc())
        .limit(1)
        .scalar_subquery()
    )

    query = select(Set).where(
        Set.training_session_id == subquery,
        Set.exercise_id == exercise_id
    )

    result = await session.execute(query)
    return result.scalars().all()

"""
Предустановленные упражнения
"""


async def orm_add_admin_exercise(session: AsyncSession, data: dict):
    """
    Добавляем предустановленное упражнение
    :param session:
    :param data: название упражнения, описание, id категории, снаряд
    :return:
    """
    obj = AdminExercises(
        name=data['name'],
        description=data['description'],
        category_id=int(data["category"]),
        equipment=data.get('equipment', OTHER),
    )
    session.add(obj)
    await session.commit()


async def orm_get_admin_exercise(session: AsyncSession, admin_exercise_id: int):
    """
    Получаем предустановленное упражнение
    :param session:
    :param admin_exercise_id:
    :return:
    """
    stmt = select(AdminExercises).where(AdminExercises.id == admin_exercise_id).limit(1)
    return await _one(session, stmt)


async def orm_get_admin_exercises(session: AsyncSession):
    """
    Получаем предустановленное упражнение
    :param session:
    :return:
    """
    query = select(AdminExercises)
    result = await session.execute(query)
    return result.scalars().all()


async def orm_get_admin_exercises_in_category(session: AsyncSession, category_id: int):
    """
    Получаем предустановленные упражнения в категории
    :param session:
    :param category_id:
    :return:
    """
    query = select(AdminExercises).where(AdminExercises.category_id == category_id)
    result = await session.execute(query)
    return result.scalars().all()


async def orm_update_admin_exercise(session: AsyncSession, admin_exercise_id: int, data: dict):
    """
    Обновляем предустановленное упражнение
    :param session:
    :param admin_exercise_id:
    :param data: название упражнения, описание, id категории
    :return:
    """
    query = (
        update(AdminExercises)
        .where(AdminExercises.id == admin_exercise_id)
        .values(
            name=data['name'],
            description=data['description'],
            category_id=int(data["category"])
        )
    )
    await session.execute(query)
    await session.commit()


async def orm_delete_admin_exercise(session: AsyncSession, admin_exercise_id):
    """
    Удаляем предустановленное упражнение
    :param session:
    :param admin_exercise_id:
    :return:
    """
    admin_exercise = await session.get(AdminExercises, admin_exercise_id)
    if admin_exercise:
        await session.delete(admin_exercise)
        try:
            await session.commit()
        except Exception as e:
            await session.rollback()
            raise e


"""
Пользовательские упражнения
"""


async def orm_add_user_exercise(session: AsyncSession, data: dict):
    """
    Добавляем пользовательское упражнение
    :param session:
    :param data: Название упражнения, описание, Telegram ID, ID категории, снаряд
    :return:
    """
    obj = UserExercises(
        name=data['name'],
        description=data['description'],
        user_id=int(data["user_id"]),
        category_id=int(data["category_id"]),
        equipment=data.get('equipment', OTHER),
    )
    session.add(obj)
    await session.commit()


async def orm_get_user_exercise(session: AsyncSession, user_exercise_id: int):
    """
    Получаем пользовательское упражнение по его id
    :param session:
    :param user_exercise_id:
    :return:
    """
    stmt = select(UserExercises).where(UserExercises.id == user_exercise_id).limit(1)
    return await _one(session, stmt)


async def orm_get_user_exercises(session: AsyncSession, user_id: int):
    """
    Получаем все пользовательские упражнения по tg_id
    :param session:
    :param user_id: Telegram ID
    :return:
    """
    query = select(UserExercises).filter(UserExercises.user_id == user_id)
    result = await session.execute(query)
    return result.scalars().all()


async def orm_get_user_exercises_in_category(session: AsyncSession, category_id: int, user_id: int):
    """
    Получаем все пользовательские упражнения в определенной категории
    :param session:
    :param category_id:
    :param user_id: Telegram ID
    :return:
    """
    query = (
        select(UserExercises)
        .where(and_(UserExercises.category_id == category_id, UserExercises.user_id == user_id))
    )
    result = await session.execute(query)
    return result.scalars().all()


async def orm_update_user_exercise(session: AsyncSession, user_exercise_id: int, data: dict):
    """
    Обновляем информацию о пользовательском упражнении
    :param session:
    :param user_exercise_id:
    :param data: Название упражнения, описание, ID категории, снаряд
    :return:
    """
    query = (
        update(UserExercises)
        .where(UserExercises.id == user_exercise_id)
        .values(
            name=data['name'],
            description=data['description'],
            category_id=int(data["category"]),
            equipment=data.get('equipment', OTHER),
        )
    )
    await session.execute(query)

    # Снаряд, в отличие от названия и описания, догоняет уже разложенные по дням
    # копии. Названием пользователь подписывает упражнение, и старая подпись в старой
    # программе — его дело; снаряд же читает интерфейс, и ровно за этим его и правят:
    # «у меня это блок, а кнопки ходят по 2.5».
    await session.execute(
        update(Exercise)
        .where(Exercise.user_exercise_id == user_exercise_id)
        .values(equipment=data.get('equipment', OTHER))
    )
    await session.commit()


async def orm_delete_user_exercise(session: AsyncSession, user_exercise_id: int):
    """
    Удаляем пользовательское упражнение из базы
    :param session:
    :param user_exercise_id:
    :return:
    """
    query = delete(UserExercises).where(UserExercises.id == user_exercise_id)
    await session.execute(query)
    await session.commit()


"""
Категории упражнений
"""


async def orm_get_categories(session: AsyncSession, user_id: int):
    """
    Получаем категории упражнений
    :param session:
    :param user_id: Telegram ID
    :return:
    """
    admin_select = select(
        AdminExercises.category_id.label('category_id')
    )

    user_select = select(
        UserExercises.category_id.label('category_id')
    ).where(
        UserExercises.user_id == user_id
    )

    combined_subquery = union_all(admin_select, user_select).subquery()

    query = (
        select(
            ExerciseCategory,
            func.count(combined_subquery.c.category_id).label("exercise_count")
        )
        .outerjoin(
            combined_subquery,
            ExerciseCategory.id == combined_subquery.c.category_id
        )
        .group_by(ExerciseCategory.id)
        .order_by(ExerciseCategory.name)
    )

    result = await session.execute(query)
    return result.all()


async def orm_get_category(session: AsyncSession, category_id: int):
    """
    Получаем определенную категорию упражнений
    :param session:
    :param category_id:
    :return:
    """
    stmt = select(ExerciseCategory).where(ExerciseCategory.id == category_id).limit(1)
    return await _one(session, stmt)

async def orm_create_categories(session: AsyncSession, categories: list):
    """
    Создаем категории упражнений по их названиям
    :param session:
    :param categories: список из названий категорий
    :return:
    """
    query = select(ExerciseCategory)
    result = await session.execute(query)
    if result.first():
        return
    session.add_all([ExerciseCategory(name=name) for name in categories])
    await session.commit()


"""
Тренировка
"""


async def orm_add_training_session(session: AsyncSession, data: dict):
    """
    Добавляем новую запись о тренировке
    :param session:
    :param data: Telegram ID, Дата, Описание
    :return:
    """

    new_session = TrainingSession(
        user_id=data["user_id"],
        date=data.get("date"),
        note=data.get("note", "")
    )
    session.add(new_session)
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        raise e
    return new_session


async def orm_get_training_session(session: AsyncSession, session_id: str):
    """
    Получаем тренировку по её UUID
    """
    from database.models import TrainingSession
    stmt = select(TrainingSession).where(TrainingSession.id == session_id).limit(1)
    return await _one(session, stmt)

async def orm_get_training_sessions_by_user(session: AsyncSession, user_id: int):
    """
    Получаем все тренировки пользователя отсортированные по дате
    """
    from database.models import TrainingSession
    query = (
        select(TrainingSession)
        .where(TrainingSession.user_id == user_id)
        .order_by(TrainingSession.date.desc())
    )
    result = await session.execute(query)
    return result.scalars().all()


async def orm_delete_training_session(session: AsyncSession, session_id: str):
    """
    Удаляем запись о тренировке
    :param session:
    :param session_id: uuid
    :return:
    """
    from database.models import TrainingSession
    query = delete(TrainingSession).where(TrainingSession.id == session_id)
    await session.execute(query)
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        raise e


async def orm_update_training_session(session: AsyncSession, session_id: str, data: dict):
    """
    Обновляем запись о тренировке
    """
    from database.models import TrainingSession
    update_data = {}
    if "date" in data:
        update_data["date"] = data["date"]
    if "note" in data:
        update_data["note"] = data["note"]

    query = (
        update(TrainingSession)
        .where(TrainingSession.id == session_id)
        .values(**update_data)
    )
    await session.execute(query)
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        raise e


"""
Пользователь
"""


async def orm_add_user(session: AsyncSession, data: dict):
    """
    Добавляем запись о пользователе
    :param session:
    :param data:
    :return: Telegram ID, Имя, Вес
    """
    user = User(
        user_id=data['user_id'],
        name=data['name'],
        weight=data['weight'],
    )
    session.add(user)
    await session.commit()


async def orm_update_user(session: AsyncSession, user_id: int, data: dict):
    """
    Обновляем запись о пользователе
    :param session:
    :param user_id:
    :param data: Имя, Вес
    :return:
    """
    query = (
        update(User)
        .where(User.user_id == user_id)
        .values(
            name=data['name'],
            weight=data['weight'],
        )
    )
    await session.execute(query)
    await session.commit()


async def orm_get_all_users(session: AsyncSession):
    """
    Получаем всех пользователей бота
    :param session:
    :return:
    """
    query = select(User)
    result = await session.execute(query)
    return result.scalars().all()


async def orm_get_user_by_id(session: AsyncSession, user_id: int):
    """
    Получаем пользователя по его tg_id
    :param session:
    :param user_id: Telegram ID
    :return:
    """
    stmt = select(User).where(User.user_id == user_id).limit(1)
    return await _one(session, stmt)


"""
Служебные функции
"""


async def orm_turn_on_off_program(session: AsyncSession, user_id: int, program_id: int | None = None):
    """
    Включаем/Выключаем программу тренировок
    :param session:
    :param user_id: Telegram ID
    :param program_id:
    :return:
    """
    query = (
        update(User)
        .where(User.user_id == user_id)
        .values(
            actual_program_id=program_id,
        )
    )
    await session.execute(query)
    await session.commit()


async def initialize_positions_for_training_day(session: AsyncSession, training_day_id: int):
    """
    Инициализируем позиции в тренировочном дне
    :param session:
    :param training_day_id:
    :return:
    """
    result = await session.execute(
        select(Exercise)
        .where(Exercise.training_day_id == training_day_id)
        .order_by(Exercise.id)
    )
    exercises = result.scalars().all()

    for index, exercise in enumerate(exercises):
        exercise.position = index
        session.add(exercise)

    await session.commit()
