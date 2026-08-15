"""Программы тренировок: список, создание, активация, настройки, удаление."""
from fastapi import APIRouter, HTTPException

from database.orm_extra import orm_update_program_settings
from database.orm_query import (
    orm_add_exercise,
    orm_add_program,
    orm_add_training_day,
    orm_delete_program,
    orm_get_admin_exercises,
    orm_get_exercises,
    orm_get_program,
    orm_get_programs,
    orm_get_training_days,
    orm_turn_on_off_program,
)
from miniapp.config import WEEK_DAYS_RU
from miniapp.db import Session
from miniapp.deps import CurrentUser
from miniapp.ownership import own_program
from miniapp.program_templates import BY_ID, TEMPLATES, template_json
from miniapp.routers.schedule import week
from miniapp.schemas import ProgramIn, ProgramPatchIn
from miniapp.serializers import program_json

router = APIRouter(prefix="/api/programs", tags=["programs"])


@router.get("")
async def list_programs(user: CurrentUser, session: Session):
    programs = await orm_get_programs(session, user.user_id)

    out = []
    for program in programs:
        days = await orm_get_training_days(session, program.id)
        # await нельзя вносить внутрь comprehension: с ним генератор становится
        # async-генератором, а sum() его не итерирует. Поэтому обычным циклом.
        filled = 0
        for d in days:
            if await orm_get_exercises(session, d.id):
                filled += 1
        out.append({**program_json(program, user.actual_program_id), "filled_days": filled})

    return {"ok": True, "programs": out}


@router.get("/templates")
async def list_templates():
    """
    Готовые программы для экрана создания.

    Объявлен ДО «/{program_id}/…»: у тех путей параметр целочисленный, поэтому
    «templates» им и так не подойдёт, но порядок объявления делает это очевидным
    и не даст сломаться при добавлении строкового параметра.
    """
    return {"ok": True, "templates": [template_json(t) for t in TEMPLATES]}


@router.post("")
async def create_program(body: ProgramIn, user: CurrentUser, session: Session):
    """
    Новая программа сразу с семью днями недели и сразу активная — как делал бот.
    Дни заводятся все семь: пустой день это просто день отдыха.

    С шаблоном дни ещё и наполняются упражнениями. Собирается это здесь, а не
    двадцатью запросами с клиента: программа обязана появиться либо целиком, либо
    никак — на середине оборвавшаяся пачка оставила бы «Верх / низ, 4 дня» с двумя
    заполненными днями и без объяснений.
    """
    await orm_add_program(session, {"name": body.name.strip(), "user_id": user.user_id})
    program = (await orm_get_programs(session, user.user_id))[-1]

    for day_name in WEEK_DAYS_RU:
        await orm_add_training_day(session, day_of_week=day_name, program_id=program.id)

    if body.template:
        template = BY_ID.get(body.template)
        if not template:
            raise HTTPException(404, "шаблон не найден")
        await _fill_from_template(session, program.id, template)

    await orm_turn_on_off_program(session, user_id=user.user_id, program_id=program.id)
    return {"ok": True, "program": program_json(program, program.id)}


async def _fill_from_template(session: Session, program_id: int, template) -> None:
    """
    Раскладывает упражнения шаблона по дням созданной программы.

    Ищем по НАЗВАНИЮ: id пресетов зависят от порядка вставки в конкретной базе
    (каталог насыпает seed.py при старте), и зашитые числа разошлись бы между
    прод- и тест-контуром. Чего в каталоге не нашлось — пропускаем молча: пустая
    строка в программе полезнее, чем пятисотка на создании.
    """
    days = {d.day_of_week.strip().lower(): d for d in await orm_get_training_days(session, program_id)}
    catalog = {e.name: e for e in await orm_get_admin_exercises(session)}

    for day_name, names in template.days.items():
        day = days.get(day_name.strip().lower())
        if not day:
            continue

        for name in names:
            preset = catalog.get(name)
            if not preset:
                continue

            await orm_add_exercise(
                session,
                {
                    "name": preset.name,
                    "description": preset.description,
                    "circle_training": False,
                    "admin_exercise_id": preset.id,
                    "equipment": preset.equipment,
                },
                day.id,
                "admin",
            )


@router.patch("/{program_id}")
async def update_program(program_id: int, body: ProgramPatchIn, user: CurrentUser, session: Session):
    """
    Название и настройки отдыха/кругов.

    В боте эти поля лежали в БД, но интерфейса к ним не существовало: их нельзя было
    ни увидеть, ни изменить — все тренировались с дефолтными пятью минутами.
    """
    await own_program(session, user.user_id, program_id)

    changes = {k: v for k, v in body.model_dump().items() if v is not None}
    if changes:
        await orm_update_program_settings(session, program_id, changes)

    program = await orm_get_program(session, program_id)
    return {"ok": True, "program": program_json(program, user.actual_program_id)}


@router.post("/{program_id}/activate")
async def activate_program(program_id: int, user: CurrentUser, session: Session):
    await own_program(session, user.user_id, program_id)
    await orm_turn_on_off_program(session, user_id=user.user_id, program_id=program_id)
    return {"ok": True}


@router.post("/{program_id}/deactivate")
async def deactivate_program(program_id: int, user: CurrentUser, session: Session):
    await own_program(session, user.user_id, program_id)
    await orm_turn_on_off_program(session, user_id=user.user_id, program_id=None)
    return {"ok": True}


@router.delete("/{program_id}")
async def delete_program(program_id: int, user: CurrentUser, session: Session):
    await own_program(session, user.user_id, program_id)

    # Снимаем активность до удаления: User.actual_program_id — обычный Integer,
    # не внешний ключ, так что ссылку на удалённую программу никто не почистит.
    if user.actual_program_id == program_id:
        await orm_turn_on_off_program(session, user_id=user.user_id, program_id=None)

    await orm_delete_program(session, program_id)
    return {"ok": True}


@router.get("/{program_id}/days")
async def program_days(program_id: int, user: CurrentUser, session: Session):
    program = await own_program(session, user.user_id, program_id)
    return {
        "ok": True,
        "program": program_json(program, user.actual_program_id),
        "days": await week(session, program.id),
    }
