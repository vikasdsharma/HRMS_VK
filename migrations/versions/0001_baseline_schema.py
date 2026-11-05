"""baseline schema

Revision ID: 0001
Revises: 
Create Date: 2026-11-04 19:13:05.444533

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0001'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Baseline = the schema before Alembic was introduced.

    Safe on every kind of database: a brand-new one gets all tables; a database created by the
    older hand-rolled upgrade code (like a live single-machine install) only gets what it lacks.
    """
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = set(insp.get_table_names())
    if 'organizations' not in existing:
        op.create_table('organizations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('address', sa.String(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name')
        )

    if 'attendance_uploads' not in existing:
        op.create_table('attendance_uploads',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('org_id', sa.Integer(), nullable=False),
        sa.Column('month', sa.String(), nullable=False),
        sa.Column('filename', sa.String(), nullable=True),
        sa.Column('uploaded_by', sa.String(), nullable=True),
        sa.Column('uploaded_at', sa.DateTime(), nullable=True),
        sa.Column('rows_saved', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(), nullable=True),
        sa.Column('details', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
        with op.batch_alter_table('attendance_uploads', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_attendance_uploads_org_id'), ['org_id'], unique=False)

    if 'categories' not in existing:
        op.create_table('categories',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('org_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('description', sa.String(), nullable=True),
        sa.Column('basic_day', sa.Float(), nullable=True),
        sa.Column('da_day', sa.Float(), nullable=True),
        sa.Column('hra_day', sa.Float(), nullable=True),
        sa.Column('other_day', sa.Float(), nullable=True),
        sa.Column('ot_multiplier', sa.Float(), nullable=True),
        sa.Column('pf_applicable', sa.Boolean(), nullable=True),
        sa.Column('esi_applicable', sa.Boolean(), nullable=True),
        sa.Column('pf_ceiling', sa.Boolean(), nullable=True),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('org_id', 'name')
        )
        with op.batch_alter_table('categories', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_categories_org_id'), ['org_id'], unique=False)

    if 'employee_uploads' not in existing:
        op.create_table('employee_uploads',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('org_id', sa.Integer(), nullable=False),
        sa.Column('filename', sa.String(), nullable=True),
        sa.Column('uploaded_by', sa.String(), nullable=True),
        sa.Column('uploaded_at', sa.DateTime(), nullable=True),
        sa.Column('created', sa.Integer(), nullable=True),
        sa.Column('updated', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(), nullable=True),
        sa.Column('details', sa.Text(), nullable=True),
        sa.Column('file_data', sa.LargeBinary(), nullable=True),
        sa.Column('row_results', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
        with op.batch_alter_table('employee_uploads', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_employee_uploads_org_id'), ['org_id'], unique=False)

    if 'settings' not in existing:
        op.create_table('settings',
        sa.Column('org_id', sa.Integer(), nullable=False),
        sa.Column('key', sa.String(), nullable=False),
        sa.Column('value', sa.String(), nullable=True),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ),
        sa.PrimaryKeyConstraint('org_id', 'key')
        )

    if 'users' not in existing:
        op.create_table('users',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('org_id', sa.Integer(), nullable=True),
        sa.Column('username', sa.String(), nullable=False),
        sa.Column('pw_hash', sa.String(), nullable=False),
        sa.Column('role', sa.String(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('username')
        )

    if 'employees' not in existing:
        op.create_table('employees',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('org_id', sa.Integer(), nullable=False),
        sa.Column('emp_code', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('father_name', sa.String(), nullable=True),
        sa.Column('gender', sa.String(), nullable=True),
        sa.Column('dob', sa.Date(), nullable=True),
        sa.Column('doj', sa.Date(), nullable=True),
        sa.Column('dol', sa.Date(), nullable=True),
        sa.Column('designation', sa.String(), nullable=True),
        sa.Column('department', sa.String(), nullable=True),
        sa.Column('mobile', sa.String(), nullable=True),
        sa.Column('address', sa.Text(), nullable=True),
        sa.Column('aadhaar', sa.String(), nullable=True),
        sa.Column('pan', sa.String(), nullable=True),
        sa.Column('uan', sa.String(), nullable=True),
        sa.Column('esic_no', sa.String(), nullable=True),
        sa.Column('bank_name', sa.String(), nullable=True),
        sa.Column('bank_acct', sa.String(), nullable=True),
        sa.Column('ifsc', sa.String(), nullable=True),
        sa.Column('category_id', sa.Integer(), nullable=True),
        sa.Column('basic_override_day', sa.Float(), nullable=True),
        sa.Column('status', sa.String(), nullable=True),
        sa.ForeignKeyConstraint(['category_id'], ['categories.id'], ),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('org_id', 'aadhaar'),
        sa.UniqueConstraint('org_id', 'emp_code'),
        sa.UniqueConstraint('org_id', 'esic_no'),
        sa.UniqueConstraint('org_id', 'pan'),
        sa.UniqueConstraint('org_id', 'uan')
        )
        with op.batch_alter_table('employees', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_employees_org_id'), ['org_id'], unique=False)

    if 'attendance' not in existing:
        op.create_table('attendance',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('org_id', sa.Integer(), nullable=False),
        sa.Column('emp_id', sa.Integer(), nullable=False),
        sa.Column('month', sa.String(), nullable=False),
        sa.Column('present_days', sa.Float(), nullable=True),
        sa.Column('paid_holidays', sa.Float(), nullable=True),
        sa.Column('ot_hours', sa.Float(), nullable=True),
        sa.Column('advance', sa.Float(), nullable=True),
        sa.Column('other_deduction', sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(['emp_id'], ['employees.id'], ),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('emp_id', 'month')
        )
        with op.batch_alter_table('attendance', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_attendance_org_id'), ['org_id'], unique=False)

    if 'payslips' not in existing:
        op.create_table('payslips',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('org_id', sa.Integer(), nullable=False),
        sa.Column('emp_id', sa.Integer(), nullable=False),
        sa.Column('month', sa.String(), nullable=False),
        sa.Column('category_name', sa.String(), nullable=True),
        sa.Column('days_in_month', sa.Integer(), nullable=True),
        sa.Column('paid_days', sa.Float(), nullable=True),
        sa.Column('ncp_days', sa.Float(), nullable=True),
        sa.Column('basic', sa.Float(), nullable=True),
        sa.Column('da', sa.Float(), nullable=True),
        sa.Column('hra', sa.Float(), nullable=True),
        sa.Column('other', sa.Float(), nullable=True),
        sa.Column('ot_amount', sa.Float(), nullable=True),
        sa.Column('gross', sa.Integer(), nullable=True),
        sa.Column('epf_wages', sa.Integer(), nullable=True),
        sa.Column('eps_wages', sa.Integer(), nullable=True),
        sa.Column('edli_wages', sa.Integer(), nullable=True),
        sa.Column('pf_ee', sa.Integer(), nullable=True),
        sa.Column('eps_er', sa.Integer(), nullable=True),
        sa.Column('pf_er_diff', sa.Integer(), nullable=True),
        sa.Column('esi_wages', sa.Integer(), nullable=True),
        sa.Column('esi_ee', sa.Integer(), nullable=True),
        sa.Column('esi_er', sa.Integer(), nullable=True),
        sa.Column('advance', sa.Float(), nullable=True),
        sa.Column('other_deduction', sa.Float(), nullable=True),
        sa.Column('total_deduction', sa.Float(), nullable=True),
        sa.Column('net', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['emp_id'], ['employees.id'], ),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('emp_id', 'month')
        )
        with op.batch_alter_table('payslips', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_payslips_org_id'), ['org_id'], unique=False)

    # Columns that older installs may still be missing on tables that already exist
    if 'employee_uploads' in existing:
        have = {c['name'] for c in insp.get_columns('employee_uploads')}
        with op.batch_alter_table('employee_uploads') as batch_op:
            if 'file_data' not in have:
                batch_op.add_column(sa.Column('file_data', sa.LargeBinary(), nullable=True))
            if 'row_results' not in have:
                batch_op.add_column(sa.Column('row_results', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('payslips', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_payslips_org_id'))

    op.drop_table('payslips')
    with op.batch_alter_table('attendance', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_attendance_org_id'))

    op.drop_table('attendance')
    with op.batch_alter_table('employees', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_employees_org_id'))

    op.drop_table('employees')
    op.drop_table('users')
    op.drop_table('settings')
    with op.batch_alter_table('employee_uploads', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_employee_uploads_org_id'))

    op.drop_table('employee_uploads')
    with op.batch_alter_table('categories', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_categories_org_id'))

    op.drop_table('categories')
    with op.batch_alter_table('attendance_uploads', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_attendance_uploads_org_id'))

    op.drop_table('attendance_uploads')
    op.drop_table('organizations')
    # ### end Alembic commands ###
