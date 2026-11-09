"""Persist check provenance and formal incidents; retain legacy prototype tables."""
from alembic import op
import sqlalchemy as sa

revision = 'a02_incidents'
down_revision = 'a01_remediation'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('monitors', sa.Column('config_revision', sa.Integer(), nullable=False, server_default='1'))
    # Old rows are explicitly ineligible; never guess that legacy checks were automatic.
    op.add_column('check_results', sa.Column('source', sa.String(16), nullable=False, server_default='legacy'))
    op.add_column('check_results', sa.Column('config_revision', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('check_results', sa.Column('check_url', sa.Text(), nullable=True))
    op.create_index('ix_check_results_monitor_id_id', 'check_results', ['monitor_id', 'id'])
    op.create_table('managed_services',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('monitor_id', sa.Integer(), sa.ForeignKey('monitors.id'), nullable=False, unique=True),
        sa.Column('project', sa.String(100), nullable=False),
        sa.Column('service', sa.String(100), nullable=False),
        sa.Column('restart_authorized', sa.Boolean(), nullable=False),
        sa.Column('policy_version', sa.Integer(), nullable=False),
        sa.Column('observed_state', sa.String(20), nullable=False),
        sa.Column('observed_at', sa.DateTime(timezone=True)),
        sa.Column('next_decision_at', sa.DateTime(timezone=True)),
        sa.UniqueConstraint('project', 'service'))
    op.create_table('detector_states',
        sa.Column('monitor_id', sa.Integer(), sa.ForeignKey('monitors.id'), primary_key=True),
        sa.Column('config_revision', sa.Integer(), nullable=False),
        sa.Column('last_check_id', sa.Integer(), nullable=False),
        sa.Column('failure_ids', sa.JSON(), nullable=False),
        sa.Column('success_count', sa.Integer(), nullable=False))
    op.create_table('incidents',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('monitor_id', sa.Integer(), sa.ForeignKey('monitors.id'), nullable=False),
        sa.Column('managed_service_id', sa.Integer(), sa.ForeignKey('managed_services.id')),
        sa.Column('config_revision', sa.Integer(), nullable=False),
        sa.Column('state', sa.String(20), nullable=False),
        sa.Column('trigger_check_ids', sa.JSON(), nullable=False),
        sa.Column('opened_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('closed_at', sa.DateTime(timezone=True)))
    op.create_index('uq_incident_active_monitor', 'incidents', ['monitor_id'], unique=True,
        postgresql_where=sa.text("state IN ('open', 'recovering')"),
        sqlite_where=sa.text("state IN ('open', 'recovering')"))
    op.create_table('recovery_decisions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('incident_id', sa.Integer(), sa.ForeignKey('incidents.id'), nullable=False, unique=True),
        sa.Column('outcome', sa.String(20), nullable=False),
        sa.Column('reason', sa.String(40), nullable=False),
        sa.Column('policy_snapshot', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_table('incident_events',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('incident_id', sa.Integer(), sa.ForeignKey('incidents.id'), nullable=False),
        sa.Column('check_id', sa.Integer(), sa.ForeignKey('check_results.id')),
        sa.Column('kind', sa.String(30), nullable=False),
        sa.Column('evidence', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_incident_events_incident_id', 'incident_events', ['incident_id'])


def downgrade():
    for table in ('incident_events', 'recovery_decisions', 'incidents', 'detector_states', 'managed_services'):
        op.drop_table(table)
    op.drop_index('ix_check_results_monitor_id_id', table_name='check_results')
    for column in ('check_url', 'config_revision', 'source'):
        op.drop_column('check_results', column)
    op.drop_column('monitors', 'config_revision')
