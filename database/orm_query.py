from datetime import datetime

from sqlalchemy import select, update, delete, func, union_all
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from database.models import User, Banner, TrainingProgram, TrainingDay, Exercise, Set, AdminExercises, ExerciseCategory, \
    ExerciseSet, UserExercises


############### Работа с баннерами (информационными страницами) ###############

async def orm_add_banner_description(session: AsyncSession, data: dict):
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
    query = update(Banner).where(Banner.name == name).values(image=image)
    await session.execute(query)
    await session.commit()


async def orm_get_banner(session: AsyncSession, page: str):
    query = select(Banner).where(Banner.name == page)
    result = await session.execute(query)
    return result.scalar()


async def orm_get_info_pages(session: AsyncSession):
    query = select(Banner)
    result = await session.execute(query)
    return result.scalars().all()


############################ Программы ######################################

async def orm_add_program(session: AsyncSession, data: dict):
    obj = TrainingProgram(
        name=data['name'],
        user_id=data['user_id'],
    )
    session.add(obj)
    await session.commit()


async def orm_update_program(session: AsyncSession, program_id: int, data: dict):
    query = (
        update(TrainingProgram)
        .where(TrainingProgram.id == program_id)
        .values(name=data["name"])
    )
    await session.execute(query)
    await session.commit()


async def orm_get_programs(session: AsyncSession, user_id: int):
    query = select(TrainingProgram).filter(TrainingProgram.user_id == user_id)
    result = await session.execute(query)
    return result.scalars().all()


async def orm_get_program(session: AsyncSession, program_id: int):
    query = select(TrainingProgram).where(TrainingProgram.id == program_id)
    result = await session.execute(query)
    return result.scalar()


async def orm_delete_program(session: AsyncSession, program_id: int):
    await session.execute(delete(TrainingDay).where(TrainingDay.training_program_id == program_id))
    await session.execute(delete(TrainingProgram).where(TrainingProgram.id == program_id))
    await session.commit()


############################ Тренировочные дни ######################################

async def orm_add_training_day(session: AsyncSession, day_of_week: str, program_id: int):
    obj = TrainingDay(
        training_program_id=program_id,
        day_of_week=day_of_week,
    )
    session.add(obj)
    await session.commit()


async def orm_get_training_day(session: AsyncSession, training_day_id: int):
    query = select(TrainingDay).where(TrainingDay.id == training_day_id)
    result = await session.execute(query)
    return result.scalar()


async def orm_get_training_days(session: AsyncSession, training_program_id: int):
    query = select(TrainingDay).filter(TrainingDay.training_program_id == training_program_id)
    result = await session.execute(query)
    return result.scalars().all()


async def orm_delete_training_day(session: AsyncSession, training_day_id: int):
    query = delete(TrainingDay).where(TrainingDay.id == training_day_id)
    await session.execute(query)
    await session.commit()


############################ Упражнения ######################################

async def orm_add_exercise(session: AsyncSession, data: dict, training_day_id: int):
    result = await session.execute(
        select(func.max(Exercise.position))
        .where(Exercise.training_day_id == training_day_id)
        .execution_options(populate_existing=True)
    )
    max_position = result.scalar()
    if max_position is None:
        max_position = -1

    obj = Exercise(
        training_day_id=training_day_id,
        name=data['name'],
        description=data['description'],
        position=max_position + 1
    )

    session.add(obj)
    await session.commit()


async def orm_get_exercises(session: AsyncSession, training_day_id: int):
    query = select(Exercise).where(Exercise.training_day_id == training_day_id).order_by(Exercise.position)
    result = await session.execute(query)
    return result.scalars().all()


async def orm_get_exercise(session: AsyncSession, exercise_id: int):
    query = select(Exercise).where(Exercise.id == exercise_id)
    result = await session.execute(query)
    return result.scalar()


async def orm_update_exercise(session: AsyncSession, exercise_id: int, data: dict):
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

    query = (
        update(Exercise)
        .where(Exercise.id == exercise_id)
        .values(**update_data)
        .execution_options(synchronize_session="fetch")
    )
    await session.execute(query)
    await session.commit()


