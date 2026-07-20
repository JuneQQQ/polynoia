from sqlalchemy import String
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from polynoia.storage.bootstrap import _SCHEMA_PATCHES
from polynoia.storage.models import MessageRow

FRONTEND_USER_MESSAGE_ID = "u-00000000-0000-0000-0000-000000000000"


def test_message_model_can_store_frontend_uuid_ids_and_reply_targets() -> None:
    assert len(FRONTEND_USER_MESSAGE_ID) == 38

    for column_name in ("id", "in_reply_to"):
        column_type = MessageRow.__table__.c[column_name].type
        assert isinstance(column_type, String)
        assert column_type.length == 64
        assert len(FRONTEND_USER_MESSAGE_ID) <= column_type.length


def test_postgresql_message_ddl_uses_varchar_64_for_client_ids() -> None:
    ddl = str(
        CreateTable(MessageRow.__table__).compile(dialect=postgresql.dialect())
    )

    assert "\tid VARCHAR(64) NOT NULL" in ddl
    assert "\tin_reply_to VARCHAR(64)" in ddl


def test_bootstrap_reply_column_patch_uses_varchar_64() -> None:
    patch_sql = next(
        sql
        for table, column, sql in _SCHEMA_PATCHES
        if (table, column) == ("messages", "in_reply_to")
    )

    assert patch_sql == "ALTER TABLE messages ADD COLUMN in_reply_to VARCHAR(64)"
