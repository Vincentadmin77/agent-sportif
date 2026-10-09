"""Database-level guard: a published selection's price and probabilities cannot
change, and a published selection cannot be deleted (PostgreSQL only).

Revision ID: 0002
Revises: 0001
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("""
    CREATE OR REPLACE FUNCTION guard_published_selection() RETURNS trigger AS $$
    BEGIN
      IF TG_OP = 'DELETE' THEN
        IF OLD.published_at IS NOT NULL THEN
          RAISE EXCEPTION 'published selection % cannot be deleted', OLD.id;
        END IF;
        RETURN OLD;
      END IF;
      IF OLD.published_at IS NOT NULL AND (
           NEW.odds IS DISTINCT FROM OLD.odds OR NEW.bookmaker IS DISTINCT FROM OLD.bookmaker
        OR NEW.odds_observed_at IS DISTINCT FROM OLD.odds_observed_at OR NEW.p_win IS DISTINCT FROM OLD.p_win
        OR NEW.fair_odds IS DISTINCT FROM OLD.fair_odds OR NEW.ev IS DISTINCT FROM OLD.ev
        OR NEW.market IS DISTINCT FROM OLD.market OR NEW.selection IS DISTINCT FROM OLD.selection
        OR NEW.line IS DISTINCT FROM OLD.line OR NEW.published_at IS DISTINCT FROM OLD.published_at) THEN
        RAISE EXCEPTION 'published selection % is immutable (price, probabilities, market)', OLD.id;
      END IF;
      RETURN NEW;
    END $$ LANGUAGE plpgsql;
    """)
    op.execute("""
    CREATE TRIGGER selections_immutable BEFORE UPDATE OR DELETE ON selections
    FOR EACH ROW EXECUTE FUNCTION guard_published_selection();
    """)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP TRIGGER IF EXISTS selections_immutable ON selections")
    op.execute("DROP FUNCTION IF EXISTS guard_published_selection()")