async def orm_delete_exercise(session: AsyncSession, exercise_id: int):
    query = delete(Exercise).where(Exercise.id == exercise_id)
    await session.execute(query)
    await session.commit()


async def move_exercise_up(session: AsyncSession, exercise_id: int):
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


async def orm_add_exercise_set(session: AsyncSession, exercise_id: int, reps: int):
    obj = ExerciseSet(
        exercise_id=exercise_id,
        reps=reps,
    )
    session.add(obj)
    await session.commit()


async def orm_get_exercise_set(session: AsyncSession, exercise_set_id: int):
    query = select(ExerciseSet).where(ExerciseSet.id == exercise_set_id)
    result = await session.execute(query)
    return result.scalar()


async def orm_get_exercise_sets(session: AsyncSession, exercise_id: int):
    query = (
        select(ExerciseSet)
        .where(ExerciseSet.exercise_id == exercise_id)
        .order_by(ExerciseSet.id)
    )
    result = await session.execute(query)
    return result.scalars().all()


async def orm_delete_exercise_set(session: AsyncSession, exercise_set_id: int):
    query = delete(ExerciseSet).where(ExerciseSet.id == exercise_set_id)
    await session.execute(query)
    await session.commit()


async def orm_update_exercise_set(session: AsyncSession, exercise_set_id: int, reps: int):
    query = (
        update(ExerciseSet)
        .where(ExerciseSet.id == exercise_set_id)
        .values(reps=reps)
    )
    await session.execute(query)
    await session.commit()


############################ Подходы ######################################

async def orm_add_set(session: AsyncSession, data: dict):
    obj = Set(
        exercise_id=data['exercise_id'],
        weight=data['weight'],
        repetitions=data['repetitions'],
        training_session_id=data['training_session_id'],
    )
    session.add(obj)
    await session.commit()


async def orm_get_sets(session: AsyncSession, exercise_id: int):
    query = select(Set).where(Set.exercise_id == exercise_id)
    result = await session.execute(query)
    return result.scalars().all()


async def orm_get_sets_by_session(session: AsyncSession, exercise_id: int, training_session_id: str):
    result = await session.execute(
        select(Set)
        .where(Set.exercise_id == exercise_id)
        .where(Set.training_session_id == training_session_id)
        .order_by(Set.id)
    )
    return result.scalars().all()


async def orm_get_set(session: AsyncSession, set_id: int):
    query = select(Set).where(Set.id == set_id)
    result = await session.execute(query)
    return result.scalar()


async def orm_update_set(session: AsyncSession, set_id: int, data: dict):
    query = (
        update(Set)
        .where(Set.id == set_id)
        .values(
            weight=data['weight'],
            repetitions=data['repetitions'],
        )
    )
    await session.execute(query)
    await session.commit()


############################ Шаблонные упражнения ######################################

async def orm_add_admin_exercise(session: AsyncSession, data: dict):
    obj = AdminExercises(
        name=data['name'],
        description=data['description'],
        category_id=int(data["category"]),
    )
    session.add(obj)
    await session.commit()


async def orm_get_admin_exercise(session: AsyncSession, admin_exercise_id: int):
    query = select(AdminExercises).where(AdminExercises.id == admin_exercise_id)
    result = await session.execute(query)
    return result.scalar()


async def orm_get_admin_exercises(session: AsyncSession):
    query = select(AdminExercises)
    result = await session.execute(query)
    return result.scalars().all()


async def orm_get_admin_exercises_in_category(session: AsyncSession, category_id: int):
    query = select(AdminExercises).where(AdminExercises.category_id == category_id)
    result = await session.execute(query)
    return result.scalars().all()


async def orm_update_admin_exercise(session: AsyncSession, admin_exercise_id: int, data: dict):
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


async def orm_delete_admin_exercise(session: AsyncSession, admin_exercise_id: int):
    query = delete(AdminExercises).where(AdminExercises.id == admin_exercise_id)
    await session.execute(query)
    await session.commit()


##################################Уникальные упражнения user###############################################
async def orm_add_user_exercise(session: AsyncSession, data: dict):
    obj = UserExercises(
        name=data['name'],
        description=data['description'],
        user_id=int(data["user_id"]),
        category_id=int(data["category_id"]),
    )
    session.add(obj)
    await session.commit()


