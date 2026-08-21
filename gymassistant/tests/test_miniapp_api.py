"""
Тесты API Mini App — сквозь весь слой данных, на живой SQLite.

Моков нет: гоняем настоящие orm_-функции бота по настоящей базе. Это единственный
способ поймать баги вроде UUID-подхода, которые на Postgres молчат, а на SQLite падают.

Запуск (из каталога gymassistant/):
    ./.venv-bot/bin/pytest tests/ -q
"""
import hashlib
import hmac
import json
import os
import sys
import tempfile
import urllib.parse
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

# База — временный файл, свой на каждый прогон: тесты не должны видеть чужие данные.
_TMP_DB = Path(tempfile.mkdtemp()) / "test.db"
os.environ["DB_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ["MINIAPP_BOT_TOKEN"] = "123456:TEST-TOKEN"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from database.engine import create_db, session_maker  # noqa: E402
from miniapp.main import app  # noqa: E402
from miniapp.seed import seed_catalog  # noqa: E402

TOKEN = os.environ["MINIAPP_BOT_TOKEN"]
USER_ID = 777_000_111


def sign(user_id: int = USER_ID, name: str = "Тестер") -> str:
    """Подписанный initData — ровно так его формирует Telegram."""
    fields = {
        "auth_date": "2000000000",
        "query_id": "AAF",
        # Реальный Telegram шлёт и signature (Ed25519-подпись для сторонней валидации).
        # Она ВХОДИТ в data_check_string HMAC — проверяем, что сервер её не выбрасывает.
        "signature": "ZmFrZV9zaWduYXR1cmU",
        "user": json.dumps({"id": user_id, "first_name": name}, ensure_ascii=False),
    }
    check_string = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(fields)


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client():
    """
    Чистая база на каждый тест.

    Иначе брошенные тренировки и программы одного теста утекают в следующий —
    пользователь-то один и тот же.
    """
    from database.engine import engine
    from database.models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await create_db()
    async with session_maker() as session:
        await seed_catalog(session)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-Init-Data": sign()},
    ) as http:
        yield http


# ---------------------------------------------------------------- подпись

@pytest.mark.anyio
async def test_forged_init_data_rejected():
    """
    Главная проверка всего проекта: подменённый user.id должен получать 401.

    Берём валидный initData и меняем в нём id на чужой, не трогая hash, — так и
    выглядела бы попытка писать подходы в чужой аккаунт.
    """
    valid = sign(user_id=111)
    forged = valid.replace(urllib.parse.quote("111"), urllib.parse.quote("222"))
    assert forged != valid

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        assert (await http.get("/api/bootstrap", headers={"X-Init-Data": forged})).status_code == 401
        assert (await http.get("/api/bootstrap", headers={"X-Init-Data": ""})).status_code == 401
        assert (await http.get("/api/bootstrap")).status_code == 401
        # Подпись валидна, но выписана другим ботом — тоже мимо.
        assert (await http.get(
            "/api/bootstrap",
            headers={"X-Init-Data": sign() + "x"},
        )).status_code == 401


# ---------------------------------------------------------------- сквозной сценарий

@pytest.mark.anyio
async def test_full_training_flow(client: httpx.AsyncClient):
    """Регистрация → программа → день → упражнения → тренировка → история."""
    boot = (await client.get("/api/bootstrap")).json()
    assert boot["ok"] and boot["user"]["name"] == "Тестер"
    assert boot["has_program"] is False  # новый пользователь, программ нет

    # --- программа заводится сразу с семью днями и сразу активной
    program = (await client.post("/api/programs", json={"name": "Тест"})).json()["program"]
    assert program["active"] is True

    days = (await client.get(f"/api/programs/{program['id']}/days")).json()["days"]
    assert [d["day_of_week"] for d in days][:2] == ["Понедельник", "Вторник"]

    # --- набиваем понедельник: два обычных упражнения и два круговых
    monday = days[0]["id"]
    catalog = (await client.get("/api/catalog")).json()["categories"]
    chest = next(c for c in catalog if c["name"] == "Грудь")
    picks = (await client.get(f"/api/catalog/{chest['id']}")).json()["exercises"][:4]

    for i, item in enumerate(picks):
        resp = await client.post(
            f"/api/days/{monday}/exercises",
            json={"admin_exercise_id": item["id"], "circle_training": i >= 2},
        )
        assert resp.status_code == 200

    exercises = resp.json()["exercises"]
    assert len(exercises) == 4
    assert [e["position"] for e in exercises] == [0, 1, 2, 3]

    # --- по два подхода в обычных, три круга в круговых
    for e in exercises[:2]:
        await client.patch(f"/api/exercises/{e['id']}", json={"sets": 2, "reps": 8})

    # --- план: 2+2 обычных подхода, затем 3 круга × 2 упражнения
    state = (await client.post("/api/training/start", json={"training_day_id": monday})).json()
    assert state["progress"]["total"] == 2 + 2 + 3 * 2
    assert state["current"]["exercise"]["id"] == exercises[0]["id"]
    assert state["current"]["set_number"] == 1
    assert state["current"]["is_circuit"] is False

    session_id = state["session_id"]

    # --- пишем первый подход: должен появиться таймер отдыха и сдвинуться шаг
    state = (await client.post("/api/training/set", json={
        "session_id": session_id,
        "exercise_id": exercises[0]["id"],
        "weight": 60.0,
        "reps": 8,
    })).json()

    assert state["progress"]["done"] == 1
    assert state["current"]["set_number"] == 2                  # тот же снаряд, второй подход
    assert state["current"]["exercise"]["id"] == exercises[0]["id"]
    assert state["rest"]["left"] > 0                            # таймер поставлен сервером
    assert state["rest"]["total"] == 300                        # rest_between_set по умолчанию

    # --- добиваем всё, что осталось по плану
    while not state["finished"]:
        current = state["current"]
        state = (await client.post("/api/training/set", json={
            "session_id": session_id,
            "exercise_id": current["exercise"]["id"],
            "weight": 50.0,
            "reps": 10,
        })).json()

    assert state["finished"] is True
    assert state["progress"]["done"] == state["progress"]["total"] == 10
    assert state["rest"] is None                                 # после последнего подхода отдыха нет

    # --- круговые шли кругами, а не подряд: 3 круга по 2 упражнения вперемешку
    circuit = [p for p in state["plan"] if p["is_circuit"]]
    assert [p["round_number"] for p in circuit] == [1, 1, 2, 2, 3, 3]
    assert circuit[0]["exercise_id"] != circuit[1]["exercise_id"]
    assert circuit[0]["exercise_id"] == circuit[2]["exercise_id"]

    finish = (await client.post("/api/training/finish", json={"session_id": session_id})).json()
    assert finish["sets"] == 10 and finish["exercises"] == 4

    # --- тренировка в истории, рекорд записан
    history = (await client.get("/api/history")).json()["sessions"]
    assert len(history) == 1 and history[0]["sets"] == 10

    detail = (await client.get(f"/api/history/{session_id}")).json()
    assert len(detail["exercises"]) == 4

    records = (await client.get("/api/stats")).json()["records"]
    assert records[0]["max_weight"] == 60.0


@pytest.mark.anyio
async def test_programs_list_counts_filled_days(client: httpx.AsyncClient):
    """
    GET /api/programs падал на боевых данных: filled_days считался через
    sum(... await ...) внутри comprehension — await делает его async-генератором,
    и sum() его не итерирует. Баг проявляется только когда программа есть.
    """
    program = (await client.post("/api/programs", json={"name": "Список"})).json()["program"]
    day = (await client.get(f"/api/programs/{program['id']}/days")).json()["days"][0]["id"]
    chest = next(
        c for c in (await client.get("/api/catalog")).json()["categories"] if c["name"] == "Грудь"
    )
    item = (await client.get(f"/api/catalog/{chest['id']}")).json()["exercises"][0]
    await client.post(f"/api/days/{day}/exercises", json={"admin_exercise_id": item["id"]})

    resp = await client.get("/api/programs")
    assert resp.status_code == 200
    listed = next(p for p in resp.json()["programs"] if p["id"] == program["id"])
    assert listed["filled_days"] == 1


@pytest.mark.anyio
async def test_history_survives_program_change(client: httpx.AsyncClient):
    """
    Баг 5: рекорды и «прошлый раз» жили на Exercise.id, а он свой в каждой программе.
    Сменил программу — история обнулилась. Теперь агрегация идёт по каталогу,
    поэтому то же упражнение в новой программе помнит и рекорд, и прошлый раз.
    """
    chest = next(
        c for c in (await client.get("/api/catalog")).json()["categories"] if c["name"] == "Грудь"
    )
    bench = (await client.get(f"/api/catalog/{chest['id']}")).json()["exercises"][0]

    async def program_with_bench(name: str) -> tuple[int, int]:
        program = (await client.post("/api/programs", json={"name": name})).json()["program"]
        days = (await client.get(f"/api/programs/{program['id']}/days")).json()["days"]
        day_id = days[0]["id"]
        result = await client.post(
            f"/api/days/{day_id}/exercises", json={"admin_exercise_id": bench["id"]},
        )
        await client.patch(f"/api/exercises/{result.json()['exercises'][0]['id']}", json={"sets": 1})
        return day_id, result.json()["exercises"][0]["id"]

    # Первая программа: жмём 100 кг.
    old_day, old_exercise = await program_with_bench("Старая")
    state = (await client.post("/api/training/start", json={"training_day_id": old_day})).json()
    await client.post("/api/training/set", json={
        "session_id": state["session_id"], "exercise_id": old_exercise, "weight": 100.0, "reps": 5,
    })
    await client.post("/api/training/finish", json={"session_id": state["session_id"]})

    # Вторая программа: то же упражнение, но другая строка Exercise с другим id.
    new_day, new_exercise = await program_with_bench("Новая")
    assert new_exercise != old_exercise

    state = (await client.post("/api/training/start", json={"training_day_id": new_day})).json()
    card = state["current"]["exercise"]

    assert card["record"] == 100.0                              # рекорд не потерялся
    assert card["prev"] == [{"weight": 100.0, "reps": 5}]       # и «прошлый раз» тоже


