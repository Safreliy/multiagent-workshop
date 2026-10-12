"""Read-only PostgreSQL tools used by each benchmark architecture.

The Python checks provide useful, deterministic errors to an agent. Database
permissions and a read-only transaction are the actual security boundary.
"""

from __future__ import annotations

import os
import re
import statistics
import time
import hashlib
from contextlib import contextmanager
from datetime import date, datetime, time as datetime_time
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Iterator, Literal, Mapping

import psycopg
from psycopg import sql as pg_sql
import sqlglot
from sqlglot import exp


DEFAULT_DSN = "postgresql://sql_agent@127.0.0.1:55432/sql_agent_lab"
DEFAULT_BASELINE_PATH = Path(__file__).resolve().parents[1] / "db" / "baseline.sql"
FreedomProfile = Literal["query_only", "indexes", "hints", "all"]
FREEDOM_PROFILES = frozenset({"query_only", "indexes", "hints", "all"})
FIXTURE_ID = "sql-agent-scaling-v2"
_GENERATED_DATABASE = re.compile(r"^sqlbench_[0-9a-f]{12}_candidate$")
_INDEX_PART = re.compile(
    r"^(?P<column>[a-z_][a-z0-9_]*)(?:\s+(?P<direction>ASC|DESC))?"
    r"(?:\s+NULLS\s+(?P<nulls>FIRST|LAST))?$",
    re.IGNORECASE,
)
_HINT_COMMENT = re.compile(r"/\*\+", re.IGNORECASE)
_HINT_BODIES = re.compile(r"/\*\+(.*?)\*/", re.IGNORECASE | re.DOTALL)
_HINT_CALL = re.compile(r"\b([A-Za-z][A-Za-z0-9_]*)\s*\(")
_ALLOWED_HINTS = frozenset(
    {
        "seqscan", "tidscan", "indexscan", "indexonlyscan", "bitmapscan",
        "noseqscan", "notidscan", "noindexscan", "noindexonlyscan", "nobitmapscan",
        "nestloop", "mergejoin", "hashjoin", "nonestloop", "nomergejoin", "nohashjoin",
        "leading", "rows", "parallel", "memoize", "nomemoize",
    }
)
_FIXTURE_TUNABLE_INDEXES = frozenset(
    {
        "orders_customer_created_idx",
        "orders_completed_created_idx",
        "order_items_product_idx",
        "products_category_idx",
        "product_suppliers_supplier_idx",
        "payments_order_status_idx",
        "shipments_order_idx",
        "returns_order_idx",
        "order_promotions_order_idx",
    }
)
_BASE_AGENT_TOOLS = (
    "get_schema",
    "get_indexes",
    "get_statistics",
    "explain",
    "explain_analyze",
    "execute",
    "benchmark",
    "submit_candidate",
    "get_capabilities",
)

_DENIED_AST_KEYS = {
    "alter",
    "analyze",
    "attach",
    "cache",
    "command",
    "commit",
    "copy",
    "create",
    "delete",
    "detach",
    "drop",
    "grant",
    "insert",
    "into",
    "load_data",
    "lock",
    "merge",
    "pragma",
    "refresh",
    "revoke",
    "rollback",
    "set",
    "transaction",
    "truncate",
    "uncache",
    "update",
    "use",
}
_DENIED_FUNCTIONS = {
    "dblink",
    "dblink_connect",
    "dblink_connect_u",
    "dblink_exec",
    "lo_export",
    "lo_import",
    "nextval",
    "pg_advisory_lock",
    "pg_advisory_lock_shared",
    "pg_advisory_unlock",
    "pg_advisory_unlock_all",
    "pg_advisory_unlock_shared",
    "pg_cancel_backend",
    "pg_log_backend_memory_contexts",
    "pg_reload_conf",
    "pg_rotate_logfile",
    "pg_sleep",
    "pg_terminate_backend",
    "set_config",
}
_DENIED_FUNCTION_PREFIXES = ("pg_advisory_", "pg_create_", "pg_drop_", "pg_replication_")
_LOCKING_CLAUSE = re.compile(
    r"\bFOR\s+(?:NO\s+KEY\s+UPDATE|KEY\s+SHARE|UPDATE|SHARE)\b", re.IGNORECASE
)


class CandidateValidationError(ValueError):
    """Raised internally when candidate SQL is outside the tool contract."""


def _validate_hint_comments(sql: str) -> None:
    bodies = _HINT_BODIES.findall(sql)
    if not bodies:
        raise CandidateValidationError("malformed pg_hint_plan hint comment")
    for body in bodies:
        calls = _HINT_CALL.findall(body)
        if not calls:
            raise CandidateValidationError("pg_hint_plan hint comment contains no recognized hint call")
        denied = sorted({name for name in calls if name.lower() not in _ALLOWED_HINTS})
        if denied:
            raise CandidateValidationError(
                "unsupported pg_hint_plan hint(s): " + ", ".join(denied)
            )


def _trim_statement(sql: str) -> str:
    return sql.strip().removesuffix(";").rstrip()


def _function_name(node: exp.Func) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.name).lower()
    try:
        return str(node.sql_name()).lower()
    except (AttributeError, TypeError):
        return str(getattr(node, "name", "")).lower()


