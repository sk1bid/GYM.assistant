"""
Напоминания в чат.

Второй воркер в процессе бота — рядом с `rest_notifier`, по той же причине и на
тех же правилах. Причина: уведомлять умеет только бот. Закрытая страница Mini App
не существует, её JS-таймер умирает вместе с ней, а Web Push внутри вебвью
Telegram нет вовсе.

Разделение обязанностей внутри самой системы напоминаний такое же строгое, как
между ботом и Mini App:

* **что и когда отправить** решает `services/notifications.py` — чистый модуль,
  на вход факты, на выход список сообщений. Там же и все тексты;
* **этот файл** только достаёт факты из базы, ходит в Telegram и убирает за собой.

Отсюда и тесты: решения проверяются без базы и без бота, а воркер отвечает лишь
за «застолбил → отправил → запомнил, что удалить».

**Порядок «сначала запись, потом отправка» переставлять нельзя.** Воркер
просыпается раз в минуту, а окно напоминания о тренировке — три часа. Право на
«один раз» держит уникальный индекс в БД (`uq_notification_once`), и застолбить
его надо ДО похода в Telegram: иначе любой сбой между отправкой и записью
превращается в повтор, а следующий тик — в ещё один.

**Убираем за собой по двум поводам сразу**, как и пинги отдыха: наступил срок
(`expires_at`) ИЛИ отпал повод (тренировка началась — гаснет напоминание о ней;
тренировку завершили — гаснет вопрос «ты ещё в зале?»).

Отличие от пингов отдыха ровно одно, и оно намеренное: здесь сообщение НЕ
пересылается заново каждую минуту. Пинг отдыха обязан быть новым сообщением,
потому что ради пуша и вибрации он и существует; напоминание же — единичное
событие, и второй звонок про то же самое был бы спамом.
"""
import asyncio
import logging
import os
import uuid
from datetime import datetime, time, timezone

from aiogram import Bot, F, Router, types
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from database.orm_extra import (
    orm_finish_training_session,
    orm_stop_rest_timer,
    utcnow,
)
from database.orm_notify import (
    orm_ambient_sent_since,
    orm_attach_message,
    orm_claim_notification,
    orm_clear_notification,
    orm_disable_notifications,
    orm_get_prefs,
    orm_has_training_since,
    orm_live_notifications,
    orm_open_sessions_of,
    orm_prune_notifications,
    orm_session_activity,
    orm_session_still_open,
    orm_users_to_notify,
)
from database.orm_query import orm_get_program, orm_get_training_session
from services.clock import now_in, resolve_tz
from services.notifications import (
    DAY,
    UNFINISHED,
    Active,
    DayPlan,
    Facts,
    Missed,
    Push,
    WeekProgress,
    plan,
    prefs_of,
)
from services.progress import (
    WEEK_DAYS_RU,
    day_of,
    missed_day,
    program_week,
    trained_dates,
    weekly_progress,
)
from services.workout import DEFAULT_CIRCULAR_ROUNDS, build_plan

MINIAPP_URL = os.getenv("MINIAPP_URL", "")

# Раз в минуту. Чаще незачем: самое точное здесь — окно напоминания о тренировке,
# и оно измеряется часами. Отдых, которому нужна секундная точность, ведёт
# отдельный воркер со своим тиком в пять секунд.
TICK_SECONDS = 60

router = Router()


# ---------------------------------------------------------------- кнопка «Завершить»


@router.callback_query(F.data.startswith("notify:finish:"))
async def finish_from_notification(callback: types.CallbackQuery, session):
    """
    «Завершить» под вопросом о брошенной тренировке.

    Владение проверяется здесь и сейчас: id тренировки уехал в callback_data,
    то есть на клиент, а всё, что побывало на клиенте, — недоверенный ввод. Ровно
    то же правило, что у Mini App (`ownership.py`).
    """
    training_id = _uuid(callback.data.split(":", 2)[2])

    training = await orm_get_training_session(session, training_id) if training_id else None
    if training is None or training.user_id != callback.from_user.id:
        await callback.answer("Тренировка не найдена", show_alert=True)
        return

    if training.finished_at is None:
        await orm_finish_training_session(session, training.id)
        # Отдых пережил бы тренировку и пинговал в пустоту — гасим его тем же жестом,
        # что и штатное «Завершить» в Mini App.
        await orm_stop_rest_timer(session, callback.from_user.id)

    await callback.answer("Тренировка завершена")

    # Вопрос отвечен — сообщению в чате больше нечего делать. Уборщик снял бы его
    # и сам на следующем тике (повод отпал), но ждать минуту после нажатия кнопки
    # незачем. Повторное удаление уборщиком безобидно: «message to delete not
    # found» он проглатывает.
    if callback.message:
        await _delete_quietly(callback.bot, callback.message.chat.id, callback.message.message_id)