@pytest.mark.anyio
async def test_set_can_be_fixed_and_removed(client: httpx.AsyncClient):
    """Записанный подход правится и удаляется, шаг тренировки пересчитывается."""
    program = (await client.post("/api/programs", json={"name": "Правки"})).json()["program"]
    day = (await client.get(f"/api/programs/{program['id']}/days")).json()["days"][0]["id"]

    chest = next(
        c for c in (await client.get("/api/catalog")).json()["categories"] if c["name"] == "Грудь"
    )
    item = (await client.get(f"/api/catalog/{chest['id']}")).json()["exercises"][0]
    added = (await client.post(
        f"/api/days/{day}/exercises", json={"admin_exercise_id": item["id"]},
    )).json()["exercises"][0]

    state = (await client.post("/api/training/start", json={"training_day_id": day})).json()
    state = (await client.post("/api/training/set", json={
        "session_id": state["session_id"], "exercise_id": added["id"], "weight": 80.0, "reps": 10,
    })).json()

    set_id = state["sets"][0]["id"]
    assert state["progress"]["done"] == 1

    # Промахнулись по степперу — правим.
    state = (await client.patch(f"/api/training/set/{set_id}", json={
        "weight": 82.5, "reps": 9,
    })).json()
    assert state["sets"][0]["weight"] == 82.5
    assert state["current"]["exercise"]["record"] == 82.5

    # Записали лишний подход — удаляем, шаг откатывается назад.
    state = (await client.delete(f"/api/training/set/{set_id}")).json()
    assert state["progress"]["done"] == 0
    assert state["current"]["set_number"] == 1


@pytest.mark.anyio
async def test_cannot_touch_other_users_data(client: httpx.AsyncClient):
    """Чужие объекты не читаются и не правятся — id в URL сам по себе ничего не даёт."""
    program = (await client.post("/api/programs", json={"name": "Моя"})).json()["program"]
    day = (await client.get(f"/api/programs/{program['id']}/days")).json()["days"][0]["id"]

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test",
        headers={"X-Init-Data": sign(user_id=999_000_222, name="Чужой")},
    ) as stranger:
        assert (await stranger.get(f"/api/programs/{program['id']}/days")).status_code == 404
        assert (await stranger.get(f"/api/day/{day}")).status_code == 404
        assert (await stranger.delete(f"/api/programs/{program['id']}")).status_code == 404
        assert (await stranger.post(
            "/api/training/start", json={"training_day_id": day},
        )).status_code == 404


@pytest.mark.anyio
async def test_program_settings_are_editable(client: httpx.AsyncClient):
    """Баг 7: настройки отдыха лежали в БД, но UI к ним не было. Теперь есть API."""
    program = (await client.post("/api/programs", json={"name": "Настройки"})).json()["program"]
    assert program["settings"]["rest_between_set"] == 300
    assert program["settings"]["circular_rounds"] == 3

    updated = (await client.patch(f"/api/programs/{program['id']}", json={
        "rest_between_set": 90,
        "rest_between_exercise": 120,
        "circular_rounds": 4,
    })).json()["program"]

    assert updated["settings"]["rest_between_set"] == 90
    assert updated["settings"]["rest_between_exercise"] == 120
    assert updated["settings"]["circular_rounds"] == 4


@pytest.mark.anyio
async def test_rest_between_exercise_is_actually_used(client: httpx.AsyncClient):
    """
    Баг 8: rest_between_exercise клали в FSM и никогда не читали — между упражнениями
    обычного блока отдыха не было вовсе. Проверяем, что теперь он ставится.
    """
    program = (await client.post("/api/programs", json={"name": "Отдых"})).json()["program"]
    await client.patch(f"/api/programs/{program['id']}", json={
        "rest_between_set": 60, "rest_between_exercise": 180,
    })

    day = (await client.get(f"/api/programs/{program['id']}/days")).json()["days"][0]["id"]
    chest = next(
        c for c in (await client.get("/api/catalog")).json()["categories"] if c["name"] == "Грудь"
    )
    picks = (await client.get(f"/api/catalog/{chest['id']}")).json()["exercises"][:2]

    for item in picks:
        added = (await client.post(
            f"/api/days/{day}/exercises", json={"admin_exercise_id": item["id"]},
        )).json()["exercises"]
    for e in added:
        await client.patch(f"/api/exercises/{e['id']}", json={"sets": 2})

    state = (await client.post("/api/training/start", json={"training_day_id": day})).json()
    session_id = state["session_id"]

    # Первый подход первого упражнения → впереди второй подход того же → отдых между подходами.
    state = (await client.post("/api/training/set", json={
        "session_id": session_id, "exercise_id": added[0]["id"], "weight": 50.0, "reps": 10,
    })).json()
    assert state["rest"]["total"] == 60

    # Второй подход первого упражнения → впереди уже другое упражнение → отдых между упражнениями.
    state = (await client.post("/api/training/set", json={
        "session_id": session_id, "exercise_id": added[0]["id"], "weight": 50.0, "reps": 10,
    })).json()
    assert state["rest"]["total"] == 180
    assert added[1]["name"] in state["rest"]["next_up"]


@pytest.mark.anyio
async def test_training_survives_reopen(client: httpx.AsyncClient):
    """
    Баг 1: состояние тренировки жило в FSM в памяти, и рестарт пода её обрывал.
    Теперь шаг вычисляется из БД — закрыли Mini App, открыли заново, шаг тот же.
    """
    program = (await client.post("/api/programs", json={"name": "Живучесть"})).json()["program"]
    day = (await client.get(f"/api/programs/{program['id']}/days")).json()["days"][0]["id"]
    chest = next(
        c for c in (await client.get("/api/catalog")).json()["categories"] if c["name"] == "Грудь"
    )
    item = (await client.get(f"/api/catalog/{chest['id']}")).json()["exercises"][0]
    added = (await client.post(
        f"/api/days/{day}/exercises", json={"admin_exercise_id": item["id"]},
    )).json()["exercises"][0]

    state = (await client.post("/api/training/start", json={"training_day_id": day})).json()
    await client.post("/api/training/set", json={
        "session_id": state["session_id"], "exercise_id": added["id"], "weight": 70.0, "reps": 10,
    })

    # «Закрыли приложение» — новый запрос, никакого состояния в памяти.
    restored = (await client.get("/api/training/state")).json()
    assert restored["session_id"] == state["session_id"]
    assert restored["progress"]["done"] == 1
    assert restored["current"]["set_number"] == 2

    # Повторный старт того же дня не плодит вторую тренировку.
    again = (await client.post("/api/training/start", json={"training_day_id": day})).json()
    assert again["session_id"] == state["session_id"]


@pytest.mark.anyio
async def test_bootstrap_today_uses_client_timezone(client: httpx.AsyncClient):
    """
    «Сегодня» считается в поясе ПОЛЬЗОВАТЕЛЯ (заголовок X-Timezone), а не сервера.

    Раньше день брался из date.today() по локали процесса — а контейнер в UTC, и
    юзер мог быть в любом поясе. Теперь пояс присылает телефон, и endpoint обязан
    его уважать; мусорный/пустой пояс безопасно откатывается на дефолт.
    """
    from zoneinfo import ZoneInfo

    from miniapp.config import WEEK_DAYS_RU
    from services.clock import DEFAULT_TZ, today_in

    for tz in ("Pacific/Kiritimati", "Etc/GMT+12", "Europe/Moscow"):
        boot = (await client.get("/api/bootstrap", headers={"X-Timezone": tz})).json()
        assert boot["today_name"] == WEEK_DAYS_RU[today_in(ZoneInfo(tz)).weekday()]

    # Невалидный пояс не роняет запрос и даёт дефолт (НСК), а не 500.
    boot = (await client.get("/api/bootstrap", headers={"X-Timezone": "'; DROP TABLE"})).json()
    assert boot["today_name"] == WEEK_DAYS_RU[today_in(DEFAULT_TZ).weekday()]


# ---------------------------------------------------------------- пропуски


async def _day_with(client: httpx.AsyncClient, name: str, count: int = 2, sets: int = 3):
    """Программа с одним заполненным днём: общая заготовка для тестов про пропуски."""
    program = (await client.post("/api/programs", json={"name": name})).json()["program"]
    day = (await client.get(f"/api/programs/{program['id']}/days")).json()["days"][0]["id"]
    chest = next(
        c for c in (await client.get("/api/catalog")).json()["categories"] if c["name"] == "Грудь"
    )
    picks = (await client.get(f"/api/catalog/{chest['id']}")).json()["exercises"][:count]

    for item in picks:
        added = (await client.post(
            f"/api/days/{day}/exercises", json={"admin_exercise_id": item["id"]},
        )).json()["exercises"]

    for e in added:
        await client.patch(f"/api/exercises/{e['id']}", json={"sets": sets})

    return day, added


@pytest.mark.anyio
async def test_skipped_set_advances_the_plan(client: httpx.AsyncClient):
    """
    Не смог подход — план всё равно едет дальше.

    Раньше выхода не было: шаг тренировки это первое расхождение плана и записанных
    подходов, поэтому сдвинуть его мог только записанный подход. Человек либо писал
    вес, которого не поднимал, либо бросал тренировку целиком.
    """
    day, added = await _day_with(client, "Пропуски")

    state = (await client.post("/api/training/start", json={"training_day_id": day})).json()
    session_id = state["session_id"]
    assert state["current"]["set_number"] == 1

    state = (await client.post("/api/training/skip", json={
        "session_id": session_id, "exercise_id": added[0]["id"],
    })).json()

    assert state["current"]["set_number"] == 2                 # шаг сдвинулся
    assert state["current"]["exercise"]["id"] == added[0]["id"]
    assert state["progress"]["done"] == 1                      # пропуск занял место в плане
    assert state["sets"][0]["skipped"] is True


@pytest.mark.anyio
async def test_skipped_sets_stay_out_of_statistics(client: httpx.AsyncClient):
    """
    Пропущенное двигает план, но не идёт ни в объём, ни в рекорды, ни в «прошлый раз».

    Иначе рекорд по упражнению становился бы нулевым, а история показывала бы
    подходы, которых не было.
    """
    day, added = await _day_with(client, "Статистика", count=1, sets=3)
    exercise_id = added[0]["id"]

    state = (await client.post("/api/training/start", json={"training_day_id": day})).json()
    session_id = state["session_id"]

    await client.post("/api/training/set", json={
        "session_id": session_id, "exercise_id": exercise_id, "weight": 80.0, "reps": 5,
    })
    await client.post("/api/training/skip", json={
        "session_id": session_id, "exercise_id": exercise_id, "whole_exercise": True,
    })

    state = (await client.get("/api/training/state")).json()
    assert state["finished"] is True                            # план отработан целиком
    assert state["progress"]["done"] == 3
    assert [s["skipped"] for s in state["sets"]] == [False, True, True]

    # Рекорд — только по поднятому.
    assert state["current"] is None
    finish = (await client.post("/api/training/finish", json={"session_id": session_id})).json()
    assert finish["sets"] == 1                                  # один настоящий подход из трёх
    assert finish["volume"] == 80.0 * 5

    profile = (await client.get("/api/profile")).json()
    assert profile["total"]["sets"] == 1
    assert profile["total"]["volume"] == 80.0 * 5

    records = (await client.get("/api/stats")).json()
    top = next(r for r in records["records"] if r["exercise_id"] == exercise_id)
    assert top["max_weight"] == 80.0                            # а не 0 от пропущенных


