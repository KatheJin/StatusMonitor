"""Add isolated remediation audit tables."""
from alembic import op
import sqlalchemy as sa
revision = 'a01_remediation'
down_revision = '386bf61684e4'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('remediation_runs',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('state', sa.String(32), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('baseline_verified', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_table('remediation_events',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('run_id', sa.String(36), sa.ForeignKey('remediation_runs.id'), nullable=False),
        sa.Column('kind', sa.String(40), nullable=False),
        sa.Column('evidence', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index('ix_remediation_events_run_id', 'remediation_events', ['run_id'])


def downgrade():
    op.drop_table('remediation_events')
    op.drop_table('remediation_runs')
