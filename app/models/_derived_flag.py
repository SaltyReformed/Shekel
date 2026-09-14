"""What a per-row DERIVATION answers at class level: a refusal to key a query.

:class:`DerivedFlag` and the :class:`NotAQueryExpression` it answers, moved
here whole from :mod:`app.models.transaction` at plan step ``balance:X-bi-7a``
so that the DEFINITION's model can carry one too: ``TransactionTemplate.recurs``
(the definition has a rule) is the same kind of thing as the row's sealed flags
-- a fact read off ANOTHER table's row, which a query over this table's class
cannot express without stating the rule a second time in SQL (ruling **R-IZ**).
Both models import it; the descriptor is unchanged but for the refusal's
sentence, which now names the definition's case beside the row's.
"""


class NotAQueryExpression:
    """What a :class:`DerivedFlag` answers at CLASS level: a refusal.

    Every comparison, comparator lookup, truth test and SQL coercion
    raises, so ``filter_by(flag=True)``, ``Transaction.flag == True``,
    ``Transaction.flag.is_(True)``, a bare ``where(Transaction.flag)`` and
    an ORM-enabled ``insert(Transaction).values(flag=...)`` all fail to
    BUILD rather than silently matching nothing.  ``hasattr`` still answers
    ``True`` -- the declarative constructor asks it before
    ``Transaction(flag=...)`` may reach the setter -- because merely
    reaching the name is not the misuse; and a DUNDER lookup gets the
    ordinary ``AttributeError``, so ``copy``, ``pickle`` and ``pydoc`` see
    an object rather than a refusal.  What passes through is a STRING that
    names the column -- ``order_by("is_envelope")``, ``text(...)`` -- which
    is the raw-SQL boundary the column comment states.
    """

    __slots__ = ("_name",)

    def __init__(self, name):
        """Remember which flag this stands for, so the refusal can name it."""
        self._name = name

    def _refuse(self, *_args, **_kwargs):
        """Raise the one refusal; bound below to every operator a query uses."""
        raise TypeError(
            f"{self._name} is a per-row derivation, not a column, so it "
            "cannot key a query: its answer is read off another table's row "
            "-- a generated row's flag is its template's, a definition's "
            "cadence is its rule's.  Load the rows and ask each one."
        )

    __eq__ = _refuse
    __ne__ = _refuse
    __bool__ = _refuse
    __hash__ = None
    # SQLAlchemy's coercion asks for this before anything else, so a bare
    # ``where(Transaction.flag)`` reaches the refusal by name rather than
    # the generic "SQL expression element expected".
    __clause_element__ = _refuse

    def __getattr__(self, attr):
        """Refuse ``.is_()``, ``.in_()`` and every other comparator lookup.

        A dunder -- ``__deepcopy__``, ``__reduce_ex__``, ``__wrapped__`` --
        and the slot itself get the ordinary ``AttributeError``: the first
        so protocol probes behave, the second so an instance built by
        ``__new__`` alone (what ``copy`` and ``pickle`` make) cannot recurse
        through this method looking for the name it has not been given.
        """
        if attr == "_name" or attr.startswith("__"):
            raise AttributeError(attr)
        self._refuse()


class DerivedFlag(property):
    """A per-row derivation whose class-level name is NOT a query expression.

    A plain ``property`` reached at class level is a ``property`` object,
    and that object compares ``False`` to everything: measured 2026-09-11,
    ``Transaction.is_envelope == True`` was ``False`` and
    ``query.filter_by(is_envelope=True)`` returned no rows with no error --
    a silent wrong answer, which is worse than the wrong column it replaced.
    This subclass answers a :class:`NotAQueryExpression` at class level and
    is otherwise a ``property``: ``setter`` works, a flag declared without
    one refuses assignment, and instance reads run the derivation.

    A ``hybrid_property`` was rejected: giving the derivation a SQL body
    would be a second spelling of one rule (ruling **R-IZ**), and no query
    keys on any of these flags or is planned to.
    """

    def __init__(self, fget=None, fset=None, fdel=None, doc=None):
        # The same four positionals ``property`` takes, because
        # ``property.setter`` rebuilds the descriptor as ``type(self)(...)``
        # and this subclass has to survive that round trip.
        super().__init__(fget, fset, fdel, doc)
        self._qualname = fget.__qualname__

    def __get__(self, obj, owner=None):
        if obj is None:
            return NotAQueryExpression(self._qualname)
        return super().__get__(obj, owner)