@pytest.mark.anyio
async def test_editing_a_skipped_set_makes_it_count(client: httpx.AsyncClient):
    """Исправил пропуск на настоящий результат — он обязан попасть в статистику."""
    day, added = await _day_with(client, "Передумал", count=1, sets=2)

    state = (await client.post("/api/training/start", json={"training_day_id": day})).json()
    state = (await client.post("/api/training/skip", json={
        "session_id": state["session_id"], "exercise_id": added[0]["id"],
    })).json()

    skipped_id = state["sets"][0]["id"]
    state = (await client.patch(f"/api/training/set/{skipped_id}",
                                json={"weight": 60.0, "reps": 8})).json()

    assert state["sets"][0]["skipped"] is False
    assert (await client.get("/api/profile")).json()["total"]["sets"] == 1


@pytest.mark.anyio
async def test_skip_only_applies_to_the_current_step(client: httpx.AsyncClient):
    """
    Пропустить можно только текущее упражнение.

    Подходы обязаны ложиться в порядке плана: на этом стоит и вычисление шага, и
    определение только что закрытого шага при постановке отдыха.
    """
    day, added = await _day_with(client, "Порядок", count=2)

    state = (await client.post("/api/training/start", json={"training_day_id": day})).json()
    resp = await client.post("/api/training/skip", json={
        "session_id": state["session_id"], "exercise_id": added[1]["id"],   # не текущее
    })
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_rest_after_a_skip_matches_the_next_step(client: httpx.AsyncClient):
    """
    После пропуска ставится тот же отдых, что и после выполненного подхода.

    Здесь легко было сломать тихо. Постановка отдыха определяет «только что закрытый
    шаг» как plan[len(done) - 1], то есть по КОЛИЧЕСТВУ записей в плановом порядке.
    Пропуск — такая же запись, поэтому инвариант держится; но стоит начать писать
    пропуски мимо текущего шага, и длительность отдыха поедет молча.
    """
    program = (await client.post("/api/programs", json={"name": "Отдых после пропуска"})).json()["program"]
    await client.patch(f"/api/programs/{program['id']}", json={
        "rest_between_set": 60, "rest_between_exercise": 180,
    })

    day = (await client.get(f"/api/programs/{program['id']}/days")).json()["days"][0]["id"]
    chest = next(
        c for c in (await client.get("/api/catalog")).json()["categories"] if c["name"] == "Грудь"
    )
    for item in (await client.get(f"/api/catalog/{chest['id']}")).json()["exercises"][:2]:
        added = (await client.post(
            f"/api/days/{day}/exercises", json={"admin_exercise_id": item["id"]},
        )).json()["exercises"]
    for e in added:
        await client.patch(f"/api/exercises/{e['id']}", json={"sets": 2})

    state = (await client.post("/api/training/start", json={"training_day_id": day})).json()
    session_id = state["session_id"]

    # Пропустили первый подход → впереди второй подход ТОГО ЖЕ снаряда.
    state = (await client.post("/api/training/skip", json={
        "session_id": session_id, "exercise_id": added[0]["id"],
    })).json()
    assert state["rest"]["total"] == 60

    # Пропустили второй → впереди уже другое упражнение.
    state = (await client.post("/api/training/skip", json={
        "session_id": session_id, "exercise_id": added[0]["id"],
    })).json()
    assert state["rest"]["total"] == 180
    assert added[1]["name"] in state["rest"]["next_up"]


@pytest.mark.anyio
async def test_skipping_a_whole_circuit_exercise_keeps_the_rounds_intact(client: httpx.AsyncClient):
    """
    «Закончить упражнение» в круговом блоке снимает только его круги.

    Круговой блок разворачивается вперемешку — по одному подходу каждого упражнения
    на круг. Списать «все оставшиеся подходы» здесь означает выбросить свои круги
    и не тронуть чужие; остальные упражнения блока должны доработать до конца.
    """
    program = (await client.post("/api/programs", json={"name": "Круг"})).json()["program"]
    await client.patch(f"/api/programs/{program['id']}", json={"circular_rounds": 3})

    day = (await client.get(f"/api/programs/{program['id']}/days")).json()["days"][0]["id"]
    chest = next(
        c for c in (await client.get("/api/catalog")).json()["categories"] if c["name"] == "Грудь"
    )
    for item in (await client.get(f"/api/catalog/{chest['id']}")).json()["exercises"][:2]:
        added = (await client.post(
            f"/api/days/{day}/exercises",
            json={"admin_exercise_id": item["id"], "circle_training": True},
        )).json()["exercises"]

    state = (await client.post("/api/training/start", json={"training_day_id": day})).json()
    assert state["progress"]["total"] == 6                     # 3 круга × 2 упражнения

    state = (await client.post("/api/training/skip", json={
        "session_id": state["session_id"],
        "exercise_id": added[0]["id"],
        "whole_exercise": True,
    })).json()

    # Списаны три круга первого упражнения, второе осталось при своих трёх.
    assert state["progress"]["done"] == 3
    assert state["current"]["exercise"]["id"] == added[1]["id"]
    assert state["finished"] is False


# ---------------------------------------------------------------- пропущенные дни

TZ_NAME = "Asia/Novosibirsk"
TZ_HEADERS = {"X-Timezone": TZ_NAME}


def _weekday_ru(days_back: int) -> str:
    """Название дня недели, каким он был `days_back` суток назад в поясе клиента."""
    from zoneinfo import ZoneInfo

    from miniapp.config import WEEK_DAYS_RU
    from services.clock import today_in

    return WEEK_DAYS_RU[(today_in(ZoneInfo(TZ_NAME)) - timedelta(days=days_back)).weekday()]


async def _program_with_days(client: httpx.AsyncClient, name: str, days_back: list[int]):
    """
    Программа, в которой заполнены дни недели, отстоящие от сегодня на `days_back`.

    Считаем от сегодняшнего дня, а не от «понедельника»: тест обязан вести себя
    одинаково в любой день недели, когда его запустят.
    """
    program = (await client.post("/api/programs", json={"name": name})).json()["program"]
    days = (await client.get(f"/api/programs/{program['id']}/days")).json()["days"]
    by_name = {d["day_of_week"]: d for d in days}

    chest = next(
        c for c in (await client.get("/api/catalog")).json()["categories"] if c["name"] == "Грудь"
    )
    item = (await client.get(f"/api/catalog/{chest['id']}")).json()["exercises"][0]

    filled = {}
    for back in days_back:
        day = by_name[_weekday_ru(back)]
        await client.post(f"/api/days/{day['id']}/exercises", json={"admin_exercise_id": item["id"]})
        filled[back] = day

    return filled


async def _train(client: httpx.AsyncClient, day_id: int, record: bool = True):
    """Отработать день. record=False — открыть тренировку и не записать ни подхода."""
    started = (await client.post("/api/training/start", json={"training_day_id": day_id})).json()
    if record:
        await client.post("/api/training/set", json={
            "session_id": started["session_id"],
            "exercise_id": started["current"]["exercise"]["id"],
            "weight": 40.0, "reps": 10,
        })
    return started["session_id"]


@pytest.mark.anyio
async def test_missed_day_shows_the_most_recent_one(client: httpx.AsyncClient):
    """
    Из нескольких пропущенных показывается САМЫЙ СВЕЖИЙ.

    Список здесь был бы упрёком, а не помощью: задача экрана — предложить ближайшее
    к отработке, а не подвести итог недели.
    """
    filled = await _program_with_days(client, "Свежесть", [1, 3, 5])

    boot = (await client.get("/api/bootstrap", headers=TZ_HEADERS)).json()
    assert boot["missed"]["id"] == filled[1]["id"]
    assert boot["missed"]["days_ago"] == 1

    # Закрыли вчерашний — подтянулся следующий по свежести, а не «все сразу».
    await _train(client, filled[1]["id"])
    boot = (await client.get("/api/bootstrap", headers=TZ_HEADERS)).json()
    assert boot["missed"]["id"] == filled[3]["id"]
    assert boot["missed"]["days_ago"] == 3


@pytest.mark.anyio
async def test_working_off_a_missed_day_on_another_date_closes_it(client: httpx.AsyncClient):
    """
    Пропустил в понедельник, сделал во вторник — в среду не предлагается.

    Ключ в том, что день закрывается по `training_day_id` сессии, а не по календарной
    дате: отработать понедельник во вторник — это перенос, а не ещё один пропуск.
    Сравнение по датам показывало бы понедельник вечно, пока не наступит следующий.
    """
    filled = await _program_with_days(client, "Перенос", [2])          # позавчера
    target = filled[2]

    boot = (await client.get("/api/bootstrap", headers=TZ_HEADERS)).json()
    assert boot["missed"]["id"] == target["id"]
    assert boot["missed"]["days_ago"] == 2

    # Отрабатываем СЕГОДНЯ — датой сессии будет сегодняшнее число, а не позавчерашнее.
    await _train(client, target["id"])

    boot = (await client.get("/api/bootstrap", headers=TZ_HEADERS)).json()
    assert boot["missed"] is None


