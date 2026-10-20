"""The rows (step X-cx's K1b-1): every stored row of the dump, once base's deploy step and
once cut's has run on it, compared row by row, the planted controls run every time
(``design.md`` s.3 items 2, 3, 8 and 10).

THE DEPLOY AND THE BOOT run through :mod:`.probes`, which says how a side's step runs and in
what environment; THE ROWS are read and compared by :mod:`.rowdiff`, which says how every
table, row and stamp is read.

BASE IS READ ONCE (``balance:R-BAL336``, which removed the second read, base deployed again,
and its noise gate).  A column the server's clock writes that the catalogs do not call a stamp
(one an UPDATE alone sets to the clock, with no clock default: :func:`.rowdiff.stamps`) is a
difference wherever a deploy writes it (exit 1), never an exit 0.

THE PLANTED CONTROLS, every run, each on a clone of base's deployed copy, nothing else
touched (triggers and foreign-key checks are off for the plant): a cent added to a numeric
column, a byte changed in a text column, a row removed, and a stamp moved to a moment earlier
than both itself and the line.  The comparison must find each exactly.  Then the fifth: the
clock written into the same stamp on two clones in turn, which the comparison of the two must
NOT find.  Any control that fails means the run measured nothing (exit 2).

THE ROLLBACK runs exactly when production's would once cut's deploy has stored its copy: when
base's tree carries a migration script for every revision that copy is stamped at
(:func:`base_resolves`, which mirrors ``deploy/shekel-deploy.sh``'s ``image_resolves_revisions``).
Then, in entrypoint order, base's deploy step runs again on a clone of cut's deployed copy and
must exit 0 and move no row, and base's app must boot there (``side/boot.py``).  When base's
tree does not know the revision, that script would refuse the rollback and name the dump to
restore, and the run says so: the copy's migration version is already a row that differs.  When
cut's deploy refused, its one transaction left the dump as it was, so production's rollback is
base's own deploy on it, which the run has already measured (the boot aside), and none runs.

THE PAGES, last (:mod:`.pages`, ruling W2): each deployed copy is cloned and crawled by its
own side's app as each of its active users, and the rows after the crawl are compared, as the
deploys' are, less what the deploys' comparison already reported.

What it does NOT compare is printed on every run (:data:`NOT_COMPARED`, and the crawl's
:data:`.pages.LIMITER`), the audit log's dropped columns and the stamps' one hole among it,
and with it the limit the days put on a run (:func:`.rowdiff.day_limit`), so an exit 0 is never
read as a claim about them.

Usage, which brings a bench up (:mod:`.bench`), deploys, compares and tears it down::

    python -m tools.rehearsal.rows --dump <pg_dump -Fc file> --base <revision> \\
        --cut <revision> --report <file>

Exit status: 0 no row differs, before or after the crawl, and the rollback holds; 1 rows
differ (each one in the report, summed per table and column on stdout), cut's deploy refused
the copy base's deployed, or the rollback to base on cut's deployed copy fails (named); 2
nothing was measured: the bench, the dump's stamps against the line
(:func:`.rowdiff.read_line`), base's deploy, a control, a crawl, or the run itself (a file it
could not write, a database error) failed, each named.  A failure of the run is never exit 1,
which says rows differ.
"""
from __future__ import annotations

import argparse
import secrets
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg import sql

from tools.rehearsal import bench, pages
from tools.rehearsal.probes import boot, deploy, pinned_instant
from tools.rehearsal.rowdiff import (AUDIT_LOG, DEPLOY, Held, Moved, Table, catalog,
                                     changed_columns, column_change, compare, day_limit,
                                     identifier, key_text, multisets, qualified, read_line,
                                     report, summary)

#: Columns a control may be planted in, in catalog order, none that a generation computes.
_PLANTABLE = """
SELECT table_schema, table_name, column_name FROM information_schema.columns
WHERE data_type = ANY(%s) AND is_generated = 'NEVER'
  AND (numeric_scale IS NULL OR numeric_scale >= 2)
ORDER BY table_schema, table_name, ordinal_position
"""
#: Where a side's tree holds its migration scripts, as its image does: ``shekel-deploy.sh``'s
#: ``IMAGE_MIGRATIONS_DIR`` default, relative to the image's working directory, which holds
#: the tree (the ``Dockerfile``'s ``COPY . .``).
_MIGRATIONS = Path("migrations") / "versions"
#: The cent the money control adds.
CENT = Decimal("0.01")
#: Printed on every run: what the comparison does not see (the coordinator's D3 ruling).
NOT_COMPARED = (
    "not compared: the audit log's id and db_user, the schema (tables, columns, "
    "constraints, indexes), privileges (the dump is "
    "restored without them and no shekel_app role exists, so the deploy's grants do nothing "
    "and the rollback's boot connects as the owner, where production's app connects as "
    "shekel_app), "
    "sequences and entrypoint step 2 (the role); "
    f"and a stamp later than the line reads as {DEPLOY!r} whatever wrote it, so two such "
    "values a deploy wrote by anything but its own clock (now() + interval) read alike")


