"""The row grader (step X-cx's K1b-1; a module of its own since K1c-2): every row of two
databases compared, by key or as a multiset, with its summary and its report (``design.md``
s.3 items 2 and 9).

EVERY TABLE IN THE CATALOG, every row, every column.  A table with a primary key is compared
row by row by that key; one without, or one whose key is not the same on both sides, as a
multiset of its rows, and the report names it.  ``system.audit_log`` is a multiset of what each
event wrote and when: never its id or database user, since its ids come from a sequence every
deploy advances.  Each row is hashed in SQL; only a row that differs is fetched whole.

THE STAMPS ARE COMPARED, BUT FOR THE ONES A DEPLOY WROTE (``balance:R-BAL331``).  A stamp is a
timestamp whose default is a read of the server's clock and nothing else, as both sides'
catalogs say (:func:`stamps`); PostgreSQL's clock writes it, which no pin reaches, so two
deploys run at two moments write two values.  The cluster's clock is read once just before the
first deploy: THE LINE (:func:`read_line`).  A stamp later than the line reads as ``deploy``
(:class:`Reading`); every other stamp is compared exactly, the audit log's own clock and the
stamps in the rows its events wrote included.  The line is checked every run: a stamp of the
restored dump later than it stops the run, named.
"""
from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

import psycopg
from psycopg import sql

from tools.rehearsal import bench

#: A table's ``(schema, name)``.
Table = tuple[str, str]
#: A row as compared: a table's JSON object, or the audit log's array (:meth:`Reading.image`).
Image = dict[str, object] | list[object]
AUDIT_LOG: Table = ("system", "audit_log")
#: What a stamp later than the line reads as (:class:`Reading`).
DEPLOY = "deploy"
#: The reads of the server's clock a timestamp's default can be, as ``pg_get_expr`` prints
#: them.
_CLOCK = (r"now\(\)|clock_timestamp\(\)|statement_timestamp\(\)|transaction_timestamp\(\)"
          r"|(CURRENT_TIMESTAMP|LOCALTIMESTAMP)(\(\d+\))?")
#: A stamp's default: a clock read and nothing else, so ``now() + interval`` is not one.
_STAMP_DEFAULT = f"^({_CLOCK})$"
#: A day's default: the server's day, or a clock read a date column keeps the day of.
_DAY_DEFAULT = f"^(CURRENT_DATE|{_CLOCK})$"


def _filled(types: str, default: str) -> sql.Composable:
    """The columns of the catalog's table ``c`` of one of ``types`` (SQL ``regtype`` values)
    whose default as ``pg_get_expr`` prints it matches ``default``, in column order."""
    return sql.SQL(
        "ARRAY(SELECT a.attname::text FROM pg_attribute a "
        "JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum "
        "WHERE a.attrelid = c.oid AND NOT a.attisdropped AND a.atttypid IN ({types}) "
        "AND pg_get_expr(d.adbin, d.adrelid) ~ {default} ORDER BY a.attnum)").format(
            types=sql.SQL(types), default=sql.Literal(default))


#: Every table: the columns of its primary key in order (none for a table without one), its
#: stamps (a timestamp whose default is :data:`_STAMP_DEFAULT`) and its days (a date whose
#: default is :data:`_DAY_DEFAULT`).
_CATALOG = sql.SQL("""
SELECT n.nspname, c.relname,
       ARRAY(SELECT a.attname::text
             FROM pg_index i
             CROSS JOIN LATERAL unnest(i.indkey::int2[]) WITH ORDINALITY AS k(attnum, position)
             JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.attnum
             WHERE i.indrelid = c.oid AND i.indisprimary
             ORDER BY k.position),
       {stamps}, {days}
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'p') AND NOT c.relispartition
  AND n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname NOT LIKE 'pg\\_%'
ORDER BY 1, 2
""").format(stamps=_filled("'timestamptz'::regtype, 'timestamp'::regtype", _STAMP_DEFAULT),
            days=_filled("'date'::regtype", _DAY_DEFAULT))


