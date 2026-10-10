"""为现有原型库建立可验证的正式模式基线，不丢弃学习数据。"""
from alembic import context

revision = "cs_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    config = context.get_context().config
    config.attributes["metadata"].create_all(context.get_context().connection)


def downgrade():
    raise RuntimeError("不支持自动降级数据库。")
