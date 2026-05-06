-- db-bootstrap.sql
-- Phase 0 sub-task 0.1: provision fishingposter database + least-privilege role.
-- Container: o630hdmppejmchbw7gn2qmn2 (postgres:16-alpine) on droplet 134.122.113.128.
-- Superuser in this container is `realms` (not `postgres`).
--
-- Usage:
--   docker exec -e APP_PASSWORD='<strong-password>' o630hdmppejmchbw7gn2qmn2 \
--     psql -U realms -d postgres -v ON_ERROR_STOP=1 \
--     -v app_password="'$APP_PASSWORD'" -f /tmp/db-bootstrap.sql
--
-- The :app_password psql variable substitution keeps the real password out of this file.
-- For ad-hoc reference, replace :'app_password' with '<APP_PASSWORD>' literal.

\set ON_ERROR_STOP on

-- 1. Create least-privilege application role (no superuser, no createdb, no createrole).
CREATE ROLE fishingposter_app WITH LOGIN PASSWORD :'app_password';

-- 2. Create the database, owned by the app role so it controls its own schema.
CREATE DATABASE fishingposter OWNER fishingposter_app;

-- 3. Tighten public-schema grants inside the new DB.
\c fishingposter

GRANT CONNECT ON DATABASE fishingposter TO fishingposter_app;
GRANT USAGE, CREATE ON SCHEMA public TO fishingposter_app;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO fishingposter_app;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO fishingposter_app;