@dataclass(frozen=True)
class Moved:
    """One row that differs: its table, its key (the primary key as JSON; empty for a
    multiset table's row), and its image on each side, None where it is absent."""

    table: str
    key: str
    before: Image | None
    after: Image | None


@dataclass(frozen=True)
class Held:
    """What one side's catalog says of a table (:data:`_CATALOG`): the columns of its primary
    key in order (none without one), its stamps and its days."""

    key: tuple[str, ...]
    stamps: tuple[str, ...] = ()
    days: tuple[str, ...] = ()


@dataclass(frozen=True)
class Reading:
    """How a comparison reads the rows: each table's stamps (:func:`stamps`) and the line
    (:func:`read_line`).  A stamp later than the line reads as :data:`DEPLOY`; every other
    stamp, and every other column, is read exactly."""

    stamps: dict[Table, tuple[str, ...]]
    line: datetime

    def later(self, document: sql.Composable) -> sql.Composable:
        """Whether the stamp named ``k`` of the JSON object ``document`` is later than the
        line: THE one spelling of the rule, which the image and the dump's check both read."""
        return sql.SQL("({} ->> k)::timestamptz > {}").format(document, sql.Literal(self.line))

    def parts(self, table: Table) -> list[tuple[str, sql.Composable, sql.Composable]]:
        """What of a row of ``table`` (aliased ``x``) holds stamps, each as the prefix its
        stamps are named by, a JSON object, and its stamps' names (a ``text[]``): the row
        itself, when it has stamps; for the audit log, its own clock and the two images of
        the row each event wrote, whose stamps are those of the table the event names."""
        own = sql.SQL("{}::text[]").format(sql.Literal(list(self.stamps.get(table, ()))))
        if table != AUDIT_LOG:
            return [("", sql.SQL("to_jsonb(x)"), own)] if self.stamps.get(table) else []
        audited = sql.SQL(
            "ARRAY(SELECT jsonb_array_elements_text({}::jsonb -> "
            "(x.table_schema || '.' || x.table_name)))").format(sql.Literal(json.dumps(
                {qualified(stamped): list(names) for stamped, names in self.stamps.items()
                 if names})))
        return [("", sql.SQL("jsonb_build_object('executed_at', x.executed_at)"), own),
                ("old_data.", sql.SQL("x.old_data"), audited),
                ("new_data.", sql.SQL("x.new_data"), audited)]

    def image(self, table: Table) -> sql.Composable:
        """A row of ``table`` (aliased ``x``) as compared, each stamp later than the line read
        as :data:`DEPLOY`: the audit log's event (what it wrote and when, never its id or
        database user, as an array), or the row whole."""
        stamped = [sql.SQL(
            "({document} || coalesce((SELECT jsonb_object_agg(k, {deploy}::text) "
            "FROM unnest({names}) AS k WHERE {later}), '{{}}'::jsonb))").format(
                document=document, deploy=sql.Literal(DEPLOY), names=names,
                later=self.later(document))
            for _, document, names in self.parts(table)]
        if table == AUDIT_LOG:
            return sql.SQL(
                "jsonb_build_array(x.table_schema, x.table_name, x.operation, x.row_id, "
                "x.changed_fields, {}, {}, x.user_id, {})").format(
                    stamped[1], stamped[2], stamped[0])
        return stamped[0] if stamped else sql.SQL("to_jsonb(x)")


def qualified(table: Table) -> str:
    """``schema.name``."""
    return ".".join(table)


def identifier(table: Table) -> sql.Identifier:
    """The table as SQL."""
    return sql.Identifier(*table)


def key_text(columns: tuple[str, ...]) -> sql.Composable:
    """A row's primary key as the text of a JSON array, of ``columns`` of the row ``x``."""
    return sql.SQL("jsonb_build_array({})::text").format(
        sql.SQL(", ").join(sql.Identifier("x", column) for column in columns))


