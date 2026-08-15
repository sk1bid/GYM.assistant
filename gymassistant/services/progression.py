"""
Что предложить на следующей тренировке: повысить, держать или сбросить.

Никакой нейросети здесь нет и не нужно. Прогрессия — не задача предсказания,
а решение по правилам, и правила эти старше приложений: **двойная прогрессия**
(растим повторения внутри плана, добрал верх — растим вес) плюс страховка
**2-for-2** от NSCA (повышаем только если план закрыт ДВЕ тренировки подряд,
а не один удачный день).

Что здесь намеренно НЕ делается:

* Не спрашивается, тяжело ли далось (RPE/RIR). Такого поля в схеме нет, и пока
  его нет, «12 повторений» одинаково выглядят и когда мог ещё пять, и когда умер.
  Это главный недостающий вход, и он же — следующий шаг.
* Не выдумывается вес, которого нет в зале. Прибавка округляется вверх до сетки
  снаряда (services/equipment.py), поэтому на блоке предложение — это ровно
  «+1 блок», а не «+0.9 кг».
* Ничего не предлагается, пока упражнение делали один раз: 2-for-2 требует двух
  тренировок, и подменять его одной — значит предлагать по случайности.

Модуль чистый: ни FastAPI, ни ORM. План + два прошлых раза → предложение.
"""
import math
from dataclasses import dataclass
from typing import Sequence

# Насколько повышать: доля от рабочего веса, округлённая ВВЕРХ до сетки снаряда.
#
# Проценты, а не фиксированная прибавка, потому что +2.5 кг — это 2 % приседа
# и 30 % махов гантелями. Верхняя граница диапазона NSCA (2–10 %) не берётся:
# промахнуться вверх дороже, чем недобрать, — недобор стоит одной лишней недели,
# а перебор роняет подход и запускает деload.
PROGRESS_PCT = 0.025

# Насколько сбрасывать после двух проваленных тренировок. Классические 10 %.
DELOAD_PCT = 0.10

# На сколько повторений можно недобрать, чтобы это ещё не считалось провалом.
# 9 из 10 — обычный день, 7 из 10 — сигнал. Число выбрано симметрично 2-for-2:
# два повторения сверху — повод повысить, два снизу — повод насторожиться.
SHORTFALL = 2

UP = "up"
HOLD = "hold"
DOWN = "down"


@dataclass
class Suggestion:
    """Что подставить в поле веса и почему."""
    action: str
    weight: float


def round_to_step(value: float, step: float) -> float:
    """Ближайший достижимый на снаряде вес."""
    if step <= 0:
        return round(value, 2)
    return round(round(value / step) * step, 2)


def progress_step(working: float, step: float) -> float:
    """Прибавка: процент от рабочего веса, но не мельче шага снаряда."""
    if step <= 0:
        return 0.0
    return max(step, math.ceil(working * PROGRESS_PCT / step) * step)


def _cleared(sets: Sequence, target_sets: int, target_reps: int) -> bool:
    """
    План закрыт полностью.

    Пропущенных подходов здесь не видно — их отфильтровал слой данных (`_lifted`),
    им в «прошлый раз» нельзя. Поэтому пропуск ловится по КОЛИЧЕСТВУ: подходов
    пришло меньше, чем планировалось, значит план не закрыт.
    """
    return len(sets) >= target_sets and all(s.repetitions >= target_reps for s in sets)


def _failed(sets: Sequence, target_sets: int, target_reps: int) -> bool:
    """Провал: подходов меньше плана или где-то заметный недобор."""
    if len(sets) < target_sets:
        return True
    return any(s.repetitions < target_reps - SHORTFALL for s in sets)


def suggest(
    target_sets: int,
    target_reps: int,
    step: float,
    previous: Sequence,
    before_previous: Sequence = (),
) -> Suggestion | None:
    """
    Предложение на ближайший подход.

    `previous` / `before_previous` — подходы этого упражнения из двух последних
    тренировок, где оно делалось (без пропущенных). Порядок между ними значения
    не имеет, важно только, закрыт ли в каждой план.

    None — предлагать нечего: упражнение новое, или все прошлые подходы без веса
    (свой вес без пояса — там растят повторения, а не килограммы, и план это уже
    и есть).
    """
    lifted = [s for s in previous if s.weight > 0]
    if not lifted:
        return None

    # Рабочий вес — максимальный из прошлого раза. Разминочных подходов у нас
    # не бывает: в плане только рабочие, и меньший вес означает, что подход
    # не дожали, а не что он лёгкий.
    working = max(s.weight for s in lifted)

    done_now = _cleared(previous, target_sets, target_reps)
    done_before = _cleared(before_previous, target_sets, target_reps)

    if done_now and done_before:
        return Suggestion(UP, round_to_step(working + progress_step(working, step), step))

    if done_now:
        # План закрыт впервые. По 2-for-2 это ещё не повод повышать — подтверди.
        return Suggestion(HOLD, working)

    if _failed(previous, target_sets, target_reps) and _failed(before_previous, target_sets, target_reps):
        return Suggestion(DOWN, _deload(working, step))

    return Suggestion(HOLD, working)


def _deload(working: float, step: float) -> float:
    """
    Сброс на 10 %, но обязательно НИЖЕ рабочего.

    Округление к сетке снаряда может вернуть тот же вес (на 20 кг со ступенью 5
    сброс даёт 18 → округляется обратно в 20), и тогда «понизить» не понизило бы
    ничего, а человек второй раз пошёл бы на тот же провал.
    """
    target = round_to_step(working * (1 - DELOAD_PCT), step)
    if target >= working:
        target = round(working - step, 2)
    return max(target, step if step > 0 else 0.0)