@pytest.mark.anyio
async def test_a_training_earlier_than_the_day_does_not_close_it(client: httpx.AsyncClient):
    """
    Тренировка, случившаяся РАНЬШЕ самого дня, его не закрывает.

    День недели повторяется каждые семь суток, и одна тренировка закрывает ровно один
    его повтор — тот, что уже наступил к её моменту. Раньше здесь стояло «была ли
    вообще сессия по этому дню за последнюю неделю», и отработанное с опозданием
    ПРОШЛОЕ воскресенье гасило напоминание про воскресенье наступившее. Наружу лезла
    суббота: день ещё дальше в прошлом, чем ближайший пропущенный, — ровно то, чего
    экран обещает не делать.
    """
    filled = await _program_with_days(client, "Свой повтор", [1, 2])
    yesterday, before = filled[1], filled[2]

    # Тренировка по вчерашнему ДНЮ НЕДЕЛИ, но датированная тремя сутками назад:
    # закрыт ею прошлый повтор этого дня, а не вчерашний.
    await _backdate(await _train(client, yesterday["id"]), days=3)

    boot = (await client.get("/api/bootstrap", headers=TZ_HEADERS)).json()
    assert boot["missed"]["id"] == yesterday["id"]
    assert boot["missed"]["days_ago"] == 1

    # А сегодняшняя — уже ПОСЛЕ дня — закрывает его: это перенос, и дальше по свежести
    # подтягивается позавчерашний.
    await _train(client, yesterday["id"])

    boot = (await client.get("/api/bootstrap", headers=TZ_HEADERS)).json()
    assert boot["missed"]["id"] == before["id"]
    assert boot["missed"]["days_ago"] == 2


@pytest.mark.anyio
async def test_todays_own_workout_is_never_offered_as_missed(client: httpx.AsyncClient):
    """
    Сегодняшняя тренировка не показывается как пропущенная неделю назад.

    Поиск идёт на шесть дней назад, а не на семь, ровно поэтому: седьмой день — это
    тот же день недели, что сегодня. Иначе главный экран показывал бы один день
    дважды («сегодня: Понедельник» и «пропущено: Понедельник, 7 дней назад»), причём
    обе кнопки открывали бы одну и ту же тренировку.
    """
    filled = await _program_with_days(client, "Сегодня", [0])          # только сегодняшний день

    boot = (await client.get("/api/bootstrap", headers=TZ_HEADERS)).json()
    assert boot["today"] is not None
    assert boot["today"]["id"] == filled[0]["id"]
    assert boot["missed"] is None


@pytest.mark.anyio
async def test_training_older_than_a_week_no_longer_closes_the_day(client: httpx.AsyncClient):
    """
    Свежесть — неделя: тренировка недельной давности день уже не закрывает.

    Без окна одна старая тренировка гасила бы напоминание о своём дне недели навсегда.
    """
    from sqlalchemy import update

    from database.models import TrainingSession

    filled = await _program_with_days(client, "Давность", [1])
    target = filled[1]

    session_id = await _train(client, target["id"])
    assert (await client.get("/api/bootstrap", headers=TZ_HEADERS)).json()["missed"] is None

    # Отодвигаем ту же тренировку на восемь суток назад.
    import uuid

    from services.clock import utcnow

    async with session_maker() as db:
        await db.execute(
            update(TrainingSession)
            .where(TrainingSession.id == uuid.UUID(session_id))
            .values(date=utcnow() - timedelta(days=8))
        )
        await db.commit()

    boot = (await client.get("/api/bootstrap", headers=TZ_HEADERS)).json()
    assert boot["missed"]["id"] == target["id"]


@pytest.mark.anyio
async def test_opening_a_workout_without_recording_does_not_close_the_day(client: httpx.AsyncClient):
    """
    Открыть тренировку и ничего не записать — не значит отработать день.

    Иначе случайное нажатие «Начать» гасило бы напоминание, а сама пустая сессия
    всё равно будет убрана уборщиком (`orm_delete_empty_sessions`).
    """
    filled = await _program_with_days(client, "Передумал", [1])
    target = filled[1]

    await _train(client, target["id"], record=False)

    boot = (await client.get("/api/bootstrap", headers=TZ_HEADERS)).json()
    assert boot["missed"]["id"] == target["id"]


@pytest.mark.anyio
async def test_rest_days_are_never_missed(client: httpx.AsyncClient):
    """День без упражнений — это день отдыха, пропустить его нельзя."""
    await _program_with_days(client, "Отдых", [])          # ни одного заполненного дня

    boot = (await client.get("/api/bootstrap", headers=TZ_HEADERS)).json()
    assert boot["missed"] is None


# ---------------------------------------------------------------- идущая тренировка


@pytest.mark.anyio
async def test_active_training_is_reported_even_on_a_rest_day(client: httpx.AsyncClient):
    """
    Тренировка, идущая в день отдыха, обязана быть видна на главной.

    Это ровно тот случай, ради которого в bootstrap появился объект `active`:
    отработать можно пропущенный день, а сегодня при этом упражнений не
    запланировано. Главная в такой ситуации уходила в ветку «день отдыха» и о
    тренировке не говорила ни слова — вернуться в неё было нельзя вовсе, только
    закрыть и открыть приложение заново.
    """
    filled = await _program_with_days(client, "Отработка", [1])       # заполнен только вчерашний
    yesterday = filled[1]

    boot = (await client.get("/api/bootstrap", headers=TZ_HEADERS)).json()
    assert boot["today"]["exercises"] == []           # сегодня отдыхаем
    assert boot["active"] is None

    session_id = await _train(client, yesterday["id"])

    boot = (await client.get("/api/bootstrap", headers=TZ_HEADERS)).json()
    assert boot["today"]["exercises"] == []           # сегодня всё ещё день отдыха
    assert boot["active"]["session_id"] == session_id
    # День назван, потому что тренируется НЕ сегодняшний: на экране с сегодняшним
    # заголовком тренировка без подписи выглядела бы чужой.
    assert boot["active"]["day"]["id"] == yesterday["id"]
    assert boot["active"]["day"]["day_of_week"] == _weekday_ru(1)


@pytest.mark.anyio
async def test_active_training_carries_its_own_day_not_todays(client: httpx.AsyncClient):
    """
    Пока отрабатывается вчерашний день, главная показывает ЕГО упражнения.

    Сегодняшний день здесь тоже заполнен — и раньше именно он и оставался на экране:
    заголовок, счётчик подходов и список были сегодняшними, хотя тренировалась
    суббота. Список упражнений обязан приехать от того дня, который тренируют.
    """
    filled = await _program_with_days(client, "Не сегодня", [0, 1])
    today, yesterday = filled[0], filled[1]

    boot = (await client.get("/api/bootstrap", headers=TZ_HEADERS)).json()
    todays_ids = {e["id"] for e in boot["today"]["exercises"]}
    assert todays_ids                                    # сегодня тоже тренировочный день

    await _train(client, yesterday["id"], record=False)

    boot = (await client.get("/api/bootstrap", headers=TZ_HEADERS)).json()
    active_day = boot["active"]["day"]
    assert active_day["id"] == yesterday["id"] != today["id"]

    # Упражнения — вчерашнего дня; с сегодняшними они не пересекаются, потому что
    # Exercise это упражнение В КОНКРЕТНОМ дне, а не строка каталога.
    active_ids = {e["id"] for e in active_day["exercises"]}
    assert active_ids and not (active_ids & todays_ids)

    # Выделять на главной надо текущий шаг, а он ещё и не обязан быть первым.
    assert boot["active"]["next_exercise_id"] in active_ids


@pytest.mark.anyio
async def test_active_training_reports_progress_against_the_plan(client: httpx.AsyncClient):
    """Счёт подходов в `active` — тот же, что на экране тренировки: план против факта."""
    filled = await _program_with_days(client, "Прогресс", [0])        # тренируем сегодняшний день
    today = filled[0]

    started = (await client.post(
        "/api/training/start", json={"training_day_id": today["id"]},
    )).json()
    total = started["progress"]["total"]

    boot = (await client.get("/api/bootstrap", headers=TZ_HEADERS)).json()
    assert boot["active"]["done"] == 0
    assert boot["active"]["total"] == total

    await client.post("/api/training/set", json={
        "session_id": started["session_id"],
        "exercise_id": started["current"]["exercise"]["id"],
        "weight": 40.0, "reps": 10,
    })

    boot = (await client.get("/api/bootstrap", headers=TZ_HEADERS)).json()
    assert boot["active"]["done"] == 1
    assert boot["active"]["total"] == total
    # Шаг уехал вперёд вместе с записанным подходом — ровно как на экране тренировки.
    state = (await client.get("/api/training/state")).json()
    assert boot["active"]["next_exercise_id"] == state["current"]["exercise"]["id"]

    # Завершённая тренировка с главной уходит — кнопке «Продолжить» больше нечего продолжать.
    await client.post("/api/training/finish", json={"session_id": started["session_id"]})
    assert (await client.get("/api/bootstrap", headers=TZ_HEADERS)).json()["active"] is None


@pytest.mark.anyio
async def test_circuit_step_reports_position_inside_the_round(client: httpx.AsyncClient):
    """
    В круговом блоке видно не только номер круга, но и место внутри него.

    «Круг 1 из 3» отвечает лишь на половину вопроса: сколько раз пройти блок. Стоя
    между тремя станциями, хочется знать, сколько снарядов осталось до конца круга, —
    и раньше эти данные на экран не приезжали вовсе.
    """
    program = (await client.post("/api/programs", json={"name": "Позиция"})).json()["program"]
    await client.patch(f"/api/programs/{program['id']}", json={"circular_rounds": 3})

    day = (await client.get(f"/api/programs/{program['id']}/days")).json()["days"][0]["id"]
    chest = next(
        c for c in (await client.get("/api/catalog")).json()["categories"] if c["name"] == "Грудь"
    )
    for item in (await client.get(f"/api/catalog/{chest['id']}")).json()["exercises"][:3]:
        added = (await client.post(
            f"/api/days/{day}/exercises",
            json={"admin_exercise_id": item["id"], "circle_training": True},
        )).json()["exercises"]

    state = (await client.post("/api/training/start", json={"training_day_id": day})).json()
    session_id = state["session_id"]

    # Круг идёт по упражнениям: 1→2→3, и только потом начинается следующий круг.
    for expected_round, expected_position in [(1, 1), (1, 2), (1, 3), (2, 1)]:
        assert state["current"]["round_number"] == expected_round
        assert state["current"]["exercise_number"] == expected_position
        assert state["current"]["total_exercises"] == 3

        state = (await client.post("/api/training/set", json={
            "session_id": session_id,
            "exercise_id": state["current"]["exercise"]["id"],
            "weight": 30.0, "reps": 10,
        })).json()


