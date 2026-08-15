import logging
import os

from aiogram.types import FSInputFile
from sqlalchemy.ext.asyncio import AsyncSession

from database.orm_query import orm_change_banner_image, orm_get_banner


async def load_banners_from_folder(bot, session: AsyncSession):
    """
    Добывает file_id для картинок из banners/ и сохраняет их в базу.

    file_id у Telegram выдаётся только за отправленное сообщение, поэтому картинка
    заливается ОТПРАВКОЙ её админу — другого способа нет. Зато это разовая
    операция: file_id привязан к боту и не протухает.

    Раньше здесь не было ни одной проверки, и все восемь картинок улетали админу
    при КАЖДОМ старте бота. В кластере под перезапускается на каждую выкатку, так
    что человек получал пачку «Загружен баннер: …» несколько раз за день —
    на ровном месте, ведь file_id уже лежали в базе с прошлого раза.
    """
    folder = "banners"
    if not os.path.exists(folder):
        logging.warning(f"Папка {folder} не найдена, пропускаем загрузку баннеров.")
        return

    banners = [f for f in os.listdir(folder) if f.lower().endswith((".jpg", ".jpeg", ".png"))]
    if not banners:
        logging.info("Нет баннеров для загрузки.")
        return

    # Баннеры заливаются отправкой картинки админу (нужен file_id). Без админа —
    # смысла нет: file_id привязан к боту, и старое чат-меню, которое их показывало,
    # в Mini App-контуре не используется. Тихо пропускаем, чтобы не сыпать ошибками.
    if not getattr(bot, "my_admins_list", None):
        logging.info("Список админов пуст — пропускаем загрузку баннеров.")
        return

    sent, skipped = 0, 0

    for filename in banners:
        path = os.path.join(folder, filename)
        name, _ = os.path.splitext(filename)

        banner = await orm_get_banner(session, name)

        if banner is None:
            # Картинка есть, а строки под неё нет. Отправлять бессмысленно:
            # сохранять file_id будет некуда, и на следующем старте повторится
            # ровно то же самое. Именно так и жила опечатка `errror.png`.
            logging.warning(
                "Баннер %r лежит в папке, но такой строки в базе нет — "
                "имя файла не совпадает ни с одной страницей. Пропускаем.", name
            )
            continue

        if banner.image:
            skipped += 1
            continue

        logging.info(f"Загружаем баннер: {filename}")

        try:
            msg = await bot.send_photo(
                chat_id=bot.my_admins_list[0],
                photo=FSInputFile(path),
                caption=f"Загружен баннер: <b>{name}</b>",
            )
            file_id = msg.photo[-1].file_id

            if await orm_change_banner_image(session, name, file_id):
                sent += 1
                logging.info(f"Баннер {name} обновлён в базе (file_id={file_id[:15]}...).")
            else:
                logging.warning("Баннер %r исчез из базы между чтением и записью.", name)

        except Exception as e:
            logging.exception(f"Ошибка при загрузке баннера {filename}: {e}")
            from aiogram.exceptions import TelegramForbiddenError
            try:
                if not isinstance(e, TelegramForbiddenError):
                    await bot.send_message(
                        chat_id=bot.my_admins_list[0],
                        text=f"⚠️ Ошибка при загрузке {filename}: {e}"
                    )
            except Exception as inner_e:
                logging.error(f"Не удалось отправить сообщение об ошибке админу: {inner_e}")

    logging.info("✅ Баннеры: залито %s, уже было %s.", sent, skipped)
