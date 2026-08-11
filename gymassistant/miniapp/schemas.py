"""
Что приходит от фронта.

Границы здесь не косметические: вес 10^9 кг или ноль повторений навсегда испортят
и графики, а починить это потом можно только руками в SQL.
"""
from pydantic import BaseModel, Field

from miniapp.config import MAX_PROGRAM_NAME, MAX_USER_NAME


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


class ExercisePatchIn(BaseModel):
    sets: int | None = Field(default=None, ge=1, le=20)
    reps: int | None = Field(default=None, ge=1, le=100)
    circle_training: bool | None = None


class UserExerciseIn(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    description: str = Field(default="", max_length=1000)
    category_id: int


class ProfileIn(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_USER_NAME)
    weight: float = Field(gt=0, le=500)