@pytest.mark.anyio
async def test_plain_block_reports_position_among_its_exercises(client: httpx.AsyncClient):
    """
    Обычный блок тоже сообщает своё место — на экране оно не показывается, но
    поле обязано быть осмысленным, а не нулевым: экран решает сам, что рисовать.
    """
    day, added = await _day_with(client, "Обычный блок", count=2, sets=2)

    state = (await client.post("/api/training/start", json={"training_day_id": day})).json()
    assert state["current"]["exercise_number"] == 1
    assert state["current"]["total_exercises"] == 2

    for _ in range(2):                                   # закрываем оба подхода первого
        state = (await client.post("/api/training/set", json={
            "session_id": state["session_id"],
            "exercise_id": state["current"]["exercise"]["id"],
            "weight": 50.0, "reps": 10,
        })).json()

    assert state["current"]["exercise_number"] == 2
    assert state["current"]["is_circuit"] is False


# ---------------------------------------------------------------- недельная сводка


async def _set_session_date(session_id: str, moment):
    import uuid

    from sqlalchemy import update

    from database.models import TrainingSession

    async with session_maker() as db:
        await db.execute(
            update(TrainingSession)
            .where(TrainingSession.id == uuid.UUID(session_id))
            .values(date=moment)
        )
        await db.commit()


async def _backdate(session_id: str, days: int):
    """Отодвигает тренировку на `days` суток назад — для проверок по неделям."""
    from services.clock import utcnow

    await _set_session_date(session_id, utcnow() - timedelta(days=days))


async def _backdate_to(session_id: str, day: date):
    """
    Ставит тренировке конкретную ДАТУ в поясе клиента.

    В базе дата лежит в naive-UTC, а недели раскладываются по календарю пользователя,
    поэтому целимся в полдень: 12:00 по клиенту не съедет в соседние сутки ни при
    каком разумном поясе. Нужно там, где важны именно РАЗНЫЕ ДАТЫ внутри одной недели,
    а сдвигом на целые сутки их не набрать — тот же день недели уедет в другую неделю.
    """
    moment = (
        datetime.combine(day, time(12, 0), tzinfo=ZoneInfo(TZ_NAME))
        .astimezone(timezone.utc)
        .replace(tzinfo=None)
    )
    await _set_session_date(session_id, moment)


def _this_monday() -> date:
    from services.clock import today_in

    today = today_in(ZoneInfo(TZ_NAME))
    return today - timedelta(days=today.weekday())


@pytest.mark.anyio
async def test_weekly_progress_reports_goal_and_done(client: httpx.AsyncClient):
    """
    Кольцо недели: цель — число тренировочных дней в программе, «сделано» — сколько
    РАЗНЫХ дней этой недели были тренировочными.
    """
    filled = await _program_with_days(client, "Неделя", [0, 2, 4])

    boot = (await client.get("/api/bootstrap", headers=TZ_HEADERS)).json()
    assert boot["week"]["done"] == 0
    assert boot["week"]["goal"] == 3
    assert boot["week"]["streak"] == 0

    await _train(client, filled[0]["id"])
    boot = (await client.get("/api/bootstrap", headers=TZ_HEADERS)).json()
    assert boot["week"]["done"] == 1
    # Одна тренировка из трёх серию не открывает: неделя войдёт в неё только закрытой.
    assert boot["week"]["streak"] == 0


@pytest.mark.anyio
async def test_weekly_left_counts_only_training_days_still_ahead(client: httpx.AsyncClient):
    """
    `left` — сколько тренировочных дней программы на этой неделе ещё впереди.

    Из него экран берёт потолок для «ещё N тренировок»: иначе в пятницу с нулём
    сделанных он просил бы четыре тренировки за два оставшихся дня.
    """
    filled = await _program_with_days(client, "Впереди", [0])   # тренировочный день — сегодня

    boot = (await client.get("/api/bootstrap", headers=TZ_HEADERS)).json()
    assert boot["week"]["left"] == 1            # сегодня ещё не отработан — он и есть остаток

    await _train(client, filled[0]["id"])
    boot = (await client.get("/api/bootstrap", headers=TZ_HEADERS)).json()
    assert boot["week"]["left"] == 0            # отработанный сегодня в остаток не идёт
    assert boot["week"]["goal"] == 1            # а цель недели от этого не меняется


@pytest.mark.anyio
async def test_weekly_left_ignores_days_already_behind(client: httpx.AsyncClient):
    """
    Прошедшие дни недели в остаток не попадают.

    Считаем по ДНЮ НЕДЕЛИ, а не по дате: вчерашний день недели остаётся впереди
    ровно в одном случае — сегодня понедельник, и «вчера» это воскресенье, до
    которого ещё шесть суток текущей недели.
    """
    from zoneinfo import ZoneInfo

    from services.clock import today_in

    await _program_with_days(client, "Позади", [1])             # тренировочный день — вчерашний

    today = today_in(ZoneInfo(TZ_NAME))
    yesterday_ahead = (today - timedelta(days=1)).weekday() > today.weekday()

    boot = (await client.get("/api/bootstrap", headers=TZ_HEADERS)).json()
    assert boot["week"]["left"] == (1 if yesterday_ahead else 0)


async def _week_of(client: httpx.AsyncClient, day_id: int, days: list[date]):
    """Отрабатывает переданные даты — по тренировке на каждую."""
    for day in days:
        await _backdate_to(await _train(client, day_id), day)


@pytest.mark.anyio
async def test_weekly_streak_counts_only_weeks_where_the_goal_was_met(client: httpx.AsyncClient):
    """
    В серию идут недели, где цель ЗАКРЫТА, а не те, где просто была тренировка.

    Раньше хватало одного похода в зал, и серия росла месяцами у человека, который
    из четырёх запланированных дней делал один: карточка показывала «серия: 8 недель»
    рядом с кольцом 0/4. Число обязано означать то, чем его называют.
    """
    filled = await _program_with_days(client, "Серия", [0, 1])   # цель — два дня в неделю
    monday = _this_monday()
    prev, prev2 = monday - timedelta(days=7), monday - timedelta(days=14)

    await _week_of(client, filled[0]["id"], [prev, prev + timedelta(days=1)])   # закрыта
    await _week_of(client, filled[0]["id"], [prev2])                            # 1 из 2

    boot = (await client.get("/api/bootstrap", headers=TZ_HEADERS)).json()
    assert boot["week"]["goal"] == 2
    assert boot["week"]["done"] == 0
    # Грейс на текущую, прошлая закрыта, позапрошлая — нет: цепочка длиной в неделю.
    assert boot["week"]["streak"] == 1

    # Добрали позапрошлую до цели — серия удлинилась, а не осталась прежней.
    await _week_of(client, filled[0]["id"], [prev2 + timedelta(days=1)])
    boot = (await client.get("/api/bootstrap", headers=TZ_HEADERS)).json()
    assert boot["week"]["streak"] == 2


@pytest.mark.anyio
async def test_weekly_streak_ignores_which_weekdays_were_trained(client: httpx.AsyncClient):
    """
    Неделя закрывается ЧИСЛОМ тренировок, а не попаданием в расписание.

    Цель задана количеством дней, поэтому отработанная в среду субботняя программа
    закрывает неделю наравне с субботней. Иначе «серия» наказывала бы за перенос,
    хотя сам перенос приложение поощряет — кнопкой «Отработать» и выбором дня.
    """
    from services.clock import today_in

    filled = await _program_with_days(client, "Не по расписанию", [0, 1])

    # Дни программы — сегодняшний и вчерашний; тренироваться будем в другие.
    today = today_in(ZoneInfo(TZ_NAME))
    scheduled = {today.weekday(), (today - timedelta(days=1)).weekday()}

    prev = _this_monday() - timedelta(days=7)
    free = [prev + timedelta(days=i) for i in range(7)
            if (prev + timedelta(days=i)).weekday() not in scheduled][:2]

    await _week_of(client, filled[0]["id"], free)

    boot = (await client.get("/api/bootstrap", headers=TZ_HEADERS)).json()
    assert boot["week"]["goal"] == 2
    assert boot["week"]["streak"] == 1


@pytest.mark.anyio
async def test_weekly_streak_has_grace_but_resets_on_a_full_gap(client: httpx.AsyncClient):
    """
    Серия мягкая: пустая ТЕКУЩАЯ неделя её не рвёт (грейс на неё), но целая пропущенная
    неделя между тренировкой и сегодня — рвёт. Пропуск дня в зале бывает по делу,
    наказывать за него сбросом цепочки демотивирует.
    """
    filled = await _program_with_days(client, "Грейс", [0])
    session_id = await _train(client, filled[0]["id"])

    # Единственную тренировку двигаем в прошлую неделю: этой недели нет, серия держится.
    await _backdate(session_id, days=7)
    boot = (await client.get("/api/bootstrap", headers=TZ_HEADERS)).json()
    assert boot["week"]["done"] == 0
    assert boot["week"]["streak"] == 1

    # Двигаем на позапрошлую: между ней и сегодня целая пустая неделя — грейса не хватает.
    await _backdate(session_id, days=14)
    boot = (await client.get("/api/bootstrap", headers=TZ_HEADERS)).json()
    assert boot["week"]["streak"] == 0


# ---------------------------------------------------------------- календарь активности


@pytest.mark.anyio
async def test_activity_calendar_counts_days_not_sessions(client: httpx.AsyncClient):
    """
    Подпись над сеткой считает ДНИ, потому что клетка сетки — это день.

    Две тренировки за сутки красят один квадрат, поэтому «19 тренировок» над
    восемнадцатью квадратами читалось как сбой отрисовки, а не как «дважды сходил».
    """
    filled = await _program_with_days(client, "Календарь", [0])

    await _train(client, filled[0]["id"])
    await _train(client, filled[0]["id"])          # вторая сессия в тот же день

    activity = (await client.get("/api/stats/activity", headers=TZ_HEADERS)).json()
    assert activity["active_days"] == 1
    assert activity["days"][-1]["sets"] == 2       # подходы обеих сессий в одной клетке


@pytest.mark.anyio
async def test_activity_calendar_starts_on_monday_and_ends_today(client: httpx.AsyncClient):
    """
    Сетка выровнена на понедельник и обрывается сегодняшним днём.

    Понедельник — потому что колонка это неделя, а строка день недели: без выравнивания
    столбцы поехали бы и соседние квадраты оказались бы разными днями. А хвост текущей
    недели сервер не досылает: будущих дней не существует, пустые клетки под форму
    последней колонки дорисовывает клиент (`heatmap()` в profile.js).
    """
    activity = (await client.get("/api/stats/activity", headers=TZ_HEADERS)).json()

    assert date.fromisoformat(activity["start"]).weekday() == 0
    assert activity["days"][0]["date"] == activity["start"]
    assert activity["days"][-1]["date"] == activity["today"]
    assert len(activity["days"]) == 7 * (activity["weeks"] - 1) + _this_monday_offset() + 1