# ---------------------------------------------------------------- воркер


async def notifier(bot: Bot, session_maker) -> None:
    """
    Вечный цикл: раз в минуту убирает отработавшее и разбирает пользователей.

    Одна упавшая отправка не должна ронять цикл — иначе один человек
    с заблокированным ботом лишит напоминаний всех остальных.
    """
    logging.info("воркер напоминаний запущен")

    while True:
        try:
            async with session_maker() as session:
                await _sweep(bot, session)

                for user in await orm_users_to_notify(session):
                    try:
                        await _handle_user(bot, session, user)
                    except TelegramForbiddenError:
                        # Бот заблокирован. Писать больше некуда, а пытаться каждую
                        # минуту — это лог из одинаковых ошибок и очередь запросов,
                        # из которой ничего не выйдет. Включит обратно сам человек.
                        logging.info("напоминания выключены: бот заблокирован user_id=%s", user.user_id)
                        await orm_disable_notifications(session, user.user_id)
                    except Exception:
                        logging.exception("напоминания user_id=%s: сбой", user.user_id)
        except Exception:
            logging.exception("воркер напоминаний: сбой итерации")

        await asyncio.sleep(TICK_SECONDS)


async def _handle_user(bot: Bot, session, user) -> None:
    for push in plan(await _facts(session, user)):
        await _send(bot, session, user, push)


async def _send(bot: Bot, session, user, push: Push) -> None:
    """Застолбить → отправить → запомнить, что удалять."""
    chat_id = user.user_id  # приватный чат с ботом: chat_id совпадает с user_id

    claim = await orm_claim_notification(session, user.user_id, push.kind, push.dedup, chat_id)
    if claim is None:
        return  # это уже слали

    sent = await bot.send_message(chat_id, push.text, reply_markup=_keyboard(push.action))
    await orm_attach_message(session, claim.id, sent.message_id, utcnow() + push.ttl)


def _keyboard(action: str | None) -> InlineKeyboardMarkup | None:
    """
    Кнопка под сообщением — та единственная вещь, ради которой его открывают.

    `app` не рисуется, если Mini App не настроен: кнопка, ведущая в никуда, хуже
    её отсутствия — само сообщение осмысленно и без неё.
    """
    if action == "app":
        if not MINIAPP_URL:
            return None
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Открыть тренировку", web_app=WebAppInfo(url=MINIAPP_URL)),
        ]])

    if action and action.startswith("finish:"):
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Завершить", callback_data=f"notify:{action}"),
        ]])

    return None


# ---------------------------------------------------------------- факты


async def _facts(session, user) -> Facts:
    """
    Всё, что нужно знать про пользователя, чтобы решить, писать ли ему.

    Считается в его поясе: «сегодня вторник» и «через три часа» — это про место
    человека, а сервер стоит в НСК и живёт в UTC. Пояс приезжает с телефона
    заголовком X-Timezone и оседает в `user.timezone`; не приезжал ни разу —
    работает DEFAULT_TZ, ровно как везде в приложении.
    """
    tz = resolve_tz(user.timezone)
    now = now_in(tz)
    today = now.date()

    week_days = await program_week(session, user.actual_program_id)
    trained = await trained_dates(session, user.user_id, tz)

    last = max(trained) if trained else None

    return Facts(
        now=now,
        prefs=prefs_of(await orm_get_prefs(session, user.user_id)),
        today=await _today_plan(session, user, week_days, today),
        trained_today=today in trained,
        active=await _active(session, user),
        week=WeekProgress(**await weekly_progress(session, user.user_id, tz, week_days, trained)),
        missed=_missed(await missed_day(session, user.user_id, tz, week_days)),
        days_off=(today - last).days if last else None,
        last_training=last,
        ambient_today=await orm_ambient_sent_since(session, user.user_id, _local_midnight(today, tz)),
    )


