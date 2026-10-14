from alembic import context

connection = context.config.attributes["connection"]
context.configure(connection=connection, target_metadata=context.config.attributes["metadata"],
                  transactional_ddl=True)
with context.begin_transaction():
    context.run_migrations()
