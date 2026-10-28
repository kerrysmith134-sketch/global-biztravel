-- 一次性引导：角色分层 + 扩展。
-- 由官方镜像 entrypoint 以超级用户 postgres 身份、在 POSTGRES_DB(gbt_os) 上执行。
-- 之后所有 schema 变更都走 Alembic（services/api/migrations），本文件不再改动。
--
-- 注意：这里刻意不建表。表由迁移以 gbt_owner 身份创建，owner 即 gbt_owner，
-- 应用以 gbt_app 连接；两者都受 RLS 约束（见迁移 0013）。
\set ON_ERROR_STOP on

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'gbt_owner') THEN
        CREATE ROLE gbt_owner LOGIN PASSWORD 'gbt_owner_pw' NOSUPERUSER NOCREATEDB NOCREATEROLE;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'gbt_app') THEN
        CREATE ROLE gbt_app LOGIN PASSWORD 'gbt_app_pw' NOSUPERUSER NOCREATEDB NOCREATEROLE;
    END IF;
END
$$;

ALTER DATABASE gbt_os OWNER TO gbt_owner;
ALTER SCHEMA public OWNER TO gbt_owner;
GRANT USAGE ON SCHEMA public TO gbt_app;
GRANT ALL ON SCHEMA public TO gbt_owner;

-- 扩展需超级用户创建（postgis / vector 均非 trusted 扩展）
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS vector;

-- 迁移角色建表后自动把 DML 授予应用角色，避免每次新增表都手工补 GRANT
ALTER DEFAULT PRIVILEGES FOR ROLE gbt_owner IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO gbt_app;
ALTER DEFAULT PRIVILEGES FOR ROLE gbt_owner IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO gbt_app;