async def _today_plan(session, user, week_days, today) -> DayPlan | None:
    """
    Что стоит в программе на сегодня. None — день отдыха, и напоминать не о чем.

    Подходы считаются тем же `build_plan`, что разворачивает день на экране
    тренировки: у кругового блока подходов ровно столько, сколько кругов в
    программе, и «5 упражнений» без этого числа ничего не сказали бы о длине
    тренировки.
    """
    planned = day_of(week_days, WEEK_DAYS_RU[today.weekday()])
    if not planned or not planned.exercises:
        return None

    program = await orm_get_program(session, user.actual_program_id)
    rounds = (program.circular_rounds if program else None) or DEFAULT_CIRCULAR_ROUNDS

    return DayPlan(
        name=planned.name,
        exercises=len(planned.exercises),
        sets=len(build_plan(planned.exercises, rounds)),
    )


async def _active(session, user) -> Active | None:
    """
    Идущая тренировка и сколько она уже молчит.

    Точка отсчёта — последний записанный подход, а если его нет, начало тренировки.
    Именно подход, а не начало: человек, который час назад начал и всё это время
    работает, тренировку не бросал.
    """
    training = await orm_open_sessions_of(session, user.user_id)
    if training is None:
        return None

    sets, last_set = await orm_session_activity(session, training.id)
    since = last_set or training.date
    if since is None:
        return None

    return Active(
        session_id=str(training.id),
        idle=max(0, int((utcnow() - since).total_seconds() // 60)),
        sets=sets,
    )


def _missed(row: dict | None) -> Missed | None:
    if not row:
        return None
    return Missed(day_id=row["id"], name=row["day_of_week"], days_ago=row["days_ago"])


def _local_midnight(today, tz) -> datetime:
    """
    Полночь местного «сегодня» в naive-UTC — так лежат даты в базе.

    Нужна, чтобы спросить «тихое уведомление сегодня уже слали?». Сутки здесь
    обязаны быть сутками ПОЛЬЗОВАТЕЛЯ: в Новосибирске местная полночь — это
    17:00 UTC накануне, и по UTC-суткам ограничение «одно в день» сдвинулось бы
    на семь часов.
    """
    return datetime.combine(today, time.min, tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------- уборка


async def _sweep(bot: Bot, session) -> None:
    """
    Убирает из чата то, что отслужило.

    Два повода, и второй важнее первого: срок (`expires_at`) — это страховка на
    случай, когда повод так и не отпал, а обычный путь — именно отпавший повод.
    Напоминание «сегодня тренировка» живёт до конца суток, но исчезает в ту
    секунду, когда человек нажал «Начать»: висеть над уже идущей тренировкой ему
    незачем.
    """
    now = utcnow()

    for row in await orm_live_notifications(session):
        try:
            if not await _done_with(session, row, now):
                continue
            await _delete_quietly(bot, row.chat_id, row.message_id)
            await orm_clear_notification(session, row.id)
        except Exception:
            logging.exception("уборка напоминания id=%s: сбой", row.id)

    await orm_prune_notifications(session)


async def _done_with(session, row, now: datetime) -> bool:
    if row.expires_at and row.expires_at <= now:
        return True

    if row.kind == DAY:
        return await orm_has_training_since(session, row.user_id, row.sent_at)

    if row.kind == UNFINISHED:
        # dedup вопроса о брошенной тренировке — её же id.
        training_id = _uuid(row.dedup)
        return training_id is None or not await orm_session_still_open(session, training_id)

    return False


def _uuid(value: str) -> uuid.UUID | None:
    """
    Строка → UUID. Колонка объявлена UUID, и на SQLite строка просто не совпадёт
    ни с чем: там значение лежит шестнадцатеричными символами без дефисов.
    На Postgres сравнение со строкой прошло бы — поэтому разойтись эти два случая
    могли бы очень надолго.
    """
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError):
        return None


async def _delete_quietly(bot: Bot, chat_id: int, message_id: int | None) -> None:
    """Сообщение мог удалить и сам пользователь — это не ошибка."""
    if not message_id:
        return
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except TelegramBadRequest as e:
        if "message to delete not found" not in str(e):
            logging.warning("не удалось удалить напоминание %s: %s", message_id, e)
    except TelegramForbiddenError:
        pass  # бота заблокировали — чистить в этом чате уже нечего
