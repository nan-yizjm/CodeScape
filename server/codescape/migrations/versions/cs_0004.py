"""学习笔记、人工架构、版本保留与独立工作区文档。"""
from alembic import op

revision = "cs_0004"
down_revision = "cs_0003"
branch_labels = None
depends_on = None


def upgrade():
    op.get_context().config.attributes["metadata"].tables["workspace_documents"].create(op.get_bind(), checkfirst=True)


def downgrade():
    raise RuntimeError("不允许降级用户数据。")
