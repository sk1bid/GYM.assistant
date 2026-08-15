import asyncio
import logging
import os
import contextlib

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ParseMode
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from dotenv import find_dotenv, load_dotenv


from database.orm_query import orm_get_banner
from middlewares.db import DataBaseSession
from database.engine import create_db, drop_db, session_maker
from handlers.user_private import user_private_router
from handlers.admin_private import admin_router
from handlers.user_group import user_group_router
from handlers.miniapp_entry import router as miniapp_router, setup_menu_button
from workers.rest_notifier import rest_notifier, router as rest_router
from utils.load_banners import load_banners_from_folder
from utils import globals

load_dotenv(find_dotenv())

logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("TOKEN")
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
PORT = int(os.getenv("PORT", 8080))

WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"

ADMIN_IDS = [int(id) for id in os.getenv("ADMIN_IDS", "").split(",") if id]

MINIAPP_URL = os.getenv("MINIAPP_URL", "")

# Готовность для kubelet. Без пробы контейнер считается готовым сразу после запуска
# процесса, и бот, падающий на старте (например, на отставшей схеме БД), успевает
# засчитаться доступной репликой, погасить прошлый под и получить «successfully
# rolled out» — а бота нет. Флаг поднимается последним шагом старта, когда база
# проверена, баннеры загружены и воркер отдыха запущен.
_ready = False

dp = Dispatcher()
# Роутеры Mini App идут первыми: /app и кнопка «Закончить отдых» не должны утонуть
# в общих обработчиках старого меню. Кнопку отдыха новый роутер берёт только вне FSM,
# так что тренировка, запущенная из самого бота, по-прежнему ведётся его же кодом.
dp.include_routers(miniapp_router, rest_router, user_private_router, user_group_router, admin_router)


async def on_startup(bot: Bot):
    logging.info("Бот запускается (webhook)...")

    run_param = False
    if run_param:
        await drop_db()
    await create_db()
    # Сначала заливка, потом чтение: иначе в тот самый запуск, когда картинка
    # наконец попадёт в базу, error_pic останется пустым до перезапуска.
    async with session_maker() as session:
        await load_banners_from_folder(bot, session)

    async with session_maker() as session:
        globals.error_pic = await orm_get_banner(session, "error")
    await bot.set_webhook(
        url=WEBHOOK_URL,
        secret_token=WEBHOOK_SECRET,
        drop_pending_updates=True,
        allowed_updates=dp.resolve_used_update_types(),
    )

    await start_rest_notifier(bot)
    with contextlib.suppress(Exception):
        await setup_menu_button(bot)

    for user in bot.my_admins_list:
        with contextlib.suppress(Exception):
            await bot.send_message(user, f"/start")


async def on_shutdown(bot: Bot):
    logging.info("Выключаем вебхук...")
    with contextlib.suppress(Exception):
        await bot.delete_webhook(drop_pending_updates=False)


async def start_health_server() -> None:
    """
    Крошечный сервер только под `/healthz` — для режима поллинга, где своего HTTP
    нет вовсе (в режиме вебхука эту роль играет сам webhook-app).

    503 до конца старта — это и есть смысл затеи: пока бот не поднялся, реплика
    не должна считаться доступной.
    """
    async def healthz(_request: web.Request) -> web.Response:
        return web.Response(status=200 if _ready else 503, text="ok" if _ready else "starting")

    app = web.Application()
    app.router.add_get("/healthz", healthz)

    # access_log=None — иначе kubelet раз в 10 секунд пишет в лог строку об успешной
    # пробе, и за сутки это ~8600 строк, в которых тонет всё остальное.
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    logging.info(f"проба готовности слушает :{PORT}/healthz")


async def init_app(bot: Bot) -> web.Application:

    dp.update.middleware(DataBaseSession(session_pool=session_maker))

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    app = web.Application()

    SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=WEBHOOK_SECRET
    ).register(app, path=WEBHOOK_PATH)

    setup_application(app, dp, bot=bot)

    return app


async def main():
    BOT_API_URL = os.getenv("BOT_API_URL")  # e.g. http://telegram-bot-api:8081
    PROXY_URL = os.getenv("PROXY_URL")

    session = None
    if BOT_API_URL:
        # Локальный telegram-bot-api сервер
        local_server = TelegramAPIServer.from_base(BOT_API_URL)
        session = AiohttpSession(api=local_server)
        logging.info(f"Используем локальный Bot API: {BOT_API_URL}")
    elif PROXY_URL:
        logging.info(f"Используем прокси: {PROXY_URL}")
        session = AiohttpSession(proxy=PROXY_URL)

    if session:
        bot = Bot(token=TOKEN, session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    else:
        logging.info("Прямое подключение к api.telegram.org")
        bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    bot.my_admins_list = ADMIN_IDS
    
    dp.update.middleware(DataBaseSession(session_pool=session_maker))

    if os.getenv("USE_POLLING", "False").lower() == "true":
        logging.info("Starting bot in POLLING mode...")
        await start_health_server()
        await bot.delete_webhook(drop_pending_updates=True)
        dp.startup.register(on_startup_polling)
        await dp.start_polling(bot)
    else:
        logging.info("Starting bot in WEBHOOK mode...")
        app = await init_app(bot)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", PORT)
        await site.start()
        # Keep the loop running
        while True:
            await asyncio.sleep(3600)

async def on_startup_polling(bot: Bot):
    logging.info("Бот запускается (polling)...")
    await create_db()
    # Порядок важен: заливка может ВПЕРВЫЕ добыть file_id для error.png, и читать
    # баннер надо после неё, иначе картинка ошибки останется пустой до следующего
    # запуска.
    async with session_maker() as session:
        await load_banners_from_folder(bot, session)
        globals.error_pic = await orm_get_banner(session, "error")

    await start_rest_notifier(bot)
    with contextlib.suppress(Exception):
        await setup_menu_button(bot)

    # База проверена, воркер отдыха жив — только теперь реплика считается доступной.
    global _ready
    _ready = True

    # Молчим по умолчанию. Сообщение писалось на КАЖДЫЙ старт процесса, а в
    # кластере под перезапускается на каждую выкатку — за один день их бывает
    # четыре. «Бот запущен» при этом не отвечает ни на один вопрос, который
    # нельзя закрыть иначе: живость видит readinessProbe через /healthz,
    # а результат выкатки — лог CI. Кому нужно, включает переменной.
    if os.getenv("NOTIFY_ADMINS_ON_START", "False").lower() == "true":
        for user in bot.my_admins_list:
            with contextlib.suppress(Exception):
                await bot.send_message(user, "Бот запущен в режиме POLLING")


async def start_rest_notifier(bot: Bot):
    """
    Поднимает воркер отдыха и держит ссылку на таск.

    Ссылка нужна не для красоты: у asyncio на таски только слабые ссылки, и таск без
    единой сильной ссылки может быть собран сборщиком мусора прямо посреди отдыха.
    """
    task = asyncio.create_task(rest_notifier(bot, session_maker))
    bot.rest_notifier_task = task
    return task

if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt, SystemExit):
        asyncio.run(main())