def catalog(connection: psycopg.Connection) -> dict[Table, Held]:
    """Every table of the database and what its catalog says of it (:class:`Held`)."""
    return {(schema, name): Held(tuple(key), tuple(stamped), tuple(dated))
            for schema, name, key, stamped, dated
            in connection.execute(_CATALOG).fetchall()}


def keys(before: dict[Table, Held], after: dict[Table, Held]) -> dict[Table, tuple[str, ...]]:
    """The key each table of either side is compared by: its primary key when both sides that
    hold it agree on one, else none (a multiset), the audit log always a multiset."""
    compared: dict[Table, tuple[str, ...]] = {}
    for table in sorted(before.keys() | after.keys()):
        held = {side[table].key for side in (before, after) if table in side}
        compared[table] = (held.pop() if len(held) == 1 and table != AUDIT_LOG else ())
    return compared


def stamps(before: dict[Table, Held], after: dict[Table, Held]
           ) -> dict[Table, tuple[str, ...]]:
    """The stamps each table of either side is read through: the columns every side that
    holds the table calls a stamp.  Where the two catalogs disagree on a column (a cut that
    adds or drops a clock default), it is compared exactly, since reading it as a stamp on
    both sides could hide a value one side wrote by other means, and comparing it exactly
    can only show one."""
    compared: dict[Table, tuple[str, ...]] = {}
    for table in sorted(before.keys() | after.keys()):
        held = [side[table].stamps for side in (before, after) if table in side]
        compared[table] = tuple(column for column in held[0]
                                if all(column in other for other in held[1:]))
    return compared


def days(before: dict[Table, Held], after: dict[Table, Held]) -> list[str]:
    """Every day either side holds, as ``schema.table.column``: a date the server's clock
    fills, which no pin reaches and no line can tell, so it is compared exactly
    (:func:`day_limit`)."""
    return sorted({f"{qualified(table)}.{column}" for side in (before, after)
                   for table, held in side.items() for column in held.days})


def read(connection: psycopg.Connection, present: dict[Table, Held],
         compared: dict[Table, tuple[str, ...]], reading: Reading
         ) -> dict[Table, Counter[tuple[str, str]]]:
    """Each ``present`` table's rows as ``(key, hash)`` pairs, counted: the key is empty where
    the table is compared as a multiset (``compared`` says), and the hash is the MD5 of the
    row's image (:meth:`Reading.image`)."""
    rows: dict[Table, Counter[tuple[str, str]]] = {}
    for table in present:
        columns = compared[table]
        if columns:
            query = sql.SQL("SELECT {}, md5(({})::text), 1 FROM {} x").format(
                key_text(columns), reading.image(table), identifier(table))
        else:
            query = sql.SQL("SELECT '', md5(({})::text), count(*) FROM {} x GROUP BY 2").format(
                reading.image(table), identifier(table))
        rows[table] = Counter({(key, digest): count
                               for key, digest, count in connection.execute(query).fetchall()})
    return rows


def _images(connection: psycopg.Connection, table: Table, columns: tuple[str, ...],
            wanted: list[str], reading: Reading) -> dict[str, Image]:
    """The images of ``table``'s rows, by key (``columns``) or, for a multiset, by hash, of
    those ``wanted``, read as :func:`read` hashed them."""
    if not wanted:
        return {}
    image = reading.image(table)
    by = key_text(columns) if columns else sql.SQL("md5(({})::text)").format(image)
    query = sql.SQL("SELECT DISTINCT ON (1) {by}, ({image})::text FROM {table} x "
                    "WHERE {by} = ANY(%s)").format(by=by, image=image,
                                                   table=identifier(table))
    return {found: json.loads(text, parse_float=Decimal)
            for found, text in connection.execute(query, [wanted]).fetchall()}


