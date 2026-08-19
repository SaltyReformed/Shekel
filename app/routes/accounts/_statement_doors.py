"""One shape for every statement write door: act, commit, or say what stopped it.

The three doors the statement pages own -- import a file, accept a match,
release one -- share their whole failure story and differ only in the act and
in what to say when it worked.  Writing that story per door is what pylint's
cross-file ``duplicate-code`` reported when the second one landed, and the
report was right: three copies of a rollback-and-flash are three places for a
refusal to stop being rendered.

**The story, once:**

* the act runs and the request commits, so the unit of work is the request and
  a refusal leaves nothing behind -- which is what makes "nothing was changed",
  the phrase every refusal message here ends with, true rather than reassuring;
* a DOMAIN refusal is the user's own sentence, flashed as it was written;
* a DATABASE error is not, so it goes to
  :func:`~app.routes._commit_helpers.handle_db_error`, which logs the detail
  and shows the user a sentence that does not name a table;
* success flashes what the door decided to say, AFTER the commit -- a business
  event or a message asserting that money moved must not appear when the
  transaction that would have moved it failed.

It lives in the accounts package rather than in ``_commit_helpers`` because the
refusal TYPE is a parameter here and that is a statement-door concern: the
import door refuses with ``StatementImportError`` and the match doors with
``ValidationError``, and generalising the helper any further would make it a
second spelling of ``try``.
"""

import logging
from dataclasses import dataclass
from typing import Callable

from flask import Response, flash, redirect
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db
from app.routes._commit_helpers import DbErrorContext, handle_db_error


@dataclass(frozen=True)
class StatementDoorContext:
    """What one statement door needs in order to fail well.

    Attributes:
        logger: The calling module's logger, so a database error is logged
            under the module that owns the door rather than under this one.
        refusal: The exception class this door's service raises for a DESIGNED
            refusal -- a sentence written for the person who submitted the
            form.  Anything else propagates.
        log_message: The ``%``-style message for a database error.
        log_args: Its arguments.
        flash_message: What to tell the user when the database refused.  It
            ends with "Nothing was changed" for the reason the module docstring
            gives.
        target: Where to send the user afterwards, whatever happened.  ONE
            destination for all three outcomes, because a door that redirected
            somewhere else on failure would lose the flash it just set.
    """

    logger: logging.Logger
    refusal: type
    log_message: str
    log_args: tuple
    flash_message: str
    target: str


def run_statement_door(
    ctx: StatementDoorContext,
    act: Callable,
    on_success: Callable,
) -> Response:
    """Run *act*, commit, and turn every outcome into a redirect to the target.

    Args:
        ctx: What this door needs in order to fail well.
        act: The service call, taking no arguments and returning whatever the
            door wants to report.  It MUST NOT commit -- this function owns the
            unit of work, which is what makes a refusal leave nothing behind.
        on_success: ``result -> (message, category)``.  Called AFTER the commit,
            so anything it emits -- a flash, a business log event -- asserts
            something that has actually happened.

    Returns:
        A redirect to ``ctx.target``, with exactly one flash set.
    """
    try:
        result = act()
        db.session.commit()
    except ctx.refusal as exc:
        db.session.rollback()
        flash(str(exc), "danger")
        return redirect(ctx.target)
    except SQLAlchemyError:
        return handle_db_error(DbErrorContext(
            logger=ctx.logger,
            log_message=ctx.log_message,
            log_args=ctx.log_args,
            flash_message=ctx.flash_message,
            redirect=ctx.target,
        ))
    message, category = on_success(result)
    flash(message, category)
    return redirect(ctx.target)
