"""Напоминания: настройки, журнал отправленного и пояс пользователя

Revision ID: a7b9c1d3e5f7
Revises: e5f7a9b1c3d4
Create Date: 2026-08-23

Три изменения, и все три — под воркер `workers/notifier.py`.

**`user.timezone`.** До сих пор пояс приезжал заголовком `X-Timezone` на каждом
запросе Mini App и нигде не оседал. Воркеру он нужен без запроса клиента: «сегодня
вторник» и «через три часа» считаются по месту человека, а сервер стоит в НСК и
живёт в UTC. NULL значит «телефон ещё не сообщил» — тогда работает DEFAULT_TZ
из services/clock.py, ровно как и раньше.

**`notification_prefs`.** Строка на пользователя, заводится лениво при первом
заходе в настройки. server_default колонок обязан совпадать с `Prefs` в
services/notifications.py: до первого захода настройки читаются оттуда, и
разойдись эти два места — «я ничего не менял» означало бы разное поведение.

**`notification`.** Журнал: что слали (чтобы не повторяться) и что ещё висит
в чате (чтобы убрать). Уникальный ключ (user_id, kind, dedup) — не оптимизация,
а единственное, что удерживает окно напоминания шириной в три часа от
превращения в 180 одинаковых сообщений: воркер просыпается раз в минуту, а его
память умирает вместе с подом.

Обе таблицы создаются пустыми: до первой отправки писать в них нечего.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a7b9c1d3e5f7'
down_revision: Union[str, None] = 'e5f7a9b1c3d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('user', sa.Column('timezone', sa.String(length=64), nullable=True))

    op.create_table(
        'notification_prefs',
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        # Минуты от полуночи в поясе пользователя: 600 — это 10:00.
        sa.Column('train_at_minutes', sa.Integer(), nullable=False, server_default='600'),
        sa.Column('lead_minutes', sa.Integer(), nullable=False, server_default='180'),
        sa.Column('day_reminder', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('unfinished', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('weekly', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('missed', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created', sa.DateTime(), nullable=True),
        sa.Column('updated', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['user.user_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('user_id'),
    )

    op.create_table(
        'notification',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('kind', sa.String(length=24), nullable=False),
        sa.Column('dedup', sa.String(length=48), nullable=False),
        sa.Column('chat_id', sa.BigInteger(), nullable=False),
        # NULL — сообщение уже убрано из чата (или его не удалось отправить);
        # строка при этом остаётся отметкой «такое уже слали».
        sa.Column('message_id', sa.Integer(), nullable=True),
        sa.Column('sent_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('created', sa.DateTime(), nullable=True),
        sa.Column('updated', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['user.user_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'kind', 'dedup', name='uq_notification_once'),
    )
    op.create_index('idx_notification_live', 'notification', ['message_id'])


def downgrade() -> None:
    op.drop_index('idx_notification_live', table_name='notification')
    op.drop_table('notification')
    op.drop_table('notification_prefs')
    op.drop_column('user', 'timezone')