def compare(before_url: str, after_url: str, line: datetime) -> list[Moved]:
    """Every row that differs between two databases, table by table in catalog order, a
    stamp later than ``line`` read as :data:`DEPLOY` (:class:`Reading`)."""
    moved: list[Moved] = []
    with psycopg.connect(before_url) as before, psycopg.connect(after_url) as after:
        held = catalog(before), catalog(after)
        compared = keys(*held)
        reading = Reading(stamps(*held), line)
        rows = (read(before, held[0], compared, reading),
                read(after, held[1], compared, reading))
        for table, columns in compared.items():
            counted = rows[0].get(table, Counter()), rows[1].get(table, Counter())
            if counted[0] != counted[1]:
                moved.extend(_parted((before, after), table, columns, counted, reading))
    return moved


def _parted(sides: tuple[psycopg.Connection, psycopg.Connection], table: Table,
            columns: tuple[str, ...],
            counted: tuple[Counter[tuple[str, str]], Counter[tuple[str, str]]],
            reading: Reading) -> list[Moved]:
    """The rows of ``table`` that the two sides' reads (``counted``, before then after) do
    not share, fetched whole from their side: a keyed row on both sides once, with both
    images; a multiset's row once for each time its count differs."""
    gone, came = counted[0] - counted[1], counted[1] - counted[0]
    index = 0 if columns else 1
    was = _images(sides[0], table, columns, sorted({pair[index] for pair in gone}), reading)
    now = _images(sides[1], table, columns, sorted({pair[index] for pair in came}), reading)
    if columns:
        return [Moved(qualified(table), key, was.get(key), now.get(key))
                for key in sorted({key for key, _ in gone + came})]
    return ([Moved(qualified(table), "", was[digest], None)
             for (_, digest), times in sorted(gone.items()) for _ in range(times)]
            + [Moved(qualified(table), "", None, now[digest])
               for (_, digest), times in sorted(came.items()) for _ in range(times)])


def _held(before_url: str, after_url: str) -> tuple[dict[Table, Held], dict[Table, Held]]:
    """Each database's catalog (:func:`catalog`)."""
    with psycopg.connect(before_url) as before, psycopg.connect(after_url) as after:
        return catalog(before), catalog(after)


def multisets(before_url: str, after_url: str) -> list[str]:
    """The tables either database holds that are compared as multisets, named for the report."""
    return [qualified(table) for table, columns in keys(*_held(before_url, after_url)).items()
            if not columns]


def day_limit(before_url: str, after_url: str) -> str:
    """The limit the days either database holds (:func:`days`) put on a run, printed with
    :data:`NOT_COMPARED`."""
    named = ", ".join(days(*_held(before_url, after_url))) or "none"
    return (f"compared, though the server's clock fills them: the days {named}, each the "
            "server's real day where a deploy inserts a row without one, so deploys either "
            "side of midnight in the server's zone differ by it (a false difference, never a "
            "false pass)")


def after_line(connection: psycopg.Connection, reading: Reading) -> list[str]:
    """Each stamp of the database on ``connection`` holding a value later than the line, as
    ``schema.table.column`` (``system.audit_log.new_data.created_at`` for a stamp of the rows
    its events wrote), by the rule the image reads (:meth:`Reading.later`)."""
    late: list[str] = []
    for table in reading.stamps:
        for prefix, document, names in reading.parts(table):
            query = sql.SQL("SELECT DISTINCT k FROM {table} x CROSS JOIN LATERAL unnest({names}) "
                            "AS k WHERE {later} ORDER BY 1").format(
                                table=identifier(table), names=names,
                                later=reading.later(document))
            late.extend(f"{qualified(table)}.{prefix}{stamp}"
                        for (stamp,) in connection.execute(query).fetchall())
    return late


