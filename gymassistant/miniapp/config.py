"""Настройки Mini App. Всё, что берётся из окружения, — только здесь."""
import os
from pathlib import Path

# Дни недели по-русски реэкспортируются: сам список живёт в services/progress.py,
# потому что о них спрашивает и воркер напоминаний в процессе бота, — а импорт
# ЭТОГО модуля уронил бы бота на старте, здесь требуется токен Mini App.
from services.progress import WEEK_DAYS_RU  # noqa: F401

# Токен нужен не для походов в Telegram (туда этот сервис не ходит вообще),
# а чтобы проверять подпись initData. См. auth.py.
BOT_TOKEN = os.environ["MINIAPP_BOT_TOKEN"]

# Сколько живёт подписанный initData. Сутки — компромисс: тренировка длится час-два,
# но человек может открыть приложение, не перезапуская клиент Telegram.
MAX_AUTH_AGE = int(os.getenv("MINIAPP_MAX_AUTH_AGE", "86400"))

STATIC_DIR = Path(__file__).parent / "static"

# Ограничения ввода — дублируют то, что валидируют схемы, но нужны и в других местах.
MAX_PROGRAM_NAME = 50
MAX_USER_NAME = 20
