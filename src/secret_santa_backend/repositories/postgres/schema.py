from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Identity,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)

metadata = MetaData()

users = Table(
    "users",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("login", String(255), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(
        "char_length(login) BETWEEN 1 AND 255 AND login = btrim(login)",
        name="ck_users_login_valid",
    ),
    UniqueConstraint("login", name="uq_users_login"),
)

santa_groups = Table(
    "santa_groups",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column(
        "organizer_user_id",
        BigInteger,
        ForeignKey("users.id", name="fk_santa_groups_organizer", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    ),
    Column("name", String(120), nullable=False),
    Column("description", Text, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(
        "char_length(name) BETWEEN 1 AND 120 AND name !~ '^[[:space:]]|[[:space:]]$'",
        name="ck_santa_groups_name_valid",
    ),
    CheckConstraint("char_length(description) <= 2000", name="ck_santa_groups_description_length"),
)