def _this_monday_offset() -> int:
    from services.clock import today_in

    return today_in(ZoneInfo(TZ_NAME)).weekday()


# ---------------------------------------------------------------- сборка программы


@pytest.mark.anyio
async def test_exercises_are_added_in_one_batch_keeping_their_order(client: httpx.AsyncClient):
    """
    Пачка ложится в день в том же порядке, в каком приехала.

    Порядок здесь не косметика: подряд идущие круговые собираются в ОДИН круговой
    блок, поэтому перестановка меняет саму структуру тренировки. Ради этого пачка
    и добавляется одним запросом — отдельные параллельные запросы с клиента
    завершаются как придётся, и структура зависела бы от сети.
    """
    program = (await client.post("/api/programs", json={"name": "Пачка"})).json()["program"]
    day = (await client.get(f"/api/programs/{program['id']}/days")).json()["days"][0]

    back = next(
        c for c in (await client.get("/api/catalog")).json()["categories"] if c["name"] == "Спина"
    )
    picks = (await client.get(f"/api/catalog/{back['id']}")).json()["exercises"][:3]

    added = (await client.post(f"/api/days/{day['id']}/exercises/batch", json={
        "items": [{"admin_exercise_id": p["id"], "circle_training": False} for p in picks],
    })).json()

    assert [e["name"] for e in added["exercises"]] == [p["name"] for p in picks]

    # Второй заход дописывает в конец, а не смешивается с уже лежащим.
    tail = (await client.post(f"/api/days/{day['id']}/exercises/batch", json={
        "items": [{"admin_exercise_id": picks[0]["id"], "circle_training": True}],
    })).json()["exercises"]

    assert len(tail) == 4
    assert tail[-1]["circle"] is True


@pytest.mark.anyio
async def test_catalog_carries_every_exercise_for_search(client: httpx.AsyncClient):
    """
    Каталог отдаёт и группы, и плоский список — по нему ищет клиент.

    Ходить на сервер за каждой буквой нельзя: круг до пода 50–90 мс, поиск обязан
    отвечать мгновенно. Плоский список нужен ещё и затем, чтобы искать СРАЗУ ПО
    ВСЕМ группам: помнить, «жим стоя» — это дельты или грудь, пользователь не должен.
    """
    catalog = (await client.get("/api/catalog")).json()
    names = {e["name"] for e in catalog["exercises"]}

    assert "Жим штанги лёжа" in names
    assert {e["category_id"] for e in catalog["exercises"]} <= {c["id"] for c in catalog["categories"]}

    # Своё упражнение видно в том же списке — иначе поиск бы его не нашёл.
    arms = next(c for c in catalog["categories"] if c["name"] == "Руки")
    created = (await client.post("/api/user-exercises", json={
        "name": "Молотки лёжа", "description": "", "category_id": arms["id"],
    })).json()["exercise"]

    catalog = (await client.get("/api/catalog")).json()
    mine = next(e for e in catalog["exercises"] if e["id"] == created["id"] and e["kind"] == "user")
    assert mine["name"] == "Молотки лёжа"


@pytest.mark.anyio
async def test_the_renamed_category_is_not_a_stub(client: httpx.AsyncClient):
    """«Трап.» читалось как обрезок и съехавшая вёрстка, а не как название группы."""
    names = {c["name"] for c in (await client.get("/api/catalog")).json()["categories"]}

    assert "Трапеции" in names
    assert "Трап." not in names


@pytest.mark.anyio
async def test_a_template_creates_a_filled_program(client: httpx.AsyncClient):
    """
    Готовая программа создаётся сразу с упражнениями — в этом весь её смысл.

    Пустые семь дней «отдых» — это чистый лист, на котором новичок и застревает:
    он не знает ни какие упражнения брать, ни сколько дней в неделю ходить. Собирает
    сервер, а не клиент двадцатью запросами: программа обязана появиться либо целиком,
    либо никак.
    """
    templates = (await client.get("/api/programs/templates")).json()["templates"]
    template = next(t for t in templates if t["id"] == "ppl3")

    program = (await client.post("/api/programs", json={
        "name": template["name"], "template": template["id"],
    })).json()["program"]

    days = (await client.get(f"/api/programs/{program['id']}/days")).json()["days"]
    filled = {d["day_of_week"]: [e["name"] for e in d["exercises"]] for d in days if d["exercises"]}

    assert len(filled) == template["days"]
    for preview in template["preview"]:
        assert filled[preview["day_of_week"]] == preview["exercises"]

    # Остальные дни остаются выходными, а не заводятся пустыми копиями.
    assert len(days) == 7

    # И программа сразу активна — ею для того и создают.
    boot = (await client.get("/api/bootstrap", headers=TZ_HEADERS)).json()
    assert boot["has_program"] is True


@pytest.mark.anyio
async def test_an_unknown_template_is_refused_rather_than_silently_empty(client: httpx.AsyncClient):
    """Опечатка в id шаблона обязана быть слышной: пустая программа выглядит как своя."""
    response = await client.post("/api/programs", json={"name": "Опечатка", "template": "нет-такого"})
    assert response.status_code == 404


@pytest.mark.anyio
async def test_the_category_rename_lands_on_an_already_filled_base(client: httpx.AsyncClient):
    """
    Переименование доезжает до баз, заведённых ДО него, — то есть до прода.

    Свежая база получает правильное имя из text_for_db, а вот живую иначе не
    поправить вовсе: orm_create_categories заполняет таблицу, только пока она пуста,
    и на непустой молча выходит. Поэтому имя чинит seed_catalog при старте, и
    проверять надо именно этот путь: возвращаем старое имя и прогоняем сев заново.
    """
    from sqlalchemy import select, update

    from database.models import ExerciseCategory

    async with session_maker() as db:
        await db.execute(
            update(ExerciseCategory)
            .where(ExerciseCategory.name == "Трапеции")
            .values(name="Трап.")
        )
        await db.commit()

    assert "Трап." in {c["name"] for c in (await client.get("/api/catalog")).json()["categories"]}

    async with session_maker() as db:
        await seed_catalog(db)

    catalog = (await client.get("/api/catalog")).json()["categories"]
    assert "Трапеции" in {c["name"] for c in catalog}
    assert "Трап." not in {c["name"] for c in catalog}

    # Категория та же самая, а не новая рядом со старой: упражнения остались при ней.
    async with session_maker() as db:
        rows = (await db.execute(select(ExerciseCategory))).scalars().all()
    assert len([c for c in rows if c.name in ("Трапеции", "Трап.")]) == 1
    assert next(c["count"] for c in catalog if c["name"] == "Трапеции") >= 2


@pytest.mark.anyio
async def test_the_whole_order_is_saved_in_one_go(client: httpx.AsyncClient):
    """
    Перетаскивание присылает конечный порядок целиком, а не серию обменов соседями.

    Пошаговые move_up/move_down означали бы N запросов на одно движение пальца,
    и каждое промежуточное состояние сервер записал бы как настоящую программу.
    """
    program = (await client.post("/api/programs", json={"name": "Порядок"})).json()["program"]
    day = (await client.get(f"/api/programs/{program['id']}/days")).json()["days"][0]

    legs = next(
        c for c in (await client.get("/api/catalog")).json()["categories"] if c["name"] == "Ноги"
    )
    picks = (await client.get(f"/api/catalog/{legs['id']}")).json()["exercises"][:3]
    added = (await client.post(f"/api/days/{day['id']}/exercises/batch", json={
        "items": [{"admin_exercise_id": p["id"]} for p in picks],
    })).json()["exercises"]

    ids = [e["id"] for e in added]
    flipped = list(reversed(ids))

    result = (await client.patch(f"/api/days/{day['id']}/exercises/order",
                                 json={"ids": flipped})).json()
    assert [e["id"] for e in result["exercises"]] == flipped
    assert [e["position"] for e in result["exercises"]] == [0, 1, 2]

    # Порядок держится и после перечитывания дня — это не только ответ обработчика.
    again = (await client.get(f"/api/day/{day['id']}")).json()["exercises"]
    assert [e["id"] for e in again] == flipped


@pytest.mark.anyio
async def test_a_partial_order_changes_nothing(client: httpx.AsyncClient):
    """
    Неполный список отвергается целиком.

    Разложив по нему позиции, мы оставили бы недосланные упражнения на старых
    местах — и они перемешались бы с новыми. Порядок здесь определяет структуру
    тренировки: подряд идущие круговые собираются в один круг.
    """
    program = (await client.post("/api/programs", json={"name": "Неполный"})).json()["program"]
    day = (await client.get(f"/api/programs/{program['id']}/days")).json()["days"][0]

    legs = next(
        c for c in (await client.get("/api/catalog")).json()["categories"] if c["name"] == "Ноги"
    )
    picks = (await client.get(f"/api/catalog/{legs['id']}")).json()["exercises"][:3]
    added = (await client.post(f"/api/days/{day['id']}/exercises/batch", json={
        "items": [{"admin_exercise_id": p["id"]} for p in picks],
    })).json()["exercises"]

    ids = [e["id"] for e in added]

    assert (await client.patch(f"/api/days/{day['id']}/exercises/order",
                               json={"ids": ids[:2]})).status_code == 400
    # Дубль вместо пропущенного — тоже мимо: длина сходится, а состав нет.
    assert (await client.patch(f"/api/days/{day['id']}/exercises/order",
                               json={"ids": [ids[0], ids[1], ids[1]]})).status_code == 400

    assert [e["id"] for e in (await client.get(f"/api/day/{day['id']}")).json()["exercises"]] == ids


@pytest.mark.anyio
async def test_the_day_carries_the_rounds_of_its_program(client: httpx.AsyncClient):
    """
    Экран дня показывает число кругов и правит его на месте.

    Круги — настройка ПРОГРАММЫ: build_plan разворачивает по ней все круговые блоки
    дня, собственного числа у блока в схеме нет. Поэтому оно и приезжает вместе
    с днём — иначе за ним пришлось бы уходить в настройки программы.
    """
    program = (await client.post("/api/programs", json={"name": "Круги"})).json()["program"]
    day = (await client.get(f"/api/programs/{program['id']}/days")).json()["days"][0]

    assert (await client.get(f"/api/day/{day['id']}")).json()["program"] == {
        "id": program["id"], "circular_rounds": 3,
    }

    await client.patch(f"/api/programs/{program['id']}", json={"circular_rounds": 5})
    assert (await client.get(f"/api/day/{day['id']}")).json()["program"]["circular_rounds"] == 5


