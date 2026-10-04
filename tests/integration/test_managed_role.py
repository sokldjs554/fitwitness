"""Managed Postgres grants ADMIN to a role creator, not necessarily SET."""
import os
from uuid import uuid4
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from fitwitness.contracts import TenantScope
from fitwitness.storage.repository import Repository


def test_migration_grants_set_to_non_superuser_owner():
    admin_dsn = os.environ["FITWITNESS_DATABASE_URL"]
    suffix = uuid4().hex[:12]
    role, database, password = "fw_owner_" + suffix, "fw_managed_" + suffix, uuid4().hex
    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE ROLE {} LOGIN CREATEROLE PASSWORD {}").format(sql.Identifier(role), sql.Literal(password)))
        admin.execute(sql.SQL("GRANT fitwitness_app TO {} WITH ADMIN TRUE, INHERIT FALSE, SET FALSE").format(sql.Identifier(role)))
        admin.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(database), sql.Identifier(role)))
    try:
        with psycopg.connect(make_conninfo(admin_dsn, dbname=database), autocommit=True) as admin:
            admin.execute("CREATE EXTENSION vector")
        dsn = make_conninfo(admin_dsn, dbname=database, user=role, password=password)
        with psycopg.connect(dsn) as c:
            assert c.execute("SELECT pg_has_role(current_user,'fitwitness_app','SET')").fetchone()[0] is False
        repo = Repository(dsn)
        repo.migrate()
        repo.migrate()  # idempotent on an already provisioned database
        with repo.connection(TenantScope(tenant_id="managed-test", user_id="operator")) as c:
            assert c.execute("SELECT current_user").fetchone()["current_user"] == "fitwitness_app"
            assert c.execute("SELECT count(*) AS n FROM fw_revisions").fetchone()["n"] == 0
    finally:
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database)))
            admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
