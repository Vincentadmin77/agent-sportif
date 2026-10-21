"""carnet: propositions subscribers put in their notebook

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-21 15:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0004'
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'carnet_entries',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('item_id', sa.String(length=160), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('label', sa.String(length=255), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'item_id'),
    )
    op.create_index('ix_carnet_entries_user_id', 'carnet_entries', ['user_id'])
    op.create_index('ix_carnet_entries_item_id', 'carnet_entries', ['item_id'])


def downgrade() -> None:
    op.drop_index('ix_carnet_entries_item_id', table_name='carnet_entries')
    op.drop_index('ix_carnet_entries_user_id', table_name='carnet_entries')
    op.drop_table('carnet_entries')
