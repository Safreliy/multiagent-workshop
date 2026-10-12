-- The source database is cloned but never queried during experiments.  Besides
-- preventing accidental mutation, this avoids active-session races with CREATE
-- DATABASE ... TEMPLATE.
COMMENT ON DATABASE sql_agent_fixture_v2 IS 'sql-agent-scaling-v2';
\connect postgres
ALTER DATABASE sql_agent_fixture_v2 ALLOW_CONNECTIONS false;