class _NoPlant(bench.BenchError):
    """No row of the copy could take a planted control."""


def stamped_revisions(url: str) -> list[str]:
    """The migration versions the copy at ``url`` is stamped at (``public.alembic_version``),
    sorted."""
    with psycopg.connect(url) as connection:
        return sorted(revision for (revision,) in connection.execute(
            "SELECT version_num FROM public.alembic_version").fetchall())


def base_resolves(worktree: Path, revisions: list[str]) -> bool:
    """Whether the tree carries a migration script for every one of ``revisions``: a file of
    its :data:`_MIGRATIONS` named ``<revision>_*.py``.

    It MIRRORS ``deploy/shekel-deploy.sh``'s ``image_resolves_revisions`` over
    ``image_migration_revisions``' listing (the basenames of the image's
    ``migrations/versions/*.py``), the predicate that script's rollback is taken by (its
    ``repin_is_safe``): Alembic resolves a revision by finding the script whose id it is.  As
    there, a revision matches a name only up to its ``_``, and an empty list resolves; that
    script's ``repin_is_safe`` refuses an empty ``alembic_version`` before it asks, which the
    copy of a deploy that exited 0 never is (the step stamps it).
    """
    listing = [path.name for path in (worktree / _MIGRATIONS).glob("*.py")]
    return all(any(name.startswith(f"{revision}_") for name in listing)
               for revision in revisions)


def rollback(made: bench.Bench, secret_key: str, moment: datetime, line: datetime) -> bool:
    """Roll back to base on cut's deployed copy exactly when production's rollback would
    (:func:`base_resolves`), printing the verdict; False when the rollback fails.

    In entrypoint order: base's deploy step runs again on a clone of cut's deployed copy and
    must exit 0 (a refusal is production's container dying at step 3, so nothing boots) and
    move no row, compared with cut's deployed copy through ``line`` (:func:`compare`); then
    base's app must boot on the clone.

    Raises:
        bench.BenchError: when a side probe cannot be run.
        psycopg.Error: when a copy cannot be read or cloned.
    """
    base, cut = made.made["base"], made.made["cut"]
    revisions = stamped_revisions(cut.url)
    stamped = ", ".join(revisions)
    if not base_resolves(base.worktree, revisions):
        print(f"rollback: base's image does not know the migration version cut's copy is "
              f"stamped at ({stamped}), so shekel-deploy.sh would refuse the rollback and name "
              "the dump to restore: rolling back past this release is a restore")
        return True
    back = bench.State(base.name, base.commit, base.worktree, "rehearsal_rollback",
                       made.clone(cut.database, "rehearsal_rollback"), base.python)
    again = "base's deploy step, run again on cut's deployed copy,"
    refused = deploy(back, back.url, secret_key, moment)
    if refused is not None:
        print(f"ROLLBACK FAILS: {again} refused: {refused}")
        return False
    failures: list[str] = []
    moved = compare(cut.url, back.url, line)
    if moved:
        failures.append(f"ROLLBACK FAILS: {again} moved {len(moved)} rows:\n"
                        + "\n".join(summary(moved, "cut", "rolled back")))
    unbooted = boot(back, back.url, secret_key, moment)
    if unbooted is not None:
        failures.append(f"ROLLBACK FAILS: base cannot boot on cut's deployed copy: {unbooted}")
    for failure in failures:
        print(failure)
    if not failures:
        print(f"rollback: base's image knows the migration version cut's copy is stamped at "
              f"({stamped}); base boots on cut's deployed copy, and base's deploy step, run "
              "again on it, exits 0 and moves no row")
    return not failures


#: Builds a control's statement from its table, its column (None for a removal) and the
#: match of the row's key; the statement takes the key as ``%s``.
_Change = Callable[[sql.Identifier, sql.Identifier | None, sql.Composable], sql.Composable]


