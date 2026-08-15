"""Метрики Apple Health рядом с тренировками

Revision ID: e5f7a9b1c3d4
Revises: d4e6f8a0b2c3
Create Date: 2026-08-15

Здоровье приезжает с телефона в отдельный стек (hae-server + MongoDB), тренировки
лежат здесь. Смотреть их вместе было неоткуда: Grafana к MongoDB нативно не ходит,
а join между двумя базами не сделать вовсе. Метрики переносятся к тренировкам, а
не наоборот, — они плоский временной ряд, тогда как связи session → set → exercise
в документную базу не переехали бы.

Формат длинный: строка — одно число одной метрики в один момент. У документа пульса
таких чисел три (min/avg/max), у сна четыре, у шагов одно; см. services/health_sync.py.
Одной колонкой `value` три разные формы иначе не описать, а заводить таблицу на
каждую — плодить схему ради того, что рисуется одним запросом.

Уникальный ключ — суть всей затеи. Health Auto Export шлёт перекрывающиеся окна,
и один день приезжает при каждой синхронизации; ключ превращает повторную
присылку в апсерт. `source` в нём обязателен: шаги за 19 июня пришли дважды —
3270 с телефона и 5200 с часов, и без источника они затирали бы друг друга.

Таблица создаётся пустой: историю (с 19 июня 2026) заливает первый прогон
scripts/sync_health.py, он же идемпотентен.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e5f7a9b1c3d4'
down_revision: Union[str, None] = 'd4e6f8a0b2c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'health_metric',
        sa.Column(
            'id',
            sa.BigInteger().with_variant(sa.Integer, "sqlite"),
            autoincrement=True, nullable=False,
        ),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('name', sa.String(length=64), nullable=False),
        sa.Column('field', sa.String(length=32), nullable=False),
        sa.Column('measured_at', sa.DateTime(), nullable=False),
        sa.Column('value', sa.Float(), nullable=False),
        sa.Column('units', sa.String(length=32), nullable=True),
        # Не nullable: колонка в ключе уникальности, а NULL в ключе не склеивается
        # сам с собой — документ без устройства дублировался бы при каждой
        # синхронизации. Неизвестный источник — пустая строка.
        sa.Column('source', sa.String(length=64), nullable=False, server_default=''),
        sa.Column('created', sa.DateTime(), nullable=True),
        sa.Column('updated', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['user.user_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'user_id', 'name', 'field', 'measured_at', 'source',
            name='uq_health_metric',
        ),
    )
    op.create_index(
        'idx_health_metric_lookup', 'health_metric',
        ['user_id', 'name', 'measured_at'],
    )


def downgrade() -> None:
    op.drop_index('idx_health_metric_lookup', table_name='health_metric')
    op.drop_table('health_metric')