async def orm_get_user_exercise(session: AsyncSession, user_exercise_id: int):
    query = select(UserExercises).where(UserExercises.id == user_exercise_id)
    result = await session.execute(query)
    return result.scalar()


async def orm_get_user_exercises(session: AsyncSession, user_id: int):
    query = select(UserExercises).filter(UserExercises.user_id == user_id)
    result = await session.execute(query)
    return result.scalars().all()


async def orm_get_user_exercises_in_category(session: AsyncSession, category_id: int, user_id: int):
    query = ((select(UserExercises)
              .where(UserExercises.category_id == category_id))
             .where(UserExercises.user_id == user_id))
    result = await session.execute(query)
    return result.scalars().all()


async def orm_update_user_exercise(session: AsyncSession, user_exercise_id: int, data: dict):
    query = (
        update(UserExercises)
        .where(UserExercises.id == user_exercise_id)
        .values(
            name=data['name'],
            description=data['description'],
            category_id=int(data["category"])
        )
    )
    await session.execute(query)
    await session.commit()


async def orm_delete_user_exercise(session: AsyncSession, user_exercise_id: int):
    query = delete(UserExercises).where(UserExercises.id == user_exercise_id)
    await session.execute(query)
    await session.commit()


##################### Категории упражнений #####################################
async def orm_get_categories(session: AsyncSession, user_id: int):
    """
    Получает список категорий упражнений с количеством упражнений в каждой категории,
    включая административные и пользовательские упражнения для конкретного пользователя.

    :param session: Асинхронная сессия SQLAlchemy.
    :param user_id: Идентификатор пользователя, для которого считаются пользовательские упражнения.
    :return: Список кортежей (ExerciseCategory, exercise_count).
    """
    # Подзапрос для административных упражнений
    admin_select = select(
        AdminExercises.category_id.label('category_id')
    )

    # Подзапрос для пользовательских упражнений, фильтрованных по user_id
    user_select = select(
        UserExercises.category_id.label('category_id')
    ).where(
        UserExercises.user_id == user_id
    )

    # Объединение подзапросов с помощью UNION ALL
    combined_subquery = union_all(admin_select, user_select).subquery()

    # Основной запрос: подсчёт количества упражнений в каждой категории
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

    # Выполнение запроса
    result = await session.execute(query)
    return result.all()
async def orm_get_category(session: AsyncSession, category_id: int):
    query = (
        select(ExerciseCategory).where(ExerciseCategory.id == category_id)
    )
    result = await session.execute(query)
    return result.scalar()


async def orm_create_categories(session: AsyncSession, categories: list):
    query = select(ExerciseCategory)
    result = await session.execute(query)
    if result.first():
        return
    session.add_all([ExerciseCategory(name=name) for name in categories])
    await session.commit()


##################### Добавляем юзера в БД #####################################

async def orm_add_user(session: AsyncSession, data: dict):
    user = User(
        user_id=data['user_id'],
        name=data['name'],
        weight=data['weight'],
    )
    session.add(user)
    await session.commit()


async def orm_update_user(session: AsyncSession, user_id: int, data: dict):
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
    query = select(User)
    result = await session.execute(query)
    return result.scalars().all()


async def orm_turn_on_off_program(session: AsyncSession, user_id: int, program_id: int | None = None):
    query = (
        update(User)
        .where(User.user_id == user_id)
        .values(
            actual_program_id=program_id,
        )
    )
    await session.execute(query)
    await session.commit()


async def orm_get_user_by_id(session: AsyncSession, user_id: int):
    result = await session.execute(
        select(User).where(User.user_id == user_id)
    )
    return result.scalars().first()


async def initialize_positions_for_training_day(session: AsyncSession, training_day_id: int):
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


async def initialize_all_positions(session: AsyncSession):
    result = await session.execute(
        select(Exercise.training_day_id).distinct()
    )
    training_day_ids = [row[0] for row in result.fetchall()]

    for training_day_id in training_day_ids:
        await initialize_positions_for_training_day(session, training_day_id)