@dataclass(frozen=True)
class _Where:
    """Where a control may be planted: ``places`` gives each ``(table, column)`` of a copy in
    catalog order, from a connection to it and its catalog (the column empty for a removal),
    and ``what`` names them when no row can take it."""

    places: Callable[[psycopg.Connection, dict[Table, Held]], list[tuple[Table, str]]]
    what: str


def _typed(types: list[str]) -> _Where:
    """Every column of one of ``types`` (:data:`_PLANTABLE`)."""
    return _Where(lambda connection, _held: [
        ((schema, name), column)
        for schema, name, column in connection.execute(_PLANTABLE, [types]).fetchall()],
        f"a column of {types}")


#: Every table, for a removal.
_ANY_ROW = _Where(lambda _connection, held: [(table, "") for table in held], "a column of any")
#: Every stamp (:class:`Held`).
_A_STAMP = _Where(lambda _connection, held: [
    (table, stamp) for table, shape in held.items() for stamp in shape.stamps], "a stamp")


def _plant(url: str, where: _Where, change: _Change) -> tuple[str, str, str]:
    """Plant a control in the first row that takes it, of the first keyed table in catalog
    order with a column ``where`` names.  Triggers and foreign-key checks are off for the
    session, so the planted row is the only one that moves; a row a constraint refuses is
    passed over for the next.  Its table, key and column (empty for a removal).

    Raises:
        _NoPlant: when no row can take it.
    """
    with psycopg.connect(url) as connection:
        connection.execute("SET session_replication_role = replica")
        held = catalog(connection)
        for table, column in where.places(connection, held):
            columns = held[table].key if table in held else ()
            if not columns or table == AUDIT_LOG or column in columns:
                continue
            target = sql.Identifier(column) if column else None
            where = (sql.SQL("WHERE x.{c} IS NOT NULL AND x.{c}::text <> ''").format(c=target)
                     if target else sql.SQL(""))
            first = connection.execute(sql.SQL(
                "SELECT {key} FROM {table} x {where} ORDER BY {key} LIMIT 1").format(
                    key=key_text(columns), table=identifier(table), where=where)).fetchone()
            if first is None:
                continue
            try:
                with connection.transaction():
                    connection.execute(change(identifier(table), target, key_text(columns)),
                                       [first[0]])
            except (psycopg.errors.IntegrityError, psycopg.errors.DataError):
                continue
            return qualified(table), first[0], column
    raise _NoPlant(f"no row of the copy could take a control in {where.what}")


def plant_cent(url: str) -> tuple[str, str, str]:
    """Add :data:`CENT` to the first numeric column that takes it (:func:`_plant`)."""
    return _plant(url, _typed(["numeric"]), lambda table, column, key: sql.SQL(
        "UPDATE {table} AS x SET {c} = x.{c} + {cent} WHERE {key} = %s").format(
            table=table, c=column, cent=sql.Literal(CENT), key=key))


def plant_byte(url: str) -> tuple[str, str, str]:
    """Change the last character of the first text column that takes it (:func:`_plant`),
    its length kept so a length limit cannot refuse it."""
    return _plant(url, _typed(["text", "character varying"]), lambda table, column, key: sql.SQL(
        "UPDATE {table} AS x SET {c} = left(x.{c}, -1) || CASE WHEN right(x.{c}, 1) = '#' "
        "THEN '*' ELSE '#' END WHERE {key} = %s").format(table=table, c=column, key=key))


def plant_removal(url: str) -> tuple[str, str, str]:
    """Remove the first row of the first keyed table that has one (:func:`_plant`)."""
    return _plant(url, _ANY_ROW, lambda table, _column, key: sql.SQL(
        "DELETE FROM {table} AS x WHERE {key} = %s").format(table=table, key=key))


def plant_earlier(url: str, line: datetime) -> tuple[str, str, str]:
    """Move the first stamp that takes it to a microsecond before both itself and ``line``
    (:func:`_plant`): a stamp a deploy wrote after the line is moved back across it, so the
    moved stamp is read exactly whoever wrote it."""
    return _plant(url, _A_STAMP, lambda table, column, key: sql.SQL(
        "UPDATE {table} AS x SET {c} = least(x.{c}, {line}) - interval '1 microsecond' "
        "WHERE {key} = %s").format(table=table, c=column, line=sql.Literal(line), key=key))


def plant_now(url: str) -> tuple[str, str, str]:
    """Write the server's clock into the first stamp that takes it (:func:`_plant`), as a
    deploy's own transaction would."""
    return _plant(url, _A_STAMP, lambda table, column, key: sql.SQL(
        "UPDATE {table} AS x SET {c} = now() WHERE {key} = %s").format(
            table=table, c=column, key=key))