# ---------------------------------------------------------------- снаряд и шаг веса


async def _day_with_preset(client: httpx.AsyncClient, name: str, program_name: str) -> dict:
    """Программа → первый день → одно упражнение каталога по названию."""
    program = (await client.post("/api/programs", json={"name": program_name})).json()["program"]
    day = (await client.get(f"/api/programs/{program['id']}/days")).json()["days"][0]

    preset = next(
        e for e in (await client.get("/api/catalog")).json()["exercises"] if e["name"] == name
    )
    added = (await client.post(f"/api/days/{day['id']}/exercises", json={
        "admin_exercise_id": preset["id"], "circle_training": False,
    })).json()["exercises"]

    return {"program": program, "day": day, "exercise": added[-1]}


@pytest.mark.anyio
async def test_the_step_of_the_weight_comes_from_the_equipment(client: httpx.AsyncClient):
    """
    Шаг кнопок «−/+» задаёт снаряд, а не константа 2.5 на всё подряд.

    На блоке вес набирается только блоками целиком; на ряду гантелей
    шаг 2, и 2.5 промахивается мимо каждой второй. Раньше шаг был зашит в клиенте
    одним числом, поэтому кнопки предлагали то, чего в зале нет.
    """
    expected = {
        "Жим штанги лёжа": ("barbell", 2.5),
        "Махи гантелями в стороны": ("dumbbell", 2.0),
        "Тяга верхнего блока": ("stack", 5.0),      # вес блока, а не шаг в кг
        "Жим ногами": ("machine", 2.5),
    }

    for name, (equipment, step) in expected.items():
        built = await _day_with_preset(client, name, f"Шаг {name}")
        assert built["exercise"]["equipment"] == equipment, name
        assert built["exercise"]["step"] == step, name

        # И на экране подхода тоже: клиент читает снаряд оттуда же.
        state = (await client.post("/api/training/start", json={
            "training_day_id": built["day"]["id"],
        })).json()
        assert state["current"]["exercise"]["equipment"] == equipment, name
        assert state["current"]["exercise"]["step"] == step, name


@pytest.mark.anyio
async def test_bodyweight_still_allows_the_belt(client: httpx.AsyncClient):
    """
    Свой вес — это «вес необязателен», а НЕ «веса не бывает».

    Подтягивания и брусья сплошь и рядом делают с блином на поясе. Первая версия
    убирала поле веса совсем — и записать такой подход было нечем. Поэтому снаряд
    остаётся признаком «поле свёрнуто», а шаг приезжает числом: пояс грузят
    обычными блинами, и кнопкам есть чем ходить.

    Решение принимает клиент И ТОЛЬКО по снаряду. По отсутствию шага его принимать
    нельзя: поле, которого в ответе нет вовсе (старый сервер под новым клиентом),
    выглядело бы так же, и жим гантелей молча писался бы с нулевым весом.
    """
    built = await _day_with_preset(client, "Подтягивания", "Свой вес")
    assert built["exercise"]["equipment"] == "bodyweight"
    assert built["exercise"]["step"] == 2.5

    state = (await client.post("/api/training/start", json={
        "training_day_id": built["day"]["id"],
    })).json()
    assert state["current"]["exercise"]["equipment"] == "bodyweight"
    assert state["current"]["exercise"]["step"] == 2.5

    # Подход с поясом записывается как обычный, и вес идёт в объём.
    state = (await client.post("/api/training/set", json={
        "session_id": state["session_id"],
        "exercise_id": built["exercise"]["id"],
        "weight": 10.0,
        "reps": 8,
    })).json()
    assert state["sets"][0]["weight"] == 10.0

    # И без пояса — тоже: нулевой вес это факт «просто подтянулся», а не пробел.
    state = (await client.post("/api/training/set", json={
        "session_id": state["session_id"],
        "exercise_id": built["exercise"]["id"],
        "weight": 0.0,
        "reps": 6,
    })).json()
    assert [s["weight"] for s in state["sets"]] == [10.0, 0.0]


@pytest.mark.anyio
async def test_the_step_can_be_pinned_to_the_gym(client: httpx.AsyncClient):
    """
    Шаг переопределяется на упражнении: снаряд задаёт начало, а не приговор.

    У блока это вес одного блока (у разных станков 4.5, 5, 5.5, 7), у остальных —
    шаг кнопок. Угадать за пользователя нельзя: любое число верно для одного зала
    и мимо для соседнего.
    """
    built = await _day_with_preset(client, "Тяга верхнего блока", "Мой зал")
    assert built["exercise"]["step"] == 5.0

    await client.patch(f"/api/exercises/{built['exercise']['id']}", json={"weight_step": 4.5})
    assert (await client.get(f"/api/day/{built['day']['id']}")).json()["exercises"][-1]["step"] == 4.5

    # Экран подхода читает тот же шаг — иначе настройка не доехала бы туда,
    # ради чего её и правили.
    state = (await client.post("/api/training/start", json={
        "training_day_id": built["day"]["id"],
    })).json()
    assert state["current"]["exercise"]["step"] == 4.5

    # Ноль — способ сказать «как у снаряда»: PATCH не умеет присылать NULL,
    # а отменить переопределение чем-то надо.
    await client.patch(f"/api/exercises/{built['exercise']['id']}", json={"weight_step": 0})
    assert (await client.get(f"/api/day/{built['day']['id']}")).json()["exercises"][-1]["step"] == 5.0


@pytest.mark.anyio
async def test_an_own_exercise_carries_the_equipment_it_was_created_with(client: httpx.AsyncClient):
    """
    Своё упражнение — единственное место, где снаряд спрашивают у пользователя.

    И правка снаряда догоняет уже разложенные по дням копии: название человек
    правит для себя, а снаряд читает интерфейс, и правят его ровно затем, чтобы
    кнопки веса начали ходить как надо.
    """
    catalog = (await client.get("/api/catalog")).json()
    assert {e["id"] for e in catalog["equipment"]} >= {"barbell", "dumbbell", "stack", "bodyweight"}

    back = next(c for c in catalog["categories"] if c["name"] == "Спина")
    created = (await client.post("/api/user-exercises", json={
        "name": "Тяга в хаммере", "description": "", "category_id": back["id"],
        "equipment": "machine",
    })).json()["exercise"]
    assert created["equipment"] == "machine"

    program = (await client.post("/api/programs", json={"name": "Своё"})).json()["program"]
    day = (await client.get(f"/api/programs/{program['id']}/days")).json()["days"][0]
    added = (await client.post(f"/api/days/{day['id']}/exercises", json={
        "user_exercise_id": created["id"], "circle_training": False,
    })).json()["exercises"][-1]
    assert added["step"] == 2.5

    await client.patch(f"/api/user-exercises/{created['id']}", json={
        "name": "Тяга в хаммере", "description": "", "category_id": back["id"],
        "equipment": "dumbbell",
    })

    fixed = (await client.get(f"/api/day/{day['id']}")).json()["exercises"][-1]
    assert fixed["equipment"] == "dumbbell"
    assert fixed["step"] == 2.0


@pytest.mark.anyio
async def test_an_unknown_equipment_is_refused(client: httpx.AsyncClient):
    """Снаряд решает, есть ли поле веса вообще, — выдумке тут взяться неоткуда."""
    back = next(
        c for c in (await client.get("/api/catalog")).json()["categories"] if c["name"] == "Спина"
    )
    resp = await client.post("/api/user-exercises", json={
        "name": "Ерунда", "description": "", "category_id": back["id"], "equipment": "кувалда",
    })
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_the_equipment_lands_on_an_already_filled_base(client: httpx.AsyncClient):
    """
    Снаряд доезжает до баз, заведённых ДО него, — то есть до прода.

    Миграция может дать существующим строкам только 'other' (шаг 2.5, ровно старое
    поведение): каталог наполняется кодом, и id одного пресета на прод- и тест-контуре
    разные. Догоняет их seed_catalog по названию — тот же путь, что и у переименования
    категории. Проверяем именно его: сбрасываем снаряд и прогоняем сев заново.

    Второй фронт — упражнения, уже разложенные по дням: они держат СНИМОК снаряда,
    и у старых строк там NULL.
    """
    from sqlalchemy import select, update

    from database.models import AdminExercises, Exercise

    built = await _day_with_preset(client, "Тяга верхнего блока", "Старая база")
    assert built["exercise"]["step"] == 5.0

    async with session_maker() as db:
        await db.execute(update(AdminExercises).values(equipment="other"))
        await db.execute(update(Exercise).values(equipment=None))
        await db.commit()

    stale = (await client.get(f"/api/day/{built['day']['id']}")).json()["exercises"][-1]
    assert stale["step"] == 2.5      # неизвестный снаряд ведёт себя как раньше, а не падает

    async with session_maker() as db:
        await seed_catalog(db)

    fixed = (await client.get(f"/api/day/{built['day']['id']}")).json()["exercises"][-1]
    assert fixed["equipment"] == "stack"
    assert fixed["step"] == 5.0

    async with session_maker() as db:
        presets = (await db.execute(select(AdminExercises))).scalars().all()
    assert {p.equipment for p in presets} >= {"barbell", "dumbbell", "stack", "bodyweight"}
    # Сев идемпотентен: повторный проход не находит работы и ничего не портит.
    async with session_maker() as db:
        await seed_catalog(db)
    assert (await client.get(f"/api/day/{built['day']['id']}")).json()["exercises"][-1]["step"] == 5.0


