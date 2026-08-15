"""
Заливка баннеров при старте бота.

Telegram подменён фейком: проверяется не отправка, а РЕШЕНИЕ — кому слать, а кому
не надо. Фиксируем то, из-за чего человек получал пачку картинок несколько раз
за день:

* баннер с уже добытым file_id не пересылается (file_id не протухает);
* картинка, под которую нет строки в базе, не шлётся вовсе — сохранять её
  результат было бы некуда, и на следующем старте повторилось бы то же самое.
  Именно так пряталась опечатка `errror.png` при строке `error`;
* баннер без file_id заливается ровно один раз, и повторный запуск уже молчит.
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

_TMP_DB = Path(tempfile.mkdtemp()) / "banners.db"
os.environ.setdefault("DB_URL", f"sqlite+aiosqlite:///{_TMP_DB}")
os.environ.setdefault("MINIAPP_BOT_TOKEN", "123:TEST")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database.engine import engine, session_maker  # noqa: E402
from database.models import Base  # noqa: E402
from database.orm_query import (  # noqa: E402
    orm_add_banner_description,
    orm_change_banner_image,
    orm_get_banner,
)
from utils.load_banners import load_banners_from_folder  # noqa: E402

ADMIN = 851_690_283


class FakePhoto:
    def __init__(self, file_id):
        self.file_id = file_id


class FakeMessage:
    def __init__(self, file_id):
        self.photo = [FakePhoto(file_id)]


class FakeBot:
    """Считает отправки вместо похода в Telegram."""

    def __init__(self):
        self.my_admins_list = [ADMIN]
        self.sent: list[str] = []

    async def send_photo(self, chat_id, photo, caption):
        # Имя баннера — то, что в caption после двоеточия; для теста хватит счётчика.
        self.sent.append(caption)
        return FakeMessage(f"file-id-{len(self.sent)}")

    async def send_message(self, chat_id, text):
        self.sent.append(text)


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def folder(tmp_path, monkeypatch):
    """Папка banners/ из двух картинок, подсунутая вместо рабочей."""
    banners = tmp_path / "banners"
    banners.mkdir()
    (banners / "main.png").write_bytes(b"\x89PNG fake")
    (banners / "profile.jpg").write_bytes(b"\xff\xd8 fake")
    monkeypatch.chdir(tmp_path)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    return banners


@pytest.mark.anyio
async def test_banner_with_file_id_is_not_resent(folder):
    """
    Главный случай. file_id добыт в прошлый раз и не протухает — пересылать
    картинку незачем. Раньше проверки не было, и каждая выкатка била админу
    пачкой «Загружен баннер».
    """
    async with session_maker() as session:
        await orm_add_banner_description(session, {"main": "", "profile": ""})
        await orm_change_banner_image(session, "main", "уже-есть")
        await orm_change_banner_image(session, "profile", "тоже-есть")

    bot = FakeBot()
    async with session_maker() as session:
        await load_banners_from_folder(bot, session)

    assert bot.sent == []


@pytest.mark.anyio
async def test_file_without_matching_row_is_skipped(folder):
    """
    Картинка есть, строки под неё нет. Отправлять бессмысленно: результат
    сохранять некуда, UPDATE поменяет ноль строк, и на следующем старте
    повторится ровно то же. Так и жила опечатка `errror.png`.
    """
    (folder / "errror.png").write_bytes(b"\x89PNG fake")

    async with session_maker() as session:
        await orm_add_banner_description(session, {"main": "", "profile": ""})
        await orm_change_banner_image(session, "main", "есть")
        await orm_change_banner_image(session, "profile", "есть")

    bot = FakeBot()
    async with session_maker() as session:
        await load_banners_from_folder(bot, session)

    assert bot.sent == []


@pytest.mark.anyio
async def test_missing_banner_uploaded_once(folder):
    """Без file_id — заливаем, но ровно один раз: второй запуск уже молчит."""
    async with session_maker() as session:
        await orm_add_banner_description(session, {"main": "", "profile": ""})
        await orm_change_banner_image(session, "profile", "есть")

    bot = FakeBot()
    async with session_maker() as session:
        await load_banners_from_folder(bot, session)

    assert len(bot.sent) == 1 and "main" in bot.sent[0]

    async with session_maker() as session:
        assert (await orm_get_banner(session, "main")).image is not None

    second = FakeBot()
    async with session_maker() as session:
        await load_banners_from_folder(second, session)

    assert second.sent == []


@pytest.mark.anyio
async def test_update_of_unknown_banner_reports_zero_rows(folder):
    """
    Возврат rowcount — то, чего не хватало, чтобы опечатка не пряталась.
    UPDATE несуществующей строки обязан быть отличим от успешного.
    """
    async with session_maker() as session:
        await orm_add_banner_description(session, {"main": ""})
        assert await orm_change_banner_image(session, "main", "x") == 1
        assert await orm_change_banner_image(session, "errror", "x") == 0