def _stored(url: str, planted: tuple[str, str, str]) -> object:
    """The planted stamp's value as the copy at ``url`` stores it, read raw."""
    name, key, column = planted
    schema, relation = name.split(".", 1)
    with psycopg.connect(url) as connection:
        columns = catalog(connection)[(schema, relation)].key
        row = connection.execute(sql.SQL("SELECT x.{c} FROM {table} x WHERE {key} = %s").format(
            c=sql.Identifier(column), table=identifier((schema, relation)),
            key=key_text(columns)), [key]).fetchone()
    return None if row is None else row[0]


def found(moved: list[Moved], planted: tuple[str, str, str], removed: bool = False) -> bool:
    """Whether a comparison found exactly one planted control: the one row, in its table at
    its key, gone (``removed``) or changed in its column and no other."""
    if len(moved) != 1:
        return False
    row, (table, key, column) = moved[0], planted
    if (row.table, row.key) != (table, key):
        return False
    if removed:
        return row.before is not None and row.after is None
    return changed_columns(row) == [column]


#: Each control found exactly: its name, its plant (given the line), whether it removes a
#: row, and what else must hold of the one row found (the cent's amount).
_CONTROLS: tuple[tuple[str, Callable[[str, datetime], tuple[str, str, str]], bool,
                       Callable[[Moved, str], bool]], ...] = (
    ("a cent", lambda url, _line: plant_cent(url), False,
     lambda row, column: column_change(row, column) == CENT),
    ("a byte", lambda url, _line: plant_byte(url), False, lambda _row, _column: True),
    ("a removed row", lambda url, _line: plant_removal(url), True, lambda _row, _column: True),
    ("a stamp moved earlier", plant_earlier, False, lambda _row, _column: True),
)
#: The control that must NOT be found (:func:`rewritten_twice`).
_TWICE = "a stamp the clock wrote on two clones in turn"


def rewritten_twice(made: bench.Bench, base: bench.State, line: datetime) -> str | None:
    """The fifth control: on each of two clones of base's deployed copy in turn, the server's
    clock written into the same stamp (:func:`plant_now`).  What is wrong with it; None when
    the two values differ, each later than ``line``, and the comparison of the two clones
    finds nothing, both stamps read as :data:`DEPLOY`.

    Raises:
        psycopg.Error: when a clone cannot be made or read.
    """
    urls = [made.clone(base.database, f"rehearsal_control_twice_{index}") for index in (0, 1)]
    try:
        planted = [plant_now(url) for url in urls]
    except _NoPlant as exc:
        return f"{_TWICE}: {exc}"
    written = [_stored(url, planted[0]) for url in urls]
    if (planted[0] != planted[1] or written[0] == written[1]
            or not all(isinstance(value, datetime) and value > line for value in written)):
        return (f"{_TWICE} planted at {' and '.join(' '.join(place) for place in planted)} "
                f"wrote {written[0]} and {written[1]}, not two moments each later than the "
                f"line {line.isoformat()}, so it measures nothing")
    moved = compare(urls[0], urls[1], line)
    if moved:
        return (f"{_TWICE} planted at {' '.join(planted[0])} was found; the comparison found "
                f"{len(moved)} rows: " + "; ".join(summary(moved, "one", "the other")))
    return None


def controls(made: bench.Bench, base: bench.State, line: datetime) -> list[str]:
    """Plant each control on its own clone of base's deployed copy and compare it with that
    copy through ``line``, then the fifth (:func:`rewritten_twice`); what is wrong with any,
    each named (none when each was found exactly and the fifth was not found)."""
    wrong: list[str] = []
    for index, (label, plant, removed, holds) in enumerate(_CONTROLS):
        url = made.clone(base.database, f"rehearsal_control_{index}")
        try:
            planted = plant(url, line)
        except _NoPlant as exc:
            wrong.append(f"{label}: {exc}")
            continue
        moved = compare(base.url, url, line)
        if not (found(moved, planted, removed) and holds(moved[0], planted[2])):
            wrong.append(f"{label} planted at {planted[0]} {planted[1]} {planted[2]} was not "
                         f"found exactly; the comparison found {len(moved)} rows: "
                         + "; ".join(summary(moved, "base", "the control")))
    twice = rewritten_twice(made, base, line)
    if twice is not None:
        wrong.append(twice)
    return wrong


