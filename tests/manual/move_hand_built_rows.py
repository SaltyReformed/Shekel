"""Rewrite link-less ``Transaction(...)`` constructions onto ``one_off_row_of``.

Plan step **balance:X-bi-7c**'s other instrument (the census is
``census_hand_built_rows.py``): the mechanical half of moving a hand-built
row onto the producer, so leaves 7c-2..n rewrite the same way and what is
left to judge is each site's SEMANTICS.  Run from the repository root::

    python tests/manual/move_hand_built_rows.py tests/path/test_x.py [...]

For every ``Transaction(...)`` whose keywords are all known and none of which
is a link (``template_id`` / ``transfer_id`` / ``credit_payback_for_id`` other
than ``None``), it writes ``one_off_row_of(period, name=, amount=, user_id=,
account_id=, scenario_id=, transaction_type_id=, [category_id=, is_envelope=,
companion_visible=, due_date=])`` and lays the rest on AFTER the placement --
a non-Projected ``status_id``, ``is_deleted``, ``notes``, the settle columns
(``**settle_day_columns(...)`` / ``**settlement_columns(...)`` become a
``setattr`` loop) -- then drops the ``db.session.add(row)`` that followed and
adds the helper import.  Anything else (a positional argument, an unknown
keyword, a ``pay_period_id`` that is not ``<row>.id``, a splat it cannot
read, an assignment to anything but a plain name, a construction that is not
its own statement -- ``return Transaction(...)``, a list element, an
``.append(...)``) is SKIPPED and printed with the reason, for the hand.

**Three keywords change MEANING on the producer's row, and the tool passes
them through regardless**: ``is_envelope`` and ``companion_visible`` land on
the DEFINITION (the row reads them from it, ruling R-BAL36), and
``due_date=None`` -- or no ``due_date`` at all -- becomes the paycheck's
START (R-BAL22), never an undated row.  A case that MEANS the row's own cell
or an undated row is a legacy-subject case and belongs on
``legacy_link_less_row_of``; the tool cannot know, so it moves the site and
the test's failure (or its silent tautology -- read the docstrings) says.
The helper import is added by RE-SORTING the whole existing
``from tests._test_helpers import (...)`` block, so unrelated import lines
move in the diff.

**It cannot classify a site, and never tries.**  A case whose SUBJECT is the
legacy link-less shape gets moved onto the producer like any other and then
FAILS -- which is the signal: the failure is read (legacy-subject ->
``legacy_link_less_row_of``; a reader of a column a derived row does not
carry; a real defect the cutover would arm) and never patched.  The comment a
constructor carried INSIDE its parentheses is dropped; the diff is read for
those.  Imports the move makes dead (``Transaction``, ``AmountOwnership``) and
LOCALS it makes dead (the ``projected = ...`` lookup that fed a dropped
``status_id=``; a ``txn = one_off_row_of(...)`` nothing reads) are left for the
hand -- ``pylint --enable=unused-import,unused-variable`` over the files.
"""
import ast, re, sys, pathlib

LINKS = {"template_id", "transfer_id", "credit_payback_for_id"}
CORE = {"user_id", "pay_period_id", "scenario_id", "account_id", "name",
        "transaction_type_id", "category_id", "amount_ownership"}
PASSTHRU = {"is_envelope", "companion_visible", "due_date"}          # builder params
AFTER = {"status_id", "is_deleted", "notes", "settled_amount", "settled_basis_id",
         "settled_on", "settled_day_basis_id", "is_override", "reconciled_by_id"}
KNOWN = CORE | PASSTHRU | AFTER
#: The spellings of "Projected" a site passes as ``status_id`` that the tool
#: DROPS (the producer places Projected).  Exact, so ``not_projected_id`` or
#: any other spelling is laid on after the placement instead -- harmless when
#: it was Projected all along, correct when it was not.
PROJECTED = re.compile(
    r"^(?:projected(?:_status)?\.id|projected_id"
    r"|ref_cache\.status_id\(\s*StatusEnum\.PROJECTED\s*\))$"
)


def model_names(tree):
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.endswith("transaction"):
            for a in node.names:
                if a.name == "Transaction":
                    names.add(a.asname or "Transaction")
    return names


