"""
Что приходит от фронта.

Границы здесь не косметические: вес 10^9 кг или ноль повторений навсегда испортят
и графики, а починить это потом можно только руками в SQL.
"""
from pydantic import BaseModel, Field, field_validator

from miniapp.config import MAX_PROGRAM_NAME, MAX_USER_NAME
from services.equipment import EQUIPMENT, OTHER


class StartTrainingIn(BaseModel):
    training_day_id: int


class SetIn(BaseModel):
    session_id: str
    exercise_id: int
    weight: float = Field(ge=0, le=1000)
    reps: int = Field(ge=1, le=1000)


class SkipIn(BaseModel):
    session_id: str
    exercise_id: int
    # true — списать разом все оставшиеся подходы упражнения («не идёт сегодня»),
    # false — только текущий («этот не смог, следующий попробую»).
    whole_exercise: bool = False


class SetEditIn(BaseModel):
    weight: float = Field(ge=0, le=1000)
    reps: int = Field(ge=1, le=1000)


class FinishTrainingIn(BaseModel):
    session_id: str


class RestIn(BaseModel):
    seconds: int = Field(ge=5, le=3600)
    next_up: str | None = None


class ProgramIn(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_PROGRAM_NAME)
    # id готовой программы из program_templates.py; None — собираем с нуля.
    template: str | None = Field(default=None, max_length=32)


class ProgramPatchIn(BaseModel):
    """Всё опционально: с фронта прилетает только то, что реально поменяли."""
    name: str | None = Field(default=None, min_length=1, max_length=MAX_PROGRAM_NAME)
    rest_between_set: int | None = Field(default=None, ge=0, le=3600)
    rest_between_exercise: int | None = Field(default=None, ge=0, le=3600)
    circular_rounds: int | None = Field(default=None, ge=1, le=20)
    circular_rest_between_rounds: int | None = Field(default=None, ge=0, le=3600)
    circular_rest_between_exercise: int | None = Field(default=None, ge=0, le=3600)


class DayExerciseIn(BaseModel):
    """Ровно одна ссылка на каталог — это же требует CHECK-констрейнт в БД."""
    admin_exercise_id: int | None = None
    user_exercise_id: int | None = None
    circle_training: bool = False


class DayExercisesIn(BaseModel):
    """
    Пачка упражнений в день. Потолок стоит на длине списка, а не на числе запросов:
    добавление идёт последовательно ради порядка, и сотня строк держала бы соединение.
    """
    items: list[DayExerciseIn] = Field(min_length=1, max_length=30)


class OrderIn(BaseModel):
    """Порядок упражнений дня целиком — все id ровно по разу, проверяет ORM."""
    ids: list[int] = Field(min_length=1, max_length=60)


class ExercisePatchIn(BaseModel):
    sets: int | None = Field(default=None, ge=1, le=20)
    reps: int | None = Field(default=None, ge=1, le=100)
    circle_training: bool | None = None
    # Шаг кнопок веса в этом зале. Ноль — «как у снаряда»: None здесь уже занят
    # значением «поле не прислали», а сбросить переопределение чем-то надо.
    weight_step: float | None = Field(default=None, ge=0, le=50)


class UserExerciseIn(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    description: str = Field(default="", max_length=1000)
    category_id: int
    # Снаряд решает, каким шагом ходят кнопки веса и нужен ли вес вообще.
    # Не прислали — 'other', то есть шаг штанги: не знаем — не выдумываем.
    equipment: str = Field(default=OTHER, max_length=16)

    @field_validator("equipment")
    @classmethod
    def known_equipment(cls, value: str) -> str:
        if value not in EQUIPMENT:
            raise ValueError(f"неизвестный снаряд: {value}")
        return value


class ProfileIn(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_USER_NAME)
    weight: float = Field(gt=0, le=500)


class NotificationsIn(BaseModel):
    """
    Настройки напоминаний. Всё опционально: с экрана прилетает только тронутое.

    Время — строкой «18:30», как его отдаёт `input type="time"`; в минуты его
    переводит `minutes()`. Разбор здесь, а не в роутере, потому что здесь же
    стоит и проверка: «25:70» обязана дать 422, а не тихо превратиться в мусор
    в колонке, по которому потом раз в минуту считается окно напоминания.
    """
    enabled: bool | None = None
    train_at: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    # До двенадцати часов. Ноль — «в самое время тренировки», тоже осмысленно.
    lead_minutes: int | None = Field(default=None, ge=0, le=720)
    day_reminder: bool | None = None
    unfinished: bool | None = None
    weekly: bool | None = None
    missed: bool | None = None

    def minutes(self) -> int | None:
        """«18:30» → 1110. Формат уже проверен схемой, разбор безопасен."""
        if self.train_at is None:
            return None
        hours, minutes = self.train_at.split(":")
        return int(hours) * 60 + int(minutes)