def gates(made: bench.Bench, line: datetime) -> bool:
    """The planted controls, each through ``line`` and printed; False when any fails, which
    is the kit's failure, named on stderr, never the release's."""
    wrong = controls(made, made.made["base"], line)
    if wrong:
        print("[rehearsal] a planted control was not read as planted, so the comparison "
              "measures nothing:\n" + "\n".join(wrong), file=sys.stderr)
        return False
    print(f"controls: {', '.join(label for label, *_ in _CONTROLS)}, each found exactly; "
          f"{_TWICE}, not found")
    return True


def rehearse(made: bench.Bench, out: Path) -> int:
    """Read the line, deploy base and cut on the bench, gate on the controls, compare base
    with cut, writing every differing row to ``out``, roll back to base on cut's deployed copy
    (:func:`rollback`), then crawl the two deployed copies and compare the rows after the crawl
    (:mod:`.pages`), adding each new differing row to ``out``; the exit status.

    Raises:
        bench.BenchError: when the dump holds a stamp later than the line (:func:`read_line`)
            or a side probe cannot be run; :class:`.pages.Unmeasured` when a crawl refused.
        psycopg.Error: when a copy cannot be read or cloned.
    """
    base, cut = made.made["base"], made.made["cut"]
    secret_key = secrets.token_hex(32)
    moment = pinned_instant(made)
    print(f"clock pinned at {moment.isoformat()}")
    line = read_line(base.url)
    print(f"line: {line.isoformat()}, the cluster's clock just before the first deploy; a "
          f"stamp later than it reads as {DEPLOY!r}")
    refused: dict[str, str | None] = {}
    for label, state in (("base", base), ("cut", cut)):
        began = time.monotonic()
        refused[label] = deploy(state, state.url, secret_key, moment)
        print(f"deploy {label}: {time.monotonic() - began:.2f} s, "
              + ("exit 0" if refused[label] is None else "REFUSED"))
    if refused["base"] is not None:
        print(f"[rehearsal] base's deploy refused the dump, so nothing is compared "
              f"(an app defect): {refused['base']}", file=sys.stderr)
        return 2
    if not gates(made, line):
        return 2
    print(NOT_COMPARED)
    print(pages.LIMITER)
    print(day_limit(base.url, cut.url))
    print("compared as multisets: " + ", ".join(multisets(base.url, cut.url)))
    if refused["cut"] is not None:
        print(f"cut's deploy refused the copy base's deployed: {refused['cut']}")
        return 1
    began = time.monotonic()
    moved = compare(base.url, cut.url, line)
    print(f"base against cut: {time.monotonic() - began:.2f} s, {len(moved)} rows differ "
          f"(each in {out})")
    _reported(moved, ("base", "cut"), out, "w")
    held = rollback(made, secret_key, moment, line)
    after = pages.after_the_crawl(pages.crawl(made, "base", base, secret_key, moment),
                                  pages.crawl(made, "cut", cut, secret_key, moment), moved, line)
    print(f"base crawled against cut crawled: {len(after)} rows differ beyond the deploys' "
          f"(each in {out})")
    _reported(after, ("base, crawled", "cut, crawled"), out, "a")
    return 1 if moved or not held or after else 0


def _reported(moved: list[Moved], sides: tuple[str, str], out: Path, mode: str) -> None:
    """Write each differing row to ``out``, opened in ``mode``, and print them summed per
    table and column (:func:`.rowdiff.report`, :func:`.rowdiff.summary`)."""
    with out.open(mode, encoding="utf-8") as written:
        written.writelines(f"{entry}\n" for entry in report(moved, *sides))
    for entry in summary(moved, *sides):
        print(entry)


def main(argv: list[str] | None = None) -> int:
    """Bring a bench up on a dump with states ``base`` and ``cut``, rehearse, tear it down."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dump", type=Path, required=True, help="a pg_dump -Fc archive")
    parser.add_argument("--base", required=True, help="the revision production runs")
    parser.add_argument("--cut", required=True, help="the revision to release")
    parser.add_argument("--report", type=Path, required=True,
                        help="where every differing row is written (never into git)")
    args = parser.parse_args(argv)
    try:
        made = bench.Bench(args.dump, {"base": args.base, "cut": args.cut})
        with made:
            status = rehearse(made, args.report)
    except (bench.BenchError, OSError, psycopg.Error) as exc:
        print(f"[rehearsal] {exc}", file=sys.stderr)
        return 2
    for part, seconds in made.timings.items():
        print(f"{part}: {seconds:.2f} s")
    return status


if __name__ == "__main__":
    sys.exit(main())
