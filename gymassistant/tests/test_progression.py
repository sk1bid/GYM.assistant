"""
Подсказка по весу: повысить, держать, понизить.

Модуль чистый (план + два прошлых раза → предложение), поэтому и тесты чистые:
ни базы, ни HTTP. Сквозной путь до экрана проверяет test_miniapp_api.
"""
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.progression import (  # noqa: E402
    DOWN, HOLD, UP, progress_step, round_to_step, suggest,
)


@dataclass
class FakeSet:
    """Ровно то, что подсказка читает у подхода."""
    weight: float
    repetitions: int


def sets(weight: float, *reps: int) -> list[FakeSet]:
    return [FakeSet(weight, r) for r in reps]


def test_nothing_to_suggest_without_history():
    """Упражнение делается впервые — предлагать не из чего."""
    assert suggest(3, 10, 2.5, []) is None


def test_first_clean_session_only_holds():
    """
    План закрыт впервые — это ещё не повод повышать.

    Правило 2-for-2: два удачных раза подряд, а не один удачный день. Иначе вес
    рос бы после каждой тренировки, на которой человек просто выспался.
    """
    hint = suggest(3, 10, 2.5, sets(60, 10, 10, 10))
    assert (hint.action, hint.weight) == (HOLD, 60)


def test_two_clean_sessions_raise_the_weight():
    """Закрыл дважды — повышаем, и ровно на шаг снаряда."""
    hint = suggest(3, 10, 2.5, sets(60, 10, 10, 10), sets(60, 10, 11, 10))
    assert (hint.action, hint.weight) == (UP, 62.5)


def test_the_raise_grows_with_the_working_weight():
    """
    Прибавка — процент от рабочего веса, а не константа.

    +2.5 кг это 2 % приседа и треть маха гантелями: одно число не может быть
    верным для обоих. Округление всегда вверх до сетки снаряда, иначе предложение
    было бы весом, которого в зале нет.
    """
    light = suggest(3, 10, 2.5, sets(40, 10, 10, 10), sets(40, 10, 10, 10))
    heavy = suggest(3, 10, 2.5, sets(200, 10, 10, 10), sets(200, 10, 10, 10))

    assert light.weight == 42.5      # 2.5% от 40 = 1 кг → округляем вверх до шага
    assert heavy.weight == 205.0     # 2.5% от 200 = 5 кг → два шага


def test_a_missing_set_is_not_a_closed_plan():
    """
    Пропущенный подход не виден в «прошлом разе» — слой данных его отфильтровал.

    Поэтому пропуск ловится по количеству: пришло два подхода вместо трёх, значит
    план не закрыт, и повышать нечего. Без этой проверки уход с упражнения
    («сегодня не идёт») читался бы как безупречная тренировка.
    """
    hint = suggest(3, 10, 2.5, sets(60, 10, 10), sets(60, 10, 10, 10))
    assert hint.action == HOLD


def test_two_failed_sessions_deload():
    """Два раза подряд заметно недобрал — сбрасываем на 10 % к сетке снаряда."""
    hint = suggest(3, 10, 2.5, sets(100, 7, 6, 5), sets(100, 8, 7, 6))
    assert (hint.action, hint.weight) == (DOWN, 90.0)


def test_a_small_shortfall_is_not_a_failure():
    """
    9 из 10 — обычная тренировка, а не сигнал сбрасывать.

    Иначе деload срабатывал бы на нормальном разбросе, и вес полз бы вниз у того,
    кто просто подошёл к верху диапазона.
    """
    hint = suggest(3, 10, 2.5, sets(60, 9, 9, 9), sets(60, 9, 10, 9))
    assert hint.action == HOLD
    assert hint.weight == 60


def test_the_deload_always_goes_below_the_working_weight():
    """
    Округление к сетке может вернуть тот же вес — и «понизить» не понизит ничего.

    На 20 кг со ступенью 5 сброс даёт 18, а это округляется обратно в 20, и человек
    второй раз пошёл бы на тот же провал.
    """
    hint = suggest(3, 10, 5, sets(20, 5, 4, 4), sets(20, 6, 5, 4))
    assert hint.action == DOWN
    assert hint.weight < 20


def test_bodyweight_without_a_belt_has_nothing_to_suggest():
    """Подтягивания без пояса растят повторениями — вес предлагать не из чего."""
    assert suggest(3, 10, 2.5, sets(0, 10, 10, 10), sets(0, 10, 10, 10)) is None


def test_the_block_moves_by_whole_blocks():
    """
    На блоке шаг снаряда — вес одного блока, поэтому подсказка не может
    предложить полблока: округление вверх к сетке даёт ровно «+1 блок».
    """
    hint = suggest(3, 12, 5.0, sets(35, 12, 12, 12), sets(35, 12, 12, 12))
    assert (hint.action, hint.weight) == (UP, 40.0)


def test_rounding_helpers():
    assert round_to_step(41.3, 2.5) == 42.5
    assert round_to_step(54.0, 5) == 55.0
    assert progress_step(35, 5) == 5        # блок: минимум один блок
    assert progress_step(150, 2.5) == 5.0   # 2.5% = 3.75 → два шага штанги
