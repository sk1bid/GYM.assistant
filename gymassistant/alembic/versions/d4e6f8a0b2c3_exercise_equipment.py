"""Снаряд упражнения: штанга, гантели, блок, свой вес

Revision ID: d4e6f8a0b2c3
Revises: c3d5e7f9a1b2
Create Date: 2026-08-11

Шаг веса на экране подхода был константой 2.5 на всё подряд: на блоке вес так
не набирается вовсе, а на махах гантелями 8 кг это прибавка в треть.
Шаг задаёт снаряд, поэтому колонкой стал снаряд, а шаг считается из него
(services/equipment.py).

Существующие строки получают 'other' — шаг 2.5, то есть ровно сегодняшнее
поведение. Правильные значения проставляет seed_catalog при старте: каталог
заполняется кодом и живую базу иначе не догнать (та же история, что была
с переименованием категории «Трап.»).

У exercise колонка nullable и БЕЗ server_default намеренно: NULL здесь читается
как «снимок с каталога ещё не снят», и по нему seed_catalog находит, что досыпать.
С дефолтом 'other' эти строки стали бы неотличимы от честно неизвестных.

Рядом `weight_step` — поправка на конкретный зал: шаг кнопок, а у блока — вес
одного блока (бывает 4.5, 5, 5.5, 7). NULL значит «как у снаряда»: правду знает
только тот, кто в этом зале стоит, а угадывать за него дороже, чем дать поправить.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd4e6f8a0b2c3'
down_revision: Union[str, None] = 'c3d5e7f9a1b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for table in ('admin_exercises', 'user_exercises'):
        op.add_column(
            table,
            sa.Column('equipment', sa.String(16), nullable=False, server_default='other'),
        )

    op.add_column('exercise', sa.Column('equipment', sa.String(16), nullable=True))
    op.add_column('exercise', sa.Column('weight_step', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('exercise', 'weight_step')
    op.drop_column('exercise', 'equipment')
    op.drop_column('user_exercises', 'equipment')
    op.drop_column('admin_exercises', 'equipment')
