"""持久化 AI 请求账本，不保存请求正文或密钥。"""
from alembic import op

revision = "cs_0003"
down_revision = "cs_0002"
branch_labels = None
depends_on = None


def upgrade():
    op.get_context().config.attributes["metadata"].tables["ai_requests"].create(op.get_bind(), checkfirst=True)


def downgrade():
    raise RuntimeError("不允许降级用户数据。")