def _validate_sql_or_raise(sql: str, *, allow_hints: bool = False) -> exp.Expression:
    if not isinstance(sql, str) or not sql.strip():
        raise CandidateValidationError("SQL must be a non-empty string")
    if len(sql) > 200_000:
        raise CandidateValidationError("SQL exceeds the 200,000 character limit")
    if _HINT_COMMENT.search(sql):
        if not allow_hints:
            raise CandidateValidationError("pg_hint_plan hints are not enabled in this freedom profile")
        _validate_hint_comments(sql)

    try:
        # SQLGlot represents a comment after the final terminator as an empty
        # Semicolon expression. It is not a second executable statement.
        statements = [
            statement for statement in sqlglot.parse(sql, read="postgres")
            if statement and not (isinstance(statement, exp.Semicolon) and not statement.args)
        ]
    except sqlglot.errors.ParseError as exc:
        raise CandidateValidationError(f"SQL parse error: {exc}") from exc

    if len(statements) != 1:
        raise CandidateValidationError("exactly one SQL statement is allowed")
    statement = statements[0]
    if not isinstance(statement, exp.Query):
        raise CandidateValidationError("only SELECT queries (including WITH/set operations) are allowed")

    for node in statement.walk():
        node_key = str(getattr(node, "key", "")).lower()
        if node_key in _DENIED_AST_KEYS:
            raise CandidateValidationError(f"disallowed SQL operation: {node_key.upper()}")
        if isinstance(node, exp.Func):
            function_name = _function_name(node)
            if function_name in _DENIED_FUNCTIONS or function_name.startswith(
                _DENIED_FUNCTION_PREFIXES
            ):
                raise CandidateValidationError(f"disallowed SQL function: {function_name}")
        if isinstance(node, (exp.Parameter, exp.Placeholder)):
            raise CandidateValidationError("query parameters are not supported")

    # SQLGlot versions have represented PostgreSQL locking clauses in different
    # ways. This secondary check is intentionally narrow and operates on the
    # normalized AST output, so strings and comments cannot trigger it.
    normalized = statement.sql(dialect="postgres")
    if _LOCKING_CLAUSE.search(normalized):
        raise CandidateValidationError("row-locking clauses are not allowed")
    return statement


def validate_candidate(sql: str, *, allow_hints: bool = False) -> dict[str, Any]:
    """Validate one candidate without connecting to PostgreSQL."""

    try:
        statement = _validate_sql_or_raise(sql, allow_hints=allow_hints)
    except CandidateValidationError as exc:
        return {"valid": False, "error": str(exc)}
    return {
        "valid": True,
        "statement_type": statement.key.upper(),
        "normalized_sql": statement.sql(dialect="postgres", pretty=False),
    }


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, datetime_time)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return str(value)


def _error_message(exc: BaseException) -> str:
    message = " ".join(str(exc).split())
    return message[:800] or exc.__class__.__name__


