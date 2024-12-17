"""Remove image from exercise

Revision ID: 78b1121a51ad
Revises: f42919bf42b1
Create Date: 2024-12-09 11:04:21.489539

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '78b1121a51ad'
down_revision: Union[str, None] = 'f42919bf42b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('exercise', 'image')
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('exercise', sa.Column('image', sa.VARCHAR(length=150), autoincrement=False, nullable=False))
    # ### end Alembic commands ###