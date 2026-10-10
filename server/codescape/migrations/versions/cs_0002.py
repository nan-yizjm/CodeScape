"""项目删除生命周期、持久化操作与首次初始化标识。"""
import sqlalchemy as sa
from alembic import context, op

revision = "cs_0002"
down_revision = "cs_0001"
branch_labels = None
depends_on = None


def upgrade():
    connection = context.get_context().connection
    if "lifecycle" not in {column["name"] for column in sa.inspect(connection).get_columns("prototype_projects")}:
        op.add_column("prototype_projects", sa.Column("lifecycle", sa.String(), nullable=False, server_default="active"))
    context.get_context().config.attributes["metadata"].create_all(connection)


def downgrade():
    raise RuntimeError("不支持自动降级数据库。")