def read_line(url: str) -> datetime:
    """The line: the cluster's clock, read once on the copy at ``url``, base's before its
    deploy and so the restored dump, just before the first deploy; then no stamp of that copy
    may be later than it.

    Raises:
        bench.BenchError: when one is (a clock skewed between production and the bench, or
            data dated ahead), each named, since a stamp a deploy wrote could not be told
            from it.
        psycopg.Error: when the copy cannot be read.
    """
    with psycopg.connect(url) as connection:
        (line,) = connection.execute("SELECT clock_timestamp()").fetchone()
        held = catalog(connection)
        late = after_line(connection, Reading(stamps(held, held), line))
    if late:
        raise bench.BenchError(
            f"the dump holds stamps later than the cluster's clock ({line.isoformat()}) just "
            "before the first deploy, so a stamp a deploy wrote cannot be told from them: "
            + ", ".join(late))
    return line


def _sides(row: Moved) -> tuple[dict[str, object], dict[str, object]] | None:
    """A row's two images when it is on both sides as a table's row (never an audit event,
    which is an array), else None."""
    if isinstance(row.before, dict) and isinstance(row.after, dict):
        return row.before, row.after
    return None


def changed_columns(row: Moved) -> list[str]:
    """The columns whose value differs in a row on both sides (none for an audit event or a
    row on one side only); a column one side lacks differs."""
    sides = _sides(row)
    if sides is None:
        return []
    was, now = sides
    return sorted(column for column in was.keys() | now.keys()
                  if column not in was or column not in now or was[column] != now[column])


def _number(value: object) -> Decimal | None:
    """``value`` as a Decimal when it is a JSON number (never a boolean), else None."""
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        return None
    return Decimal(value)


def column_change(row: Moved, column: str) -> Decimal | None:
    """How far ``column`` moved in a row on both sides, when it is a number on both."""
    sides = _sides(row)
    if sides is None:
        return None
    was, now = (_number(image.get(column)) for image in sides)
    return None if was is None or now is None else now - was


def summary(moved: list[Moved], before: str, after: str) -> list[str]:
    """The differing rows summed per table and per column: how many rows, and the total
    change of a column whose every changed value is a number on both sides."""
    lines: list[str] = []
    for table in dict.fromkeys(row.table for row in moved):
        rows = [row for row in moved if row.table == table]
        only_before = sum(1 for row in rows if row.after is None)
        only_after = sum(1 for row in rows if row.before is None)
        lines.append(f"{table}: {len(rows)} rows differ ({only_before} only on {before}, "
                     f"{only_after} only on {after}, {len(rows) - only_before - only_after} "
                     "changed)")
        changes: dict[str, list[Decimal | None]] = {}
        for row in rows:
            for column in changed_columns(row):
                changes.setdefault(column, []).append(column_change(row, column))
        for column, deltas in changes.items():
            numbers = [delta for delta in deltas if delta is not None]
            total = (f", total change {sum(numbers, Decimal(0)):+}"
                     if len(numbers) == len(deltas) else "")
            lines.append(f"  {column}: {len(deltas)} rows{total}")
    return lines


def _shown(value: object) -> str:
    """A value as the report writes it: JSON, its Decimals as written."""
    return json.dumps(value, default=str, sort_keys=True)


def report(moved: list[Moved], before: str, after: str) -> Iterator[str]:
    """One line per differing row: its table and key, and the row where it is on one side
    only or is an audit event, else each changed column's value before and after."""
    for row in moved:
        where = f"{row.table} {row.key or '(no key)'}"
        sides = _sides(row)
        if row.after is None:
            yield f"{where}: only on {before}: {_shown(row.before)}"
        elif row.before is None or sides is None:
            yield f"{where}: only on {after}: {_shown(row.after)}"
        else:
            yield f"{where}: " + "; ".join(
                f"{column} {_shown(sides[0].get(column))} -> {_shown(sides[1].get(column))}"
                for column in changed_columns(row))