@pytest.mark.anyio
async def test_the_duplicate_presets_collapse_without_losing_history(client: httpx.AsyncClient):
    """
    Двойники схлопываются, а записанные подходы остаются на месте.

    На проде «Молотки гантелями» приехали из бота, «Молотки» — из нового каталога,
    и год они прожили в списке рядом. Слить их удалением старой карточки нельзя:
    у exercise.admin_exercise_id стоит ON DELETE CASCADE, у exercise → set тоже,
    поэтому удаление унесло бы подходы вместе с карточкой. Сначала перецепка,
    потом удаление — этот порядок тест и стережёт.
    """
    from sqlalchemy import select

    from database.models import AdminExercises, Exercise, Set

    # Возвращаем базу в состояние прода: старая карточка рядом с канонической.
    async with session_maker() as db:
        canonical = (await db.execute(
            select(AdminExercises).where(AdminExercises.name == "Молотки")
        )).scalar_one()
        db.add(AdminExercises(
            name="Молотки гантелями",
            description="Приехало из бота",
            category_id=canonical.category_id,
            equipment="other",
        ))
        await db.commit()
        canonical_id = canonical.id

    built = await _day_with_preset(client, "Молотки гантелями", "Старая база")
    state = (await client.post("/api/training/start", json={
        "training_day_id": built["day"]["id"],
    })).json()
    await client.post("/api/training/set", json={
        "session_id": state["session_id"],
        "exercise_id": built["exercise"]["id"],
        "weight": 22.5,
        "reps": 8,
    })

    async with session_maker() as db:
        await seed_catalog(db)

    names = {e["name"] for e in (await client.get("/api/catalog")).json()["exercises"]}
    assert "Молотки" in names
    assert "Молотки гантелями" not in names

    async with session_maker() as db:
        exercise = (await db.execute(
            select(Exercise).where(Exercise.id == built["exercise"]["id"])
        )).scalar_one()
        sets = (await db.execute(
            select(Set).where(Set.exercise_id == built["exercise"]["id"])
        )).scalars().all()

    # Упражнение дня уцелело и смотрит на каноническую карточку.
    assert exercise.admin_exercise_id == canonical_id
    # Название в дне — снимок, оно и должно остаться прежним.
    assert exercise.name == "Молотки гантелями"
    assert [(s.weight, s.repetitions) for s in sets] == [(22.5, 8)]


@pytest.mark.anyio
async def test_the_preset_renamed_by_a_single_letter_keeps_its_card(client: httpx.AsyncClient):
    """
    «Жим штанги лежа» → «лёжа»: карточка та же, а не вторая рядом.

    Именно на этой букве каталог и разъехался. Сверка снаряда идёт ПО ИМЕНИ,
    поэтому строка без «ё» никогда не совпадала с CATALOG — снаряд ей не
    проставлялся, а досыпка заводила рядом второй жим. Когда канонической
    карточки ещё нет, слияние обязано переименовать старую, а не создавать новую.
    """
    from sqlalchemy import select, update

    from database.models import AdminExercises

    async with session_maker() as db:
        await db.execute(
            update(AdminExercises)
            .where(AdminExercises.name == "Жим штанги лёжа")
            .values(name="Жим штанги лежа", equipment="other")
        )
        await db.commit()
        before = (await db.execute(
            select(AdminExercises.id).where(AdminExercises.name == "Жим штанги лежа")
        )).scalar_one()

    async with session_maker() as db:
        await seed_catalog(db)

    async with session_maker() as db:
        rows = (await db.execute(
            select(AdminExercises).where(AdminExercises.name.in_(
                ("Жим штанги лежа", "Жим штанги лёжа")
            ))
        )).scalars().all()

    assert len(rows) == 1, "второй жим рядом со старым — это и был баг"
    assert rows[0].id == before, "карточка та же самая, а не заведённая заново"
    assert rows[0].name == "Жим штанги лёжа"
    assert rows[0].equipment == "barbell", "после переименования снаряд наконец совпал"


@pytest.mark.anyio
async def test_the_renamed_category_unblocks_the_presets_behind_it(client: httpx.AsyncClient):
    """
    Пока категория называлась «Грудные», в неё не заезжал НИ ОДИН пресет груди.

    Досыпка ищет категорию по имени и незнакомое молча пропускает
    (`category not in categories`). Из-за этого прод год выглядел наполненным,
    а половины груди в каталоге просто не было — и заметить это по логам нельзя,
    пропуск ничего не пишет.
    """
    from sqlalchemy import delete, select, update

    from database.models import AdminExercises, ExerciseCategory

    async with session_maker() as db:
        chest = (await db.execute(
            select(ExerciseCategory).where(ExerciseCategory.name == "Грудь")
        )).scalar_one()
        await db.execute(delete(AdminExercises).where(AdminExercises.category_id == chest.id))
        await db.execute(
            update(ExerciseCategory).where(ExerciseCategory.id == chest.id).values(name="Грудные")
        )
        await db.commit()

    async with session_maker() as db:
        await seed_catalog(db)

    catalog = (await client.get("/api/catalog")).json()
    groups = {c["name"]: c["id"] for c in catalog["categories"]}
    assert "Грудь" in groups and "Грудные" not in groups

    chest_names = {
        e["name"] for e in catalog["exercises"] if e["category_id"] == groups["Грудь"]
    }
    assert "Жим штанги лёжа" in chest_names
    assert "Брусья" in chest_names


@pytest.mark.anyio
async def test_the_preset_filed_under_the_wrong_group_moves_home(client: httpx.AsyncClient):
    """
    «Жим гантелей лёжа» лежал в «Прессе», «Становая тяга» — в «Ногах».

    Категория карточки — такой же факт из CATALOG, как снаряд, и руками её тоже
    негде выставить. Пока она врёт, упражнение не находится там, где его ищут.
    """
    from sqlalchemy import select, update

    from database.models import AdminExercises, ExerciseCategory

    async with session_maker() as db:
        abs_id = (await db.execute(
            select(ExerciseCategory.id).where(ExerciseCategory.name == "Пресс")
        )).scalar_one()
        await db.execute(
            update(AdminExercises)
            .where(AdminExercises.name == "Жим гантелей лёжа")
            .values(category_id=abs_id)
        )
        await db.commit()

    before = (await client.get("/api/catalog")).json()
    groups = {c["name"]: c["id"] for c in before["categories"]}
    press = next(e for e in before["exercises"] if e["name"] == "Жим гантелей лёжа")
    assert press["category_id"] == groups["Пресс"]

    async with session_maker() as db:
        await seed_catalog(db)

    after = (await client.get("/api/catalog")).json()
    groups = {c["name"]: c["id"] for c in after["categories"]}
    chest = next(e for e in after["exercises"] if e["name"] == "Жим гантелей лёжа")
    assert chest["category_id"] == groups["Грудь"]


@pytest.mark.anyio
async def test_the_stack_is_counted_in_blocks_but_stored_in_kilograms(client: httpx.AsyncClient):
    """
    Блок считается блоками, а хранится килограммами.

    У станка человек не набирает вес — он втыкает пин в блок и знает его номер.
    У разных станков блоки разные, поэтому шаг в килограммах здесь неправильная единица
    в принципе. Но объём, рекорды и графики живут в килограммах и общие для всех
    снарядов, поэтому перевод делает клиент по весу блока, а сервер продолжает
    получать килограммы — ничего в слое данных про блоки не знает.

    Сервер обязан отдать ровно два числа, из которых перевод складывается: снаряд
    и вес блока. Здесь проверяется, что они доезжают до ВСЕХ трёх экранов, где
    подход показывают, — иначе один и тот же подход выглядел бы по-разному.
    """
    built = await _day_with_preset(client, "Тяга горизонтального блока", "Блок")
    assert built["exercise"]["equipment"] == "stack"
    assert built["exercise"]["step"] == 5.0

    state = (await client.post("/api/training/start", json={
        "training_day_id": built["day"]["id"],
    })).json()
    assert state["current"]["exercise"]["equipment"] == "stack"
    assert state["current"]["exercise"]["step"] == 5.0

    # Семь блоков при блоке 5 кг — это 35 кг, и в базу уходят именно они.
    state = (await client.post("/api/training/set", json={
        "session_id": state["session_id"],
        "exercise_id": built["exercise"]["id"],
        "weight": 35.0,
        "reps": 12,
    })).json()
    assert state["sets"][0]["weight"] == 35.0

    await client.post("/api/training/finish", json={"session_id": state["session_id"]})

    # История — третий экран, и он тоже должен знать, чем делить.
    session_id = (await client.get("/api/history")).json()["sessions"][0]["id"]
    detail = (await client.get(f"/api/history/{session_id}")).json()["exercises"][0]
    assert detail["equipment"] == "stack"
    assert detail["step"] == 5.0
    assert detail["sets"][0]["weight"] == 35.0

    # Поправили вес блока — те же 35 кг читаются как другое число блоков.
    # Килограммы при этом не переписываются: что подняли, то и подняли.
    await client.patch(f"/api/exercises/{built['exercise']['id']}", json={"weight_step": 7})
    detail = (await client.get(f"/api/history/{session_id}")).json()["exercises"][0]
    assert detail["step"] == 7.0
    assert detail["sets"][0]["weight"] == 35.0


@pytest.mark.anyio
async def test_the_weight_hint_needs_two_clean_sessions(client: httpx.AsyncClient):
    """
    Подсказка по весу доезжает до экрана подхода — и подчиняется правилу 2-for-2.

    Смысл сквозного теста не в арифметике (её проверяет test_progression.py),
    а в стыке: подсказке нужны ДВЕ прошлые тренировки, а слой данных умел отдавать
    только одну. Без позапрошлого раза правило выродилось бы в «закрыл план —
    повышай», то есть в рост веса после каждого удачно выспавшегося дня.
    """
    built = await _day_with_preset(client, "Жим штанги лёжа", "Прогрессия")
    exercise_id = built["exercise"]["id"]
    await client.patch(f"/api/exercises/{exercise_id}", json={"sets": 2, "reps": 10})

    async def train(weight: float, reps: int) -> dict:
        state = (await client.post("/api/training/start", json={
            "training_day_id": built["day"]["id"],
        })).json()
        for _ in range(2):
            state = (await client.post("/api/training/set", json={
                "session_id": state["session_id"],
                "exercise_id": exercise_id,
                "weight": weight,
                "reps": reps,
            })).json()
        await client.post("/api/training/finish", json={"session_id": state["session_id"]})
        return state

    # Тренировок ещё не было — предлагать не из чего.
    state = (await client.post("/api/training/start", json={
        "training_day_id": built["day"]["id"],
    })).json()
    assert state["current"]["exercise"]["suggest"] is None
    await client.post("/api/training/finish", json={"session_id": state["session_id"]})

    # Первый закрытый план — держим: по 2-for-2 это ещё не подтверждение.
    await train(60.0, 10)
    state = (await client.post("/api/training/start", json={
        "training_day_id": built["day"]["id"],
    })).json()
    assert state["current"]["exercise"]["suggest"] == {"action": "hold", "weight": 60.0}
    await client.post("/api/training/finish", json={"session_id": state["session_id"]})

    # Второй подряд — повышаем на шаг штанги.
    await train(60.0, 10)
    state = (await client.post("/api/training/start", json={
        "training_day_id": built["day"]["id"],
    })).json()
    assert state["current"]["exercise"]["suggest"] == {"action": "up", "weight": 62.5}