def rewrite_call(src, call, indent):
    """Return (new_call_text, after_assignments) or (None, reason)."""
    kws = {}
    splats = []
    for k in call.keywords:
        if k.arg is None:
            splats.append(ast.get_source_segment(src, k.value))
        else:
            kws[k.arg] = ast.get_source_segment(src, k.value)
    if call.args:
        return None, "positional args"
    if any(k in LINKS and kws[k] != "None" for k in kws):
        return None, "linked"
    unknown = set(kws) - KNOWN - LINKS
    if unknown:
        return None, f"unknown keywords {sorted(unknown)}"
    for sp in splats:
        if not sp.startswith("settle_day_columns(") and not sp.startswith("settlement_columns("):
            return None, f"splat {sp[:40]}"
    if "pay_period_id" not in kws or not kws["pay_period_id"].endswith(".id"):
        return None, "pay_period_id is not <row>.id"
    if "amount_ownership" not in kws:
        return None, "no amount_ownership"
    m = re.fullmatch(r"AmountOwnership\.own\((.*)\)", kws["amount_ownership"], re.S)
    if not m:
        return None, f"amount_ownership {kws['amount_ownership'][:40]}"
    amount = m.group(1).strip()
    period = kws["pay_period_id"][: -len(".id")]
    parts = [f"{period}", f"name={kws['name']}", f"amount={amount}"]
    for key in ("user_id", "account_id", "scenario_id", "transaction_type_id"):
        if key not in kws:
            return None, f"missing {key}"
        parts.append(f"{key}={kws[key]}")
    if "category_id" in kws and kws["category_id"] != "None":
        parts.append(f"category_id={kws['category_id']}")
    for key in ("is_envelope", "companion_visible", "due_date"):
        if key in kws and kws[key] != "None" and kws[key] != "False":
            parts.append(f"{key}={kws[key]}")
    after = []
    if "status_id" in kws and not PROJECTED.match(kws["status_id"].strip()):
        after.append(("status_id", kws["status_id"]))
    for key in ("is_deleted", "notes", "settled_amount", "settled_basis_id",
                "settled_on", "settled_day_basis_id", "is_override", "reconciled_by_id"):
        if key in kws and kws[key] not in ("None", "False"):
            after.append((key, kws[key]))
    for sp in splats:
        after.append(("**", sp))
    inner = ",\n".join(indent + "    " + p for p in parts)
    text = "one_off_row_of(\n" + inner + ",\n" + indent + ")"
    return text, after


def process(path):
    src = pathlib.Path(path).read_text()
    tree = ast.parse(src)
    names = model_names(tree)
    lines = src.splitlines(keepends=True)
    edits = []  # (start_line_idx, end_line_idx_exclusive, replacement_text)
    handled = set()
    # map statements: find Assign/Expr statements whose value is the call
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.Expr)):
            continue
        value = node.value
        var = None
        call = None
        wrapped_add = False
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id in names:
            call = value
            if isinstance(node, ast.Assign):
                if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                    var = node.targets[0].id
                else:
                    # ``self.txn = Transaction(...)`` or a tuple target: the
                    # rewrite would drop the assignment, so it is the hand's.
                    handled.add(id(call))
                    print(f"SKIPPED  {path}:{call.lineno}: assignment target is not a plain name")
                    continue
        elif (isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute)
              and value.func.attr == "add" and len(value.args) == 1
              and isinstance(value.args[0], ast.Call)
              and isinstance(value.args[0].func, ast.Name) and value.args[0].func.id in names):
            call = value.args[0]
            wrapped_add = True
        if call is None:
            continue
        handled.add(id(call))
        indent = re.match(r"\s*", lines[node.lineno - 1]).group(0)
        new, after = rewrite_call(src, call, indent)
        loc = f"{path}:{call.lineno}"
        if new is None:
            print(f"SKIPPED  {loc}: {after}")
            continue
        if var is None and after:
            var = "row"
        if var is not None:
            stmt = f"{indent}{var} = {new}\n"
        else:
            stmt = f"{indent}{new}\n"
        for key, val in after:
            if key == "**":
                stmt += f"{indent}for _column, _value in {val}.items():\n{indent}    setattr({var}, _column, _value)\n"
            else:
                stmt += f"{indent}{var}.{key} = {val}\n"
        start = node.lineno - 1
        end = node.end_lineno  # exclusive
        # swallow a following db.session.add(var) / db_session.add(var) line
        if var is not None and not wrapped_add:
            for j in range(end, min(end + 3, len(lines))):
                if re.fullmatch(rf"\s*(db\.session|db_session|_db\.session|session)\.add\({var}\)\s*", lines[j]):
                    lines[j] = ""
                    break
        edits.append((start, end, stmt))
        print(f"REWRITTEN {loc}" + (f" (+{len(after)} after)" if after else ""))
    # every construction the statement loop did not reach -- a ``return
    # Transaction(...)``, a list element, an ``.append(Transaction(...))`` --
    # is named, never silently left
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in names and id(node) not in handled):
            print(f"SKIPPED  {path}:{node.lineno}: not a statement-level construction")
    for start, end, stmt in sorted(edits, reverse=True):
        lines[start:end] = [stmt]
    out = "".join(lines)
    # imports
    if "one_off_row_of" not in src:
        m = re.search(r"from tests\._test_helpers import \(\n((?:    [A-Za-z_0-9]+,\n)+)\)", out)
        if m:
            names = [l.strip().rstrip(",") for l in m.group(1).splitlines()]
            names = sorted(set(names) | {"one_off_row_of"}, key=lambda n: (n.lower().lstrip("_"), n))
            block = "from tests._test_helpers import (\n" + "".join(f"    {n},\n" for n in names) + ")"
            out = out[:m.start()] + block + out[m.end():]
        else:
            m = re.search(r"^from tests\._test_helpers import (.+)$", out, re.M)
            if m:
                out = out[:m.start()] + f"from tests._test_helpers import {m.group(1)}, one_off_row_of" + out[m.end():]
            else:
                print(f"NOTE {path}: add `from tests._test_helpers import one_off_row_of` by hand")
    pathlib.Path(path).write_text(out)


if __name__ == "__main__":
    for f in sys.argv[1:]:
        process(f)
