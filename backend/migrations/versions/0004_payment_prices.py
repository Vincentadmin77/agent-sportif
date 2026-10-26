"""payment prices replace shopify skus

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-26 12:00:00

Shopify is dropped: plans are sold directly with Stripe and PayPal. Each plan
now maps provider price ids (Stripe price, PayPal plan) to a billing period in days.
"""
from alembic import op
import sqlalchemy as sa


revision = '0004'
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('plans', schema=None) as batch_op:
        batch_op.add_column(sa.Column('payment_prices', sa.JSON(), nullable=False, server_default='{}'))
        batch_op.drop_column('shopify_skus')


def downgrade() -> None:
    with op.batch_alter_table('plans', schema=None) as batch_op:
        batch_op.add_column(sa.Column('shopify_skus', sa.JSON(), nullable=False, server_default='[]'))
        batch_op.drop_column('payment_prices')