class PostgresTools:
    """JSON-returning, read-only tools around a deterministic PostgreSQL fixture."""

    def __init__(
        self,
        dsn: str | None = None,
        baseline_sql: str | None = None,
        *,
        freedom_profile: FreedomProfile = "query_only",
        controller_dsn: str | None = None,
        statement_timeout_ms: int = 15_000,
        max_rows: int = 50,
        metrics_hook: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        if statement_timeout_ms < 100 or statement_timeout_ms > 120_000:
            raise ValueError("statement_timeout_ms must be between 100 and 120000")
        if max_rows < 1 or max_rows > 1_000:
            raise ValueError("max_rows must be between 1 and 1000")
        if freedom_profile not in FREEDOM_PROFILES:
            raise ValueError(
                f"freedom_profile must be one of {', '.join(sorted(FREEDOM_PROFILES))}"
            )
        self.dsn = dsn or os.getenv("SQL_AGENT_DSN", DEFAULT_DSN)
        self.controller_dsn = controller_dsn
        self.freedom_profile: FreedomProfile = freedom_profile
        self.baseline_sql = _trim_statement(
            baseline_sql if baseline_sql is not None else DEFAULT_BASELINE_PATH.read_text("utf-8")
        )
        _validate_sql_or_raise(self.baseline_sql, allow_hints=self._hints_enabled)
        self.statement_timeout_ms = statement_timeout_ms
        self.max_rows = max_rows
        self.metrics_hook = metrics_hook
        self._action_log: list[dict[str, Any]] = []

    @property
    def _indexes_enabled(self) -> bool:
        return self.freedom_profile in {"indexes", "all"}

    @property
    def _hints_enabled(self) -> bool:
        return self.freedom_profile in {"hints", "all"}

    @property
    def available_tools(self) -> tuple[str, ...]:
        optional = (
            "create_candidate_index",
            "configure_index",
            "drop_candidate_index",
        ) if self._indexes_enabled else ()
        return _BASE_AGENT_TOOLS + optional

    def _emit(self, tool: str, started: float, ok: bool, **details: Any) -> None:
        if self.metrics_hook is None:
            return
        event = {
            "event": "tool_call",
            "tool": tool,
            "ok": ok,
            "wall_time_ms": round((time.perf_counter() - started) * 1000, 3),
            **details,
        }
        try:
            self.metrics_hook(event)
        except Exception:
            # Instrumentation must never alter an experimental run.
            pass

    @contextmanager
    def _readonly_connection(self) -> Iterator[psycopg.Connection[Any]]:
        with psycopg.connect(self.dsn, autocommit=False, connect_timeout=5) as connection:
            with connection.transaction():
                connection.execute(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
                )
                connection.execute(
                    "SELECT set_config('statement_timeout', %s, true)",
                    (f"{self.statement_timeout_ms}ms",),
                )
                connection.execute(
                    "SELECT set_config('lock_timeout', '2000ms', true)"
                )
                connection.execute(
                    "SELECT set_config('search_path', 'public, pg_catalog', true)"
                )
                yield connection

    def validate_candidate(self, sql: str) -> dict[str, Any]:
        """Validate syntax, result types, and exact multiset equivalence.

        This is the deterministic final correctness check without performance
        measurement. The module-level function of the same name is deliberately
        a cheap AST-only check for callers that do not have a live database.
        """

        started = time.perf_counter()
        query, error = self._validated(sql)
        if error:
            result = {
                "ok": True,
                "correct": False,
                "error": error["error"],
                "validation_error": True,
            }
            self._emit("validate_candidate", started, True, correct=False)
            return result
        try:
            with self._readonly_connection() as connection:
                equivalent, reason = self._equivalent(connection, query)
            result = {"ok": True, "correct": equivalent}
            if reason:
                result["error"] = reason
            self._emit("validate_candidate", started, True, correct=equivalent)
            return result
        except Exception as exc:
            self._emit("validate_candidate", started, False, correct=False)
            return {"ok": False, "correct": False, "error": _error_message(exc)}

    def get_schema(self) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            with self._readonly_connection() as connection:
                rows = connection.execute(
                    """
                    SELECT c.table_name, c.ordinal_position, c.column_name,
                           c.data_type, c.is_nullable, c.column_default
                    FROM information_schema.columns AS c
                    WHERE c.table_schema = 'public'
                    ORDER BY c.table_name, c.ordinal_position
                    """
                ).fetchall()
            tables: dict[str, list[dict[str, Any]]] = {}
            for table, position, column, data_type, nullable, default in rows:
                tables.setdefault(table, []).append(
                    {
                        "position": position,
                        "name": column,
                        "type": data_type,
                        "nullable": nullable == "YES",
                        "default": default,
                    }
                )
            result = {"ok": True, "tables": tables}
            self._emit("get_schema", started, True, table_count=len(tables))
            return result
        except Exception as exc:
            result = {"ok": False, "error": _error_message(exc)}
            self._emit("get_schema", started, False)
            return result

    def get_indexes(self) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            with self._readonly_connection() as connection:
                rows = connection.execute(
                    """
                    SELECT tbl.relname AS table_name, idx.relname AS index_name,
                           i.indisunique, i.indisprimary,
                           pg_get_indexdef(i.indexrelid) AS definition,
                           pg_relation_size(i.indexrelid) AS size_bytes
                    FROM pg_index AS i
                    JOIN pg_class AS idx ON idx.oid = i.indexrelid
                    JOIN pg_class AS tbl ON tbl.oid = i.indrelid
                    JOIN pg_namespace AS ns ON ns.oid = tbl.relnamespace
                    WHERE ns.nspname = 'public'
                    ORDER BY tbl.relname, idx.relname
                    """
                ).fetchall()
            indexes = [
                {
                    "table": row[0],
                    "name": row[1],
                    "unique": row[2],
                    "primary": row[3],
                    "definition": row[4],
                    "size_bytes": row[5],
                }
                for row in rows
            ]
            self._emit("get_indexes", started, True, index_count=len(indexes))
            return {"ok": True, "indexes": indexes}
        except Exception as exc:
            self._emit("get_indexes", started, False)
            return {"ok": False, "error": _error_message(exc)}

    def get_statistics(self) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            with self._readonly_connection() as connection:
                table_rows = connection.execute(
                    """
                    SELECT c.relname, c.reltuples::bigint, pg_relation_size(c.oid)
                    FROM pg_class AS c
                    JOIN pg_namespace AS ns ON ns.oid = c.relnamespace
                    WHERE ns.nspname = 'public' AND c.relkind = 'r'
                    ORDER BY c.relname
                    """
                ).fetchall()
                column_rows = connection.execute(
                    """
                    SELECT tablename, attname, null_frac, n_distinct,
                           most_common_vals::text, most_common_freqs::text,
                           histogram_bounds::text, correlation
                    FROM pg_stats
                    WHERE schemaname = 'public'
                    ORDER BY tablename, attname
                    """
                ).fetchall()
            result = {
                "ok": True,
                "tables": [
                    {"table": row[0], "estimated_rows": row[1], "size_bytes": row[2]}
                    for row in table_rows
                ],
                "columns": [
                    {
                        "table": row[0],
                        "column": row[1],
                        "null_fraction": row[2],
                        "distinct_estimate": row[3],
                        "most_common_values": row[4],
                        "most_common_frequencies": row[5],
                        "histogram_bounds": row[6],
                        "correlation": row[7],
                    }
                    for row in column_rows
                ],
            }
            self._emit("get_statistics", started, True, table_count=len(table_rows))
            return _json_value(result)
        except Exception as exc:
            self._emit("get_statistics", started, False)
            return {"ok": False, "error": _error_message(exc)}

    def get_capabilities(self) -> dict[str, Any]:
        """Return the active freedom profile and real pg_hint_plan availability."""

        started = time.perf_counter()
        try:
            with self._readonly_connection() as connection:
                row = connection.execute(
                    """
                    SELECT current_database(),
                           (SELECT extversion FROM pg_extension WHERE extname = 'pg_hint_plan'),
                           current_setting('pg_hint_plan.enable_hint', true)
                    """
                ).fetchone()
            extension_version = row[1]
            hint_setting = row[2]
            # The custom setting exists only when the module has been loaded.
            # sql_agent cannot read the superuser-only shared_preload_libraries
            # setting, so this is the least-privilege capability check.
            preloaded = hint_setting is not None
            hint_available = bool(extension_version and preloaded and hint_setting == "on")
            result = {
                "ok": True,
                "database": row[0],
                "freedom_profile": self.freedom_profile,
                "available_tools": list(self.available_tools),
                "index_changes_allowed": self._indexes_enabled,
                "hints_allowed": self._hints_enabled,
                "pg_hint_plan": {
                    "available": hint_available,
                    "extension_version": extension_version,
                    "preloaded": preloaded,
                    "enabled": hint_setting == "on",
                },
            }
            if self._hints_enabled and not hint_available:
                result["configuration_error"] = (
                    "profile enables hints but pg_hint_plan is not installed, preloaded and enabled"
                )
            self._emit("get_capabilities", started, True)
            return result
        except Exception as exc:
            self._emit("get_capabilities", started, False)
            return {"ok": False, "error": _error_message(exc)}

    @contextmanager
    def _controller_connection(self) -> Iterator[psycopg.Connection[Any]]:
        if not self._indexes_enabled:
            raise CandidateValidationError("index changes are not enabled in this freedom profile")
        if not self.controller_dsn:
            raise CandidateValidationError("controller_dsn is required for index changes")
        with psycopg.connect(self.controller_dsn, autocommit=True, connect_timeout=5) as connection:
            database = connection.execute("SELECT current_database()").fetchone()[0]
            if not _GENERATED_DATABASE.fullmatch(database):
                raise CandidateValidationError(
                    "index controller may only target an isolated generated candidate database"
                )
            try:
                marker = connection.execute(
                    "SELECT fixture_id FROM public.benchmark_fixture_metadata"
                ).fetchone()
            except Exception as exc:
                raise CandidateValidationError("candidate database has no fixture marker") from exc
            if marker is None or marker[0] != FIXTURE_ID:
                raise CandidateValidationError("candidate database fixture marker does not match")
            connection.execute("SET statement_timeout = '120s'")
            connection.execute("SET lock_timeout = '5s'")
            yield connection

    @staticmethod
    def _index_parts(columns: list[str]) -> list[tuple[str, str | None, str | None]]:
        if not isinstance(columns, list) or not 1 <= len(columns) <= 6:
            raise CandidateValidationError("columns must contain between 1 and 6 items")
        parsed: list[tuple[str, str | None, str | None]] = []
        seen: set[str] = set()
        for item in columns:
            if not isinstance(item, str):
                raise CandidateValidationError("each index column must be a string")
            match = _INDEX_PART.fullmatch(item.strip())
            if not match:
                raise CandidateValidationError(
                    f"invalid index column {item!r}; use 'column [ASC|DESC] [NULLS FIRST|LAST]'"
                )
            column = match.group("column").lower()
            if column in seen:
                raise CandidateValidationError(f"duplicate index column: {column}")
            seen.add(column)
            parsed.append(
                (
                    column,
                    match.group("direction").upper() if match.group("direction") else None,
                    match.group("nulls").upper() if match.group("nulls") else None,
                )
            )
        return parsed

    @staticmethod
    def _predicate_sql(table: str, predicate: str | None, allowed_columns: set[str]) -> str | None:
        if predicate is None or not predicate.strip():
            return None
        if len(predicate) > 2_000 or _HINT_COMMENT.search(predicate):
            raise CandidateValidationError("invalid partial-index predicate")
        try:
            parsed = sqlglot.parse_one(
                f"SELECT 1 FROM {table} WHERE {predicate}", read="postgres"
            )
        except sqlglot.errors.ParseError as exc:
            raise CandidateValidationError(f"predicate parse error: {exc}") from exc
        where = parsed.args.get("where")
        if where is None:
            raise CandidateValidationError("partial-index predicate is missing")
        for node in where.this.walk():
            if isinstance(node, (exp.Query, exp.Subquery, exp.Parameter, exp.Placeholder)) or (
                isinstance(node, exp.Func) and not isinstance(node, exp.Cast)
            ):
                raise CandidateValidationError(
                    "partial-index predicate may not contain subqueries, functions or parameters"
                )
            if isinstance(node, exp.Column):
                if node.name.lower() not in allowed_columns:
                    raise CandidateValidationError(f"unknown predicate column: {node.name}")
                if node.table and node.table.lower() != table.lower():
                    raise CandidateValidationError("predicate may only reference the indexed table")
        return where.this.sql(dialect="postgres", pretty=False)

    @staticmethod
    def _table_columns(connection: psycopg.Connection[Any], table: str) -> set[str]:
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", table):
            raise CandidateValidationError("table must be a simple lowercase identifier")
        rows = connection.execute(
            """
            SELECT a.attname
            FROM pg_attribute AS a
            JOIN pg_class AS c ON c.oid = a.attrelid
            JOIN pg_namespace AS n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relname = %s AND c.relkind = 'r'
              AND a.attnum > 0 AND NOT a.attisdropped
            """,
            (table,),
        ).fetchall()
        if not rows:
            raise CandidateValidationError(f"unknown fixture table: {table}")
        return {row[0] for row in rows}

    def create_candidate_index(
        self,
        table: str,
        columns: list[str],
        include: list[str] | None = None,
        predicate: str | None = None,
        fillfactor: int = 90,
    ) -> dict[str, Any]:
        """Build one allowlisted B-tree index on the isolated candidate database.

        Columns use ``name [ASC|DESC] [NULLS FIRST|LAST]``.  INCLUDE accepts
        simple column names.  A partial predicate may use only columns, literals
        and operators: functions and subqueries are rejected.
        """

        started = time.perf_counter()
        try:
            parts = self._index_parts(columns)
            includes = include or []
            if not isinstance(includes, list) or len(includes) > 8:
                raise CandidateValidationError("include must contain at most 8 columns")
            if not isinstance(fillfactor, int) or not 10 <= fillfactor <= 100:
                raise CandidateValidationError("fillfactor must be an integer between 10 and 100")
            with self._controller_connection() as connection:
                allowed = self._table_columns(connection, table)
                indexed_names = {part[0] for part in parts}
                if not indexed_names <= allowed:
                    unknown = sorted(indexed_names - allowed)
                    raise CandidateValidationError(f"unknown index columns: {unknown}")
                include_names: list[str] = []
                for item in includes:
                    if not isinstance(item, str) or not re.fullmatch(r"[a-z_][a-z0-9_]*", item):
                        raise CandidateValidationError("INCLUDE values must be simple column names")
                    if item not in allowed:
                        raise CandidateValidationError(f"unknown INCLUDE column: {item}")
                    if item in indexed_names or item in include_names:
                        raise CandidateValidationError(f"duplicate index/INCLUDE column: {item}")
                    include_names.append(item)
                normalized_predicate = self._predicate_sql(table, predicate, allowed)
                count = connection.execute(
                    """
                    SELECT count(*) FROM pg_class AS c
                    JOIN pg_namespace AS n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public' AND c.relkind = 'i'
                      AND c.relname LIKE 'agent_idx_%'
                    """
                ).fetchone()[0]
                signature = repr((table, parts, include_names, normalized_predicate, fillfactor))
                index_name = "agent_idx_" + hashlib.sha256(signature.encode()).hexdigest()[:16]
                exists = connection.execute(
                    "SELECT to_regclass(%s)", (f"public.{index_name}",)
                ).fetchone()[0]
                if exists:
                    definition = connection.execute(
                        "SELECT pg_get_indexdef(%s::regclass)", (f"public.{index_name}",)
                    ).fetchone()[0]
                    result = {"ok": True, "created": False, "index": index_name, "definition": definition}
                    self._emit("create_candidate_index", started, True, created=False)
                    return result
                if count >= 4:
                    raise CandidateValidationError("at most 4 candidate indexes may exist at once")

                rendered_parts = []
                for column, direction, nulls in parts:
                    fragment = pg_sql.Identifier(column)
                    if direction:
                        fragment += pg_sql.SQL(" " + direction)
                    if nulls:
                        fragment += pg_sql.SQL(" NULLS " + nulls)
                    rendered_parts.append(fragment)
                statement = pg_sql.SQL("CREATE INDEX {} ON public.{} ({})").format(
                    pg_sql.Identifier(index_name),
                    pg_sql.Identifier(table),
                    pg_sql.SQL(", ").join(rendered_parts),
                )
                if include_names:
                    statement += pg_sql.SQL(" INCLUDE ({})").format(
                        pg_sql.SQL(", ").join(map(pg_sql.Identifier, include_names))
                    )
                statement += pg_sql.SQL(" WITH (fillfactor = {})").format(
                    pg_sql.Literal(fillfactor)
                )
                if normalized_predicate:
                    statement += pg_sql.SQL(" WHERE ") + pg_sql.SQL(normalized_predicate)
                ddl = statement.as_string(connection)
                build_started = time.perf_counter()
                with connection.transaction():
                    connection.execute(statement)
                    build_ms = (time.perf_counter() - build_started) * 1000
                    definition, size_bytes = connection.execute(
                        "SELECT pg_get_indexdef(%s::regclass), pg_relation_size(%s::regclass)",
                        (f"public.{index_name}", f"public.{index_name}"),
                    ).fetchone()
            action = {
                "action": "create_index",
                "index": index_name,
                "ddl": ddl,
                "definition": definition,
                "build_time_ms": round(build_ms, 3),
                "storage_bytes": size_bytes,
            }
            self._action_log.append(action)
            result = {"ok": True, "created": True, **action}
            self._emit("create_candidate_index", started, True, created=True)
            return result
        except Exception as exc:
            self._emit("create_candidate_index", started, False)
            return {"ok": False, "error": _error_message(exc)}

    def configure_index(self, index_name: str, fillfactor: int) -> dict[str, Any]:
        """Set B-tree fillfactor and rebuild one fixture or candidate index."""

        started = time.perf_counter()
        try:
            if not isinstance(fillfactor, int) or not 10 <= fillfactor <= 100:
                raise CandidateValidationError("fillfactor must be an integer between 10 and 100")
            if index_name not in _FIXTURE_TUNABLE_INDEXES and not re.fullmatch(
                r"agent_idx_[0-9a-f]{16}", index_name
            ):
                raise CandidateValidationError("index is not allowlisted for tuning")
            with self._controller_connection() as connection:
                row = connection.execute(
                    """
                    SELECT i.relname, am.amname
                    FROM pg_class AS i
                    JOIN pg_namespace AS n ON n.oid = i.relnamespace
                    JOIN pg_am AS am ON am.oid = i.relam
                    WHERE n.nspname = 'public' AND i.relkind = 'i' AND i.relname = %s
                    """,
                    (index_name,),
                ).fetchone()
                if row is None or row[1] != "btree":
                    raise CandidateValidationError("only an existing public B-tree index can be tuned")
                alter = pg_sql.SQL("ALTER INDEX public.{} SET (fillfactor = {})").format(
                    pg_sql.Identifier(index_name), pg_sql.Literal(fillfactor)
                )
                with connection.transaction():
                    connection.execute(alter)
                    build_started = time.perf_counter()
                    connection.execute(
                        pg_sql.SQL("REINDEX INDEX public.{}").format(pg_sql.Identifier(index_name))
                    )
                    build_ms = (time.perf_counter() - build_started) * 1000
                    size_bytes = connection.execute(
                        "SELECT pg_relation_size(%s::regclass)", (f"public.{index_name}",)
                    ).fetchone()[0]
            action = {
                "action": "configure_index",
                "index": index_name,
                "fillfactor": fillfactor,
                "build_time_ms": round(build_ms, 3),
                "storage_bytes": size_bytes,
            }
            self._action_log.append(action)
            self._emit("configure_index", started, True)
            return {"ok": True, **action}
        except Exception as exc:
            self._emit("configure_index", started, False)
            return {"ok": False, "error": _error_message(exc)}

    def drop_candidate_index(self, index_name: str) -> dict[str, Any]:
        """Drop only an index created by create_candidate_index in this candidate clone."""

        started = time.perf_counter()
        try:
            if not re.fullmatch(r"agent_idx_[0-9a-f]{16}", index_name):
                raise CandidateValidationError("only generated candidate indexes may be dropped")
            with self._controller_connection() as connection:
                exists = connection.execute(
                    "SELECT to_regclass(%s)", (f"public.{index_name}",)
                ).fetchone()[0]
                if not exists:
                    raise CandidateValidationError("candidate index does not exist")
                size_bytes = connection.execute(
                    "SELECT pg_relation_size(%s::regclass)", (f"public.{index_name}",)
                ).fetchone()[0]
                connection.execute(
                    pg_sql.SQL("DROP INDEX public.{}").format(pg_sql.Identifier(index_name))
                )
            action = {"action": "drop_index", "index": index_name, "storage_bytes": size_bytes}
            self._action_log.append(action)
            self._emit("drop_candidate_index", started, True)
            return {"ok": True, **action}
        except Exception as exc:
            self._emit("drop_candidate_index", started, False)
            return {"ok": False, "error": _error_message(exc)}

    def reset_environment(self) -> dict[str, Any]:
        """Remove candidate indexes and restore fixture-index storage parameters."""

        if not self._indexes_enabled:
            return {"ok": True, "changed": False}
        try:
            with self._controller_connection() as connection:
                generated = connection.execute(
                    """
                    SELECT c.relname FROM pg_class AS c
                    JOIN pg_namespace AS n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public' AND c.relkind = 'i'
                      AND c.relname LIKE 'agent_idx_%'
                    """
                ).fetchall()
                for (name,) in generated:
                    if not re.fullmatch(r"agent_idx_[0-9a-f]{16}", name):
                        raise CandidateValidationError("unexpected index in reserved namespace")
                    connection.execute(
                        pg_sql.SQL("DROP INDEX public.{}").format(pg_sql.Identifier(name))
                    )
                tuned = connection.execute(
                    """
                    SELECT c.relname FROM pg_class AS c
                    JOIN pg_namespace AS n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public' AND c.relkind = 'i'
                      AND c.relname = ANY(%s) AND c.reloptions IS NOT NULL
                    """,
                    (list(_FIXTURE_TUNABLE_INDEXES),),
                ).fetchall()
                for (name,) in tuned:
                    connection.execute(
                        pg_sql.SQL("ALTER INDEX public.{} RESET (fillfactor)").format(
                            pg_sql.Identifier(name)
                        )
                    )
                    connection.execute(
                        pg_sql.SQL("REINDEX INDEX public.{}").format(pg_sql.Identifier(name))
                    )
            return {"ok": True, "changed": bool(generated or tuned)}
        except Exception as exc:
            return {"ok": False, "error": _error_message(exc)}

    def experiment_artifacts(self) -> dict[str, Any]:
        """Return schema-action costs and the current index state for run artifacts."""

        created = [a for a in self._action_log if a["action"] == "create_index"]
        indexes = self.get_indexes()
        current_candidates = [
            item for item in indexes.get("indexes", [])
            if item["name"].startswith("agent_idx_")
        ] if indexes.get("ok") else []
        return {
            "freedom_profile": self.freedom_profile,
            "actions": list(self._action_log),
            "index_build_time_ms": round(
                sum(float(a.get("build_time_ms", 0)) for a in self._action_log), 3
            ),
            "created_index_storage_bytes_cumulative": sum(
                int(a["storage_bytes"]) for a in created
            ),
            "current_candidate_index_storage_bytes": sum(
                int(item["size_bytes"]) for item in current_candidates
            ),
            "final_indexes": indexes,
        }

    def _validated(self, sql: str) -> tuple[str | None, dict[str, Any] | None]:
        validation = validate_candidate(sql, allow_hints=self._hints_enabled)
        if not validation["valid"]:
            return None, {"ok": False, "error": validation["error"], "validation_error": True}
        return _trim_statement(sql), None

    def explain(self, sql: str) -> dict[str, Any]:
        return self._explain(sql, analyze=False)

    def explain_analyze(self, sql: str) -> dict[str, Any]:
        return self._explain(sql, analyze=True)

    def _explain(self, sql: str, *, analyze: bool) -> dict[str, Any]:
        tool_name = "explain_analyze" if analyze else "explain"
        started = time.perf_counter()
        query, error = self._validated(sql)
        if error:
            self._emit(tool_name, started, False, validation_error=True)
            return error
        prefix = (
            "EXPLAIN (ANALYZE TRUE, BUFFERS TRUE, WAL FALSE, FORMAT JSON) "
            if analyze
            else "EXPLAIN (COSTS TRUE, VERBOSE FALSE, FORMAT JSON) "
        )
        try:
            with self._readonly_connection() as connection:
                raw = connection.execute(prefix + query).fetchone()[0]
            payload = raw[0] if isinstance(raw, list) else raw
            result = {"ok": True, "plan": _json_value(payload)}
            self._emit(tool_name, started, True)
            return result
        except Exception as exc:
            self._emit(tool_name, started, False)
            return {"ok": False, "error": _error_message(exc)}

    def execute(self, sql: str) -> dict[str, Any]:
        started = time.perf_counter()
        query, error = self._validated(sql)
        if error:
            self._emit("execute", started, False, validation_error=True)
            return error
        try:
            with self._readonly_connection() as connection:
                cursor = connection.execute(query)
                columns = [description.name for description in cursor.description or ()]
                rows = cursor.fetchmany(self.max_rows + 1)
            truncated = len(rows) > self.max_rows
            visible_rows = rows[: self.max_rows]
            result = {
                "ok": True,
                "columns": columns,
                "rows": _json_value(visible_rows),
                "returned_rows": len(visible_rows),
                "truncated": truncated,
            }
            self._emit("execute", started, True, returned_rows=len(visible_rows), truncated=truncated)
            return result
        except Exception as exc:
            self._emit("execute", started, False)
            return {"ok": False, "error": _error_message(exc)}

    @staticmethod
    def _time_query(connection: psycopg.Connection[Any], query: str) -> float:
        started = time.perf_counter()
        # Psycopg's regular cursor buffers the full result before fetchall().
        # Single-row streaming preserves row decoding and full-result timing
        # without growing Python memory with the result cardinality.
        with connection.cursor() as cursor:
            for _ in cursor.stream(query):
                pass
        return (time.perf_counter() - started) * 1000

    def benchmark(self, sql: str, n: int = 5, warmup: int = 1) -> dict[str, Any]:
        started = time.perf_counter()
        query, error = self._validated(sql)
        if error:
            self._emit("benchmark", started, False, validation_error=True)
            return error
        if not isinstance(n, int) or not 1 <= n <= 25:
            return {"ok": False, "error": "n must be an integer between 1 and 25"}
        if not isinstance(warmup, int) or not 0 <= warmup <= 5:
            return {"ok": False, "error": "warmup must be an integer between 0 and 5"}
        try:
            with self._readonly_connection() as connection:
                for _ in range(warmup):
                    self._time_query(connection, query)
                samples = [self._time_query(connection, query) for _ in range(n)]
            result = {
                "ok": True,
                "runs": n,
                "warmup_runs": warmup,
                "samples_ms": [round(sample, 3) for sample in samples],
                "median_ms": round(statistics.median(samples), 3),
                "min_ms": round(min(samples), 3),
                "max_ms": round(max(samples), 3),
            }
            self._emit("benchmark", started, True, runs=n)
            return result
        except Exception as exc:
            self._emit("benchmark", started, False)
            return {"ok": False, "error": _error_message(exc)}

    def _equivalent(
        self, connection: psycopg.Connection[Any], candidate_sql: str
    ) -> tuple[bool, str | None]:
        baseline_description = connection.execute(
            f"SELECT * FROM ({self.baseline_sql}) AS baseline_shape LIMIT 0"
        ).description
        candidate_description = connection.execute(
            f"SELECT * FROM ({candidate_sql}) AS candidate_shape LIMIT 0"
        ).description
        baseline_types = [column.type_code for column in baseline_description or ()]
        candidate_types = [column.type_code for column in candidate_description or ()]
        if len(baseline_types) != len(candidate_types):
            return False, (
                "result column count differs: "
                f"baseline={len(baseline_types)}, candidate={len(candidate_types)}"
            )
        if baseline_types != candidate_types:
            return False, "result column types differ from baseline"

        comparison = f"""
            SELECT
                EXISTS (({self.baseline_sql}) EXCEPT ALL ({candidate_sql})),
                EXISTS (({candidate_sql}) EXCEPT ALL ({self.baseline_sql}))
        """
        try:
            baseline_extra, candidate_extra = connection.execute(comparison).fetchone()
            if baseline_extra or candidate_extra:
                directions = []
                if baseline_extra:
                    directions.append("baseline has rows missing from candidate")
                if candidate_extra:
                    directions.append("candidate has rows missing from baseline")
                return False, "; ".join(directions)
            return True, None
        except Exception as exc:
            return False, _error_message(exc)

    def submit_candidate(self, sql: str, n: int = 5, warmup: int = 1) -> dict[str, Any]:
        """Check exact multiset equivalence, then run a paired timing comparison."""

        started = time.perf_counter()
        query, error = self._validated(sql)
        if error:
            result = {
                "ok": True,
                "correct": False,
                "error": error["error"],
                "validation_error": True,
            }
            self._emit("submit_candidate", started, True, correct=False)
            return result
        if not isinstance(n, int) or not 1 <= n <= 25:
            return {"ok": False, "correct": False, "error": "n must be between 1 and 25"}
        if not isinstance(warmup, int) or not 0 <= warmup <= 5:
            return {"ok": False, "correct": False, "error": "warmup must be between 0 and 5"}

        try:
            with self._readonly_connection() as connection:
                equivalent, reason = self._equivalent(connection, query)
                if not equivalent:
                    result = {"ok": True, "correct": False, "error": reason}
                    self._emit("submit_candidate", started, True, correct=False)
                    return result

                for _ in range(warmup):
                    self._time_query(connection, self.baseline_sql)
                    self._time_query(connection, query)

                baseline_samples: list[float] = []
                candidate_samples: list[float] = []
                for iteration in range(n):
                    # AB/BA alternation reduces systematic cache and order bias.
                    if iteration % 2 == 0:
                        baseline_samples.append(self._time_query(connection, self.baseline_sql))
                        candidate_samples.append(self._time_query(connection, query))
                    else:
                        candidate_samples.append(self._time_query(connection, query))
                        baseline_samples.append(self._time_query(connection, self.baseline_sql))

            baseline_median = statistics.median(baseline_samples)
            candidate_median = statistics.median(candidate_samples)
            speedup = baseline_median / candidate_median if candidate_median > 0 else None
            result = {
                "ok": True,
                "correct": True,
                "measurement_scope": "same_database_current_physical_state",
                "provisional": self._indexes_enabled,
                "runs": n,
                "warmup_runs": warmup,
                "baseline_samples_ms": [round(value, 3) for value in baseline_samples],
                "candidate_samples_ms": [round(value, 3) for value in candidate_samples],
                "baseline_median_ms": round(baseline_median, 3),
                "candidate_median_ms": round(candidate_median, 3),
                "speedup": round(speedup, 4) if speedup is not None else None,
            }
            self._emit("submit_candidate", started, True, correct=True, runs=n)
            return result
        except Exception as exc:
            result = {"ok": False, "correct": False, "error": _error_message(exc)}
            self._emit("submit_candidate", started, False, correct=False)
            return result


_DEFAULT: PostgresTools | None = None


def _default() -> PostgresTools:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = PostgresTools()
    return _DEFAULT


def get_schema() -> dict[str, Any]:
    return _default().get_schema()


def get_indexes() -> dict[str, Any]:
    return _default().get_indexes()


def get_statistics() -> dict[str, Any]:
    return _default().get_statistics()


def get_capabilities() -> dict[str, Any]:
    return _default().get_capabilities()


def explain(sql: str) -> dict[str, Any]:
    return _default().explain(sql)


def explain_analyze(sql: str) -> dict[str, Any]:
    return _default().explain_analyze(sql)


def execute(sql: str) -> dict[str, Any]:
    return _default().execute(sql)


def benchmark(sql: str, n: int = 5, warmup: int = 1) -> dict[str, Any]:
    return _default().benchmark(sql, n=n, warmup=warmup)


def submit_candidate(sql: str, n: int = 5, warmup: int = 1) -> dict[str, Any]:
    return _default().submit_candidate(sql, n=n, warmup=warmup)
