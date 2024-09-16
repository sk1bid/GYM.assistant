from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from GYM_assistant.database.models import User, Banner, Training_Program, Training_Day, Exercise, Set


############### Работа с баннерами (информационными страницами) ###############

async def orm_add_banner_description(session: AsyncSession, data: dict):
    # Добавляем новый или изменяем существующий по именам
    # пунктов меню: main, about, cart, shipping, payment, catalog
    query = select(Banner)
    result = await session.execute(query)
    if result.first():
        return
    session.add_all([Banner(name=name, description=description) for name, description in data.items()])
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
    obj = Training_Program(
        name=data['name'],
        description=data['description'],
    )
    session.add(obj)
    await session.commit()


async def orm_update_program(session: AsyncSession, program_id: int, data: dict):
    query = (
        update(Training_Program)
        .where(Training_Program.id == program_id)
        .values(
            name=data["name"],
            description=data["description"],
        )
    )
    await session.execute(query)
    await session.commit()


async def orm_get_programs(session: AsyncSession, user_id: int):
    query = select(Training_Program).filter(Training_Program.user_id == user_id)
    result = await session.execute(query)
    return result.scalars().all()


async def orm_get_program(session: AsyncSession, program_id: int):
    query = select(Training_Program).where(Training_Program.id == program_id)
    result = await session.execute(query)
    return result.scalar()


async def orm_delete_program(session: AsyncSession, program_id: int):
    query = (
        delete(Training_Program).where(Training_Program.id == program_id)
    )
    await session.execute(query)
    await session.commit()


############################ Тренировочные дни ######################################

async def orm_add_training_day(session: AsyncSession, data: dict, program_id: int):
    obj = Training_Day(
        training_program_id=program_id,
        day_of_week=str(data['day_of_week']),
    )
    session.add(obj)
    await session.commit()


async def orm_get_training_day(session: AsyncSession, training_day_id: int):
    query = select(Training_Day).where(Training_Day.id == training_day_id)
    result = await session.execute(query)
    return result.scalar()


async def orm_get_training_days(session: AsyncSession, training_program_id: int):
    query = select(Training_Day).filter(Training_Day.training_program_id == training_program_id)
    result = await session.execute(query)
    return result.scalars().all()


async def orm_delete_training_day(session, training_day_id: int):
    query = (
        delete(Training_Day).where(Training_Day.id == training_day_id)
    )
    await session.execute(query)
    await session.commit()


############################ Упражнения ######################################

async def orm_add_exercise(session: AsyncSession, data: dict, training_day_id: int):
    obj = Exercise(
        training_day_id=training_day_id,
        name=data['name'],
        description=data['description'],
        image=data['image'],
    )
    session.add(obj)
    await session.commit()


async def orm_get_exercises(session: AsyncSession, training_day_id: int):
    query = select(Exercise).where(Exercise.training_day_id == int(training_day_id))
    result = await session.execute(query)
    return result.scalars().all()


async def orm_get_exercise(session: AsyncSession, exercise_id: int):
    query = select(Exercise).where(Exercise.id == int(exercise_id))
    result = await session.execute(query)
    return result.scalar()


async def orm_update_exercise(session: AsyncSession, exercise_id: int, data: dict):
    query = (
        update(Exercise)
        .where(Exercise.id == exercise_id)
        .values(
            name=data['name'],
            description=data['description'],
            image=data['image'],
            training_day_id=int(data['training_day_id']),
        )
    )
    await session.execute(query)
    await session.commit()


############################ Подходы ######################################

async def orm_add_set(session: AsyncSession, data: dict):
    obj = Set(
        exercise_id=int(data['exercise_id']),
        weight=data['weight'],
        repetitions=data['repetitions'],
    )
    session.add(obj)
    await session.commit()


async def orm_get_sets(session: AsyncSession, exercise_id: int):
    query = select(Set).where(Set.exercise_id == int(exercise_id))
    result = await session.execute(query)
    return result.scalars().all()


async def orm_get_set(session: AsyncSession, set_id: int):
    query = select(Set).where(Set.id == int(set_id))
    result = await session.execute(query)
    return result.scalar()


async def orm_update_set(session: AsyncSession, set_id: int, data: dict):
    query = (
        update(Set)
        .where(Set.id == int(set_id))
        .values(
            name=data['name'],
            description=data['description'],
            image=data['image'],
        )
    )
    await session.execute(query)
    await session.commit()


##################### Добавляем юзера в БД #####################################


async def orm_add_user(
        session: AsyncSession,
        data: dict
):
    user = User(
        user_id=int(data['user_id']),
        name=data['name'],
        weight=float(data['weight']),
    )
    session.add(user)
    await session.commit()


async def orm_update_user(session: AsyncSession, user_id: int, data: dict):
    query = (
        update(User)
        .where(User.user_id == user_id)
        .values(
            name=data['name'],
            weight=float(data['weight']),
        )
    )
    await session.execute(query)
    await session.commit()


async def orm_get_user_by_id(session: AsyncSession, user_id: int):
    result = await session.execute(
        select(User).where(User.user_id == user_id)
    )
    return result.scalars().first()
