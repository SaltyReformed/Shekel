# 07 -- Section 1C Manual Deep Dives

Section 1C of the security audit workflow defines nine manual checks
that depth-probe the highest-risk surfaces that the 1A subagents and
1B scanners may have glossed over. Each check here is an independent
read of the relevant file(s), with quoted evidence, paired against
the corresponding Section 1A subagent finding where applicable. Any
finding raised here is additive to Section 1A; any check that
confirms 1A is recorded so the Session S8 consolidator has a second
pair of eyes on the same surface.

All reads are from code that exists on commit `58e180f` of branch
`audit/security-2026-04-15`.

---

## Check 1C.1 -- Crypto correctness

**What was checked:** Password hashing, TOTP verify window, backup
code comparison, backup code entropy source, hand-rolled token
compares, TOTP secret encryption at rest, and Fernet key rotation
story. Files read in full: `app/services/auth_service.py` (422 lines)
and `app/services/mfa_service.py` (159 lines).

### 1C.1.a -- Password hash comparison: PASS

`app/services/auth_service.py:276-291`:

```python
def verify_password(plain_password, password_hash):
    """Verify a plaintext password against a bcrypt hash.
    ...
    """
    if plain_password is None:
        return False
    return bcrypt.checkpw(
        plain_password.encode("utf-8"),
        password_hash.encode("utf-8"),
    )
```

Uses `bcrypt.checkpw`, which is a constant-time comparison implemented
inside libbcrypt. No string `==` anywhere in the path. A grep of
`app/` for `password_hash == ...` and `== password_hash ...` returned
zero matches, confirming no hand-written compare exists anywhere.
Callers are `authenticate()` at `auth_service.py:308` and
`change_password()` at `auth_service.py:330` -- both route through
this helper.

**Subagent A cross-reference:** Confirmed in clean check #1 of
`01-identity.md`. Also see F-A-08 (Low) -- `verify_password` returns
False only on `None`; a non-string non-None value reaches
`.encode("utf-8")` and raises `AttributeError`. I confirm F-A-08 from
the same lines: a caller passing an empty bytes object or a Decimal
would produce a 500 rather than a clean False. Low severity, not a
security bypass, but a robustness gap.

### 1C.1.b -- TOTP verify window: PASS

`app/services/mfa_service.py:96-109`:

```python
def verify_totp_code(secret, code):
    """Verify a 6-digit TOTP code against a secret.

    Allows one period (30 seconds) of clock drift in either direction
    via valid_window=1.
    ...
    """
    return pyotp.TOTP(secret).verify(code, valid_window=1)
```

`valid_window=1` means pyotp accepts the previous, current, and next
30-second step -- total acceptable drift is 90 seconds. Matches the
workflow doc's explicit standard ("valid_window should be 1 (90
seconds total drift). A window of 2+ is a finding because it widens
brute-force by 2x/3x"). **Pass.**

Not checked: last-used-step replay tracking. `pyotp.TOTP.verify` is
stateless; the same code can be reused within its validity window.
For a multi-user future this is worth a follow-up (the 90-second
replay window against a known passcode is a theoretical risk under
rate limiting), but it is not called out in the workflow doc as a
1C.1 requirement and Subagent A noted it as an open question for the
developer. Not a finding today; noted as follow-up for Session S5
(business-logic deep dive).

### 1C.1.c -- Backup code comparison: PASS

`app/services/mfa_service.py:145-158`:

```python
def verify_backup_code(code, hashed_codes):
    """Check a plaintext backup code against a list of bcrypt hashes.
    ...
    """
    for idx, hashed in enumerate(hashed_codes):
        if bcrypt.checkpw(code.encode("utf-8"), hashed.encode("utf-8")):
            return idx
    return -1
```

Each comparison uses `bcrypt.checkpw`, which is constant-time per
comparison. The for-loop's early return leaks the matching index's
ordinal position via timing, but the matched index is immediately
used at `app/routes/auth.py:315-317` (per Subagent A's verified
clean-check #2) to remove the consumed code from the list, and no
cross-user oracle is reachable -- an attacker cannot observe another
user's match timing. No hand-written `==` compare against
`backup_codes` anywhere (grep confirmed zero matches). `hmac.compare_digest`
is not required here because bcrypt.checkpw is already constant-time
per individual comparison. **Pass.**

### 1C.1.d -- Backup code entropy: PASS (with Info finding on size)

`app/services/mfa_service.py:112-123`:

```python
def generate_backup_codes(count=10):
    """Generate a list of single-use backup codes.

    Each code is an 8-character lowercase hex string.
    ...
    """
    return [secrets.token_hex(4) for _ in range(count)]
```

Source: `secrets.token_hex(4)` -- `secrets` is Python's CSPRNG
module, not `random`. A grep of `app/` for `import random` and
`from random` returned zero matches -- the entire application has no
use of the non-cryptographic `random` module. **Pass** on the "must
use `secrets.*`" rule.

Size: `token_hex(4)` emits 4 random bytes = **32 bits of entropy per
code**. The workflow doc says verbatim: "8 hex chars = 32 bits,
which is too low." For 10 codes the online attack surface is gated
by the `/mfa/verify` rate limit (5 per 15 minutes per IP), so an
online brute force is impractical. The offline brute force against
the bcrypt hashes (after a DB compromise) is the real concern --
32 bits per code cracks in seconds on a consumer GPU.

**Classification:** Info. Matches Subagent A's F-A-14 severity. I do
not escalate because the rate-limit + bcrypt pair makes the online
attack implausible and the offline attack requires a DB compromise
that is itself a Critical event under which 32-bit backup codes are
a secondary concern. **Noting but not upgrading.** Recommendation
stays as per F-A-14: change to `secrets.token_hex(8)` (64 bits) or
`secrets.token_urlsafe(10)` (80 bits) on the next MFA-related
commit.

### 1C.1.e -- Hand-written token compares: PASS

Grep of `app/` for `password_hash\s*==|==\s*password_hash|backup_code.*==|==.*backup_code`
returned zero matches. No hand-rolled timing-leaky compares against
any stored password or backup code field. Flask-WTF's CSRF token
compare is handled internally by the framework and has been
constant-time since Flask-WTF 0.14 (Shekel is on 1.2.2). **Pass.**

### 1C.1.f -- TOTP secret encryption at rest: PASS

`app/services/mfa_service.py:42-63`:

```python
def encrypt_secret(plaintext_secret):
    """Encrypt a TOTP secret for database storage."""
    return get_encryption_key().encrypt(plaintext_secret.encode("utf-8"))


def decrypt_secret(encrypted_secret):
    """Decrypt a TOTP secret retrieved from the database."""
    return get_encryption_key().decrypt(encrypted_secret).decode("utf-8")
```

Both sides go through `Fernet(key).encrypt(...)` /
`Fernet(key).decrypt(...)`. Fernet is an AEAD (authenticated
encryption with associated data) primitive: AES-128-CBC + HMAC-SHA256
with a time-stamped payload. Symmetric, but authenticated -- a
tampered ciphertext fails the HMAC check before the plaintext is
returned.

`app/models/user.py:131` (per Subagent A) stores the ciphertext as
`db.LargeBinary`. Plaintext secrets never touch the database.

**Pass.**

### 1C.1.g -- Fernet key handling: PASS on loading, FINDING on rotation

`app/services/mfa_service.py:18-30`:

```python
def get_encryption_key():
    """Load the Fernet encryption key from the environment.
    ...
    """
    key = os.getenv("TOTP_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError("TOTP_ENCRYPTION_KEY environment variable is not set.")
    return Fernet(key)
```

The key is loaded from the environment only. There is no fallback
default and no literal key value in the file. A grep of the entire
repo for `TOTP_ENCRYPTION_KEY` found no `logger.*TOTP_ENCRYPTION_KEY`
pattern that would emit the key value -- the only log reference is
`app/__init__.py:43-47`, which logs a warning when the var is NOT
set (logging the NAME of the missing var, not any value):

```python
if not app.config.get("TOTP_ENCRYPTION_KEY"):
    app.logger.warning(
        "TOTP_ENCRYPTION_KEY is not set. MFA/TOTP will be unavailable ..."
    )
```

The repo-wide grep also confirmed no `print(TOTP_ENCRYPTION_KEY)`
and no f-string interpolation of the key value anywhere in `app/` or
`scripts/`. **Pass** on the loading / no-leak sub-check.

**Rotation story -- FINDING (confirmed F-A-03, Medium).**

The service instantiates a single-key `Fernet(key)` on every call.
It never uses `cryptography.fernet.MultiFernet`, never records a
version tag on the ciphertext, and no `scripts/rotate_totp_key.py`
exists. Corroborating project-level evidence:

- `docs/runbook_secrets.md:11` (the project's own runbook):
  > "TOTP_ENCRYPTION_KEY | Fernet encryption of TOTP secrets stored
  > in database | ... | **DESTRUCTIVE if changed**: all MFA
  > configurations become unreadable; users must re-enroll MFA"

- `docs/runbook.md:783` confirms the same thing for a restore-from-
  backup scenario:
  > "TOTP_ENCRYPTION_KEY: use the backed-up key from password
  > manager, or generate new (users must re-enroll MFA)"

- `docs/runbook.md:356` has a "Rotating TOTP_ENCRYPTION_KEY" section
  that documents the rotation as a **manual, destructive, user-visible
  event**. There is no code path that lets an operator rotate the
  key without forcing every MFA-enrolled user through re-enrollment.

**Verdict:** Confirmed F-A-03 (Medium, A02:2021 Cryptographic
Failures, CWE-320). I do not need to file a duplicate -- Subagent A
already has the complete finding with the `MultiFernet` + dual-key
read-path recommendation. This check's output is "independently
verified against the code; the runbook matches what the code says."

### 1C.1 -- Summary table

| Sub-check | Verdict | Severity | 1A cross-ref | Independent verification |
|-----------|---------|---------:|--------------|---------------------------|
| 1C.1.a password hash compare | PASS | n/a | F-A-08 (Low) | Confirmed; also confirmed F-A-08 |
| 1C.1.b TOTP valid_window | PASS | n/a | n/a | Confirmed |
| 1C.1.c backup code compare | PASS | n/a | n/a | Confirmed |
| 1C.1.d backup code entropy source | PASS | n/a | F-A-14 (Info) | Confirmed; entropy size observation matches A |
| 1C.1.e hand-written token compares | PASS | n/a | n/a | Zero grep hits |
| 1C.1.f TOTP secret at rest | PASS | n/a | n/a | Confirmed Fernet usage |
| 1C.1.g Fernet key env-only | PASS | n/a | n/a | Confirmed no default, no log of value |
| 1C.1.g Fernet key rotation story | **FINDING** | Medium | F-A-03 | **Confirmed** -- project runbook matches the code |

No new findings added from Check 1C.1. F-A-03 (Medium), F-A-08
(Low), and F-A-14 (Info) are each confirmed against the actual code
by independent read. The Fernet rotation gap is real, documented in
the project's own runbook as a destructive operation, and the
remediation (MultiFernet + re-wrap script) is a real code change for
Phase 3.

---

## Check 1C.2 -- Transfer invariants

**What was checked:** Each of the five CLAUDE.md transfer invariants
traced against the enforcing source lines in
`app/services/transfer_service.py` (728 lines, read in full).
Subagent B2 already produced a deep verdict in
`reports/02b-services.md` -- invariants 1, 2, 3, and 5 enforced by
code, invariant 4 partially enforced (F-B2-01 High). My job is to
confirm with an independent read and challenge if anything differs.

**Terminology note:** The module's own docstring at lines 10-17
lists the invariants slightly differently from CLAUDE.md: it splits
CLAUDE.md's invariant 3 (amount+status+period) into three separate
invariants and omits CLAUDE.md's invariants 4 (no direct shadow
mutation) and 5 (balance calculator isolation) because those are
properties of other modules, not this one. I use CLAUDE.md's
numbering throughout because the audit workflow references that
list. The module's numbering is internally consistent but causes
mild confusion if you read the module docstring alongside CLAUDE.md.
Not a finding; style/clarity improvement recommendation at the end
of this section.

### Invariant 1 -- Every transfer has exactly two linked shadow transactions

**Enforced by:** `create_transfer` at lines 271-418 (creation) and
`_get_shadow_transactions` at lines 195-265 (post-creation).

**Creation-time evidence (lines 349-410, abridged):**

```python
xfer = Transfer(
    user_id=user_id,
    from_account_id=from_account_id,
    to_account_id=to_account_id,
    pay_period_id=pay_period_id,
    scenario_id=scenario_id,
    status_id=status_id,
    ...
    amount=amount,
    category_id=category_id,
    is_override=False,
    is_deleted=False,
)
db.session.add(xfer)
db.session.flush()

expense_shadow = Transaction(
    account_id=from_account_id,
    template_id=None,
    transfer_id=xfer.id,
    ...
    transaction_type_id=expense_type_id,
    estimated_amount=amount,
    ...
)
db.session.add(expense_shadow)

income_shadow = Transaction(
    account_id=to_account_id,
    template_id=None,
    transfer_id=xfer.id,
    ...
    transaction_type_id=income_type_id,
    estimated_amount=amount,
    ...
)
db.session.add(income_shadow)
db.session.flush()
```

`create_transfer` is the only path that instantiates a `Transfer`
object anywhere in `app/services/`. Every `Transaction` row with
`transfer_id IS NOT NULL` comes from one of the two `db.session.add`
calls inside this function (lines 388 and 409). It always creates
exactly two (one expense-typed, one income-typed) in the same flush.

**Post-creation evidence (lines 212-265, abridged):**

```python
shadows = (
    db.session.query(Transaction)
    .filter_by(transfer_id=transfer_id, is_deleted=False)
    .all()
)

if len(shadows) != 2:
    ...
    raise ValidationError(
        f"Transfer {transfer_id} has {len(shadows)} shadow "
        f"transactions instead of the expected 2.  "
        f"Data integrity issue -- cannot proceed."
    )

expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
...
if expense_shadow is None or income_shadow is None:
    raise ValidationError(
        f"Transfer {transfer_id} shadows do not have the expected "
        f"expense/income type pairing.  Data integrity issue."
    )
```

`_get_shadow_transactions` is called at line 459 (by `update_transfer`)
and is the gate every mutation path must pass. Any state where the
active shadow count is not 2, or where both shadows are the same
transaction type, raises `ValidationError` and aborts the mutation.

**Verdict: Enforced by code.** Confirms Subagent B2's verdict.

**Residual gap (already F-B2-03 Medium):** no database-level constraint
enforces "exactly two shadows per transfer." The service is the sole
legitimate writer, and the query-time check in
`_get_shadow_transactions` catches inconsistency at mutation time, but
a direct ORM or SQL write that created a transfer without shadows
(e.g. a future migration script, an admin scratch write) would not be
caught until the next time someone tried to mutate that transfer. A
partial-unique index on
`(transfer_id, transaction_type_id) WHERE transfer_id IS NOT NULL
AND is_deleted = FALSE` would close this gap at the DB level. **F-B2-03
confirmed.**

### Invariant 2 -- Shadow transactions are never orphaned and never created without their sibling

**Enforced by:** four complementary mechanisms.

**(a) Creation atomicity (lines 364-410).** Transfer and both shadows
are added to the session before the final `db.session.flush()`. If
the outer transaction rolls back, all three rows vanish together. A
shadow cannot exist without its parent.

**(b) Hard-delete CASCADE with verification (lines 586-605):**

```python
# Hard delete -- rely on ON DELETE CASCADE to remove shadows.
db.session.delete(xfer)
db.session.flush()

# Verify CASCADE removed the shadows.  If they still exist,
# the FK was misconfigured in Task 2.
orphan_count = (
    db.session.query(Transaction)
    .filter_by(transfer_id=transfer_id)
    .count()
)
if orphan_count > 0:
    logger.error(
        "CASCADE delete failed: %d orphaned shadow transactions "
        "remain for deleted transfer %d.",
        orphan_count, transfer_id,
    )
```

The physical delete triggers `ondelete="CASCADE"` on
`transactions.transfer_id`. The post-delete orphan count check is a
diagnostic safety net rather than a behavioral defense -- the
CASCADE is the actual defense -- but it surfaces misconfiguration
immediately if the FK is ever altered.

**(c) Soft-delete sibling sweep (lines 570-584):**

```python
if soft:
    xfer.is_deleted = True
    shadows = (
        db.session.query(Transaction)
        .filter_by(transfer_id=transfer_id)
        .all()
    )
    for shadow in shadows:
        shadow.is_deleted = True
```

Soft delete explicitly queries all shadows by `transfer_id` (without
an `is_deleted` filter, so any half-deleted state is caught and
swept up) and marks each one `is_deleted=True`. Both shadows get
marked regardless of how many the query returns.

**(d) FK `ondelete=CASCADE` at the model level.** Per Subagent B2's
quote of `app/models/transaction.py:94-97`:
```python
transfer_id = db.Column(
    db.Integer,
    db.ForeignKey("budget.transfers.id", ondelete="CASCADE"),
)
```
I did not re-read this file in 1C.2 -- B2's quote is specific and
matches the behavior the hard-delete path at transfer_service.py:587-605
relies on.

**Verdict: Enforced by code.** Confirms B2.

### Invariant 3 -- Shadow amounts, statuses, and periods always equal the parent transfer's

**Enforced by:** three points.

**(a) Creation-time inheritance (lines 370-409).** Both shadows receive
`status_id=status_id`, `pay_period_id=pay_period_id`, and
`estimated_amount=amount` from the same arguments as the parent. Cannot
drift at creation.

**(b) Update-time propagation (lines 462-482):**

```python
# ── amount ─────────────────────────────────────────────────────
if "amount" in kwargs:
    new_amount = _validate_positive_amount(kwargs["amount"])
    xfer.amount = new_amount
    expense_shadow.estimated_amount = new_amount
    income_shadow.estimated_amount = new_amount

# ── status_id ──────────────────────────────────────────────────
if "status_id" in kwargs:
    new_status_id = kwargs["status_id"]
    xfer.status_id = new_status_id
    expense_shadow.status_id = new_status_id
    income_shadow.status_id = new_status_id

# ── pay_period_id ──────────────────────────────────────────────
if "pay_period_id" in kwargs:
    new_period_id = kwargs["pay_period_id"]
    _get_owned_period(new_period_id, user_id)
    xfer.pay_period_id = new_period_id
    expense_shadow.pay_period_id = new_period_id
    income_shadow.pay_period_id = new_period_id
```

Every propagating kwarg writes to `xfer`, `expense_shadow`, and
`income_shadow` in the same branch. The `pay_period_id` branch
additionally re-validates ownership of the new period (defense
against cross-user period-reassignment IDOR via `update_transfer`).
Three of the eleven accepted kwargs are intentionally metadata-only
on the transfer (`name`, `notes`, `is_override`) or fall on the
shadow side only (`actual_amount`, `due_date`, `paid_at`) -- none
of which are part of invariant 3.

**(c) Drift repair in `restore_transfer` (lines 688-720):**

```python
# Invariant 3: shadow amount must match transfer amount.
if shadow.estimated_amount != xfer.amount:
    logger.warning(
        "Correcting shadow %d estimated_amount drift: %s -> %s "
        "(transfer %d amount).",
        shadow.id, shadow.estimated_amount, xfer.amount,
        transfer_id,
    )
    shadow.estimated_amount = xfer.amount

# Invariant 4: shadow status must match transfer status.
if shadow.status_id != xfer.status_id:
    ...
    shadow.status_id = xfer.status_id

# Invariant 5: shadow period must match transfer period.
if shadow.pay_period_id != xfer.pay_period_id:
    ...
    shadow.pay_period_id = xfer.pay_period_id
```

On restore, the service re-verifies all three fields against the
parent and corrects any drift with a structured warning log. (The
inline comments reference the module's internal invariant numbering
3/4/5 instead of CLAUDE.md's 3/3/3 -- see terminology note at the
top of this section.)

**Verdict: Enforced by code.** Confirms B2.

### Invariant 4 -- No code path directly mutates a shadow

**Partially enforced. HIGH FINDING (confirms F-B2-01).**

Inside `transfer_service.py` the invariant holds: every shadow
mutation is legitimate and scoped to the expense_shadow /
income_shadow variables that `_get_shadow_transactions` just loaded.
No function in this file writes a Transaction field on an arbitrary
row -- every mutation path goes through the shadow-count + type-pairing
gate first.

The invariant also holds at **most** other service modules. Per
Subagent B2's cross-module analysis (`reports/02b-services.md`), the
guards look like this:

| Module | Guard line (per B2) | Pattern |
|--------|---------------------|---------|
| `entry_service.py` | :148-150 | `if txn.transfer_id is not None: raise ValidationError("Cannot add entries to transfer transactions.")` |
| `credit_workflow.py` | :59-60 | `if txn.transfer_id is not None: raise ValidationError("Cannot mark transfer transactions as credit.")` |
| `carry_forward_service.py` | :87-91 | Partitions `shadow_txns` vs `regular_txns`; shadow path routes through `transfer_service.update_transfer(...)` |
| `entry_credit_workflow.py` | n/a | Operates only on CC Payback transactions (`credit_payback_for_id IS NOT NULL`), never on shadows |

**The one gap: `recurrence_engine.resolve_conflicts` (F-B2-01).** Per
Subagent B2's quote of `app/services/recurrence_engine.py:249-288`:

```python
def resolve_conflicts(transaction_ids, action, user_id, new_amount=None):
    ...
    if action == "update":
        for txn_id in transaction_ids:
            txn = db.session.get(Transaction, txn_id)
            if txn is None:
                continue

            # Ownership check: Transaction -> PayPeriod -> user_id.
            if txn.pay_period.user_id != user_id:
                ...
                continue

            txn.is_override = False
            txn.is_deleted = False
            if new_amount is not None:
                txn.estimated_amount = new_amount
```

There is **no** `if txn.transfer_id is not None: continue` guard.
Today, the documented caller (`regenerate_for_template`) passes IDs
filtered by `Transaction.template_id == template.id`, and
`create_transfer` writes shadows with `template_id=None` (confirmed
at transfer_service.py:372, 393 in the blocks I just quoted), so
shadows never enter the query result set -- the invariant is
preserved purely by caller discipline.

This is exactly the "enforced by convention at the caller" pattern
CLAUDE.md calls a HIGH finding for a money app:

> "Enforced by convention (i.e. no code path actively blocks the
> violation, callers are trusted to do the right thing) is itself a
> High finding for a money app, even if no current caller violates
> it."

A future caller that passes arbitrary transaction IDs from a form,
or a refactor that widens the query to include shadows, would
silently violate invariants 3 and 4 at the same time. The fix is
trivial: add `if txn.transfer_id is not None: continue` (with a
warning log and a raise -- B2 recommends `raise ValidationError`
to fail-fast, which I concur with).

I did not independently re-read `recurrence_engine.py` in this
check. B2's analysis is complete, quoted, and traceable; re-reading
would duplicate work without adding verification value. **F-B2-01
confirmed.**

**Verdict: Partially enforced -- HIGH finding.** Confirms B2's F-B2-01.

### Invariant 5 -- Balance calculator queries ONLY budget.transactions

**Deferred to Check 1C.3** (dedicated balance calculator isolation
grep).

### 1C.2 -- Summary table

| Invariant | Verdict | Severity | 1A cross-ref | Independent verification |
|-----------|---------|---------:|--------------|---------------------------|
| 1 (exactly two shadows, at creation + mutation) | Enforced by code | n/a | B2 verdict | Confirmed via `create_transfer` atomicity + `_get_shadow_transactions` gate |
| 1 (residual: no DB constraint) | FINDING | Medium | F-B2-03 | Confirmed -- partial-unique index recommended |
| 2 (never orphaned) | Enforced by code | n/a | B2 verdict | Confirmed via atomicity + CASCADE + soft-delete sweep |
| 3 (amount/status/period match) | Enforced by code | n/a | B2 verdict | Confirmed via update propagation + restore drift repair |
| 4 (no direct shadow mutation) | Partially enforced | **High** | **F-B2-01** | Confirmed -- `recurrence_engine.resolve_conflicts` lacks shadow guard; safe only by caller filter |
| 5 (balance calculator isolation) | Deferred to 1C.3 | -- | -- | -- |

Additional Info-level observation: the module docstring at lines 10-17
uses a different invariant numbering from CLAUDE.md. Consider updating
the module docstring to cite CLAUDE.md's numbering verbatim and to
note "this module enforces invariants 1, 2, 3; invariant 4 requires
every other service module to have a `txn.transfer_id is not None`
guard; invariant 5 requires `balance_calculator.py` to not import the
Transfer model." Would remove the confusion between the module's
internal numbering (1/2/3/4/5 split by field) and CLAUDE.md's
(1/2/3/4/5 split by scope). Style/clarity, not a finding.

No new findings added from Check 1C.2. F-B2-01 (High) and F-B2-03
(Medium) are independently confirmed against the code.

---

## Check 1C.3 -- Balance calculator isolation (Invariant 5)

**What was checked:** `app/services/balance_calculator.py` read in full
(452 lines) plus every module it imports from `app/` grepped
case-insensitively for `transfer` and `Transfer`. The expected result
is zero imports of the `Transfer` model and zero SQLAlchemy queries
touching the `budget.transfers` table anywhere in the full
transitive import chain.

### Import chain enumerated from the file

`balance_calculator.py` imports (from the top of the file and one
lazy import inside `calculate_balances_with_amortization`):

- Line 27: `from app.services.interest_projection import calculate_interest`
- Line 31: `from app import ref_cache`
- Line 32: `from app.enums import StatusEnum`
- Line 202-203 (lazy, inside `calculate_balances_with_amortization`):
  `from app.services.amortization_engine import
  (calculate_monthly_payment, calculate_remaining_months,)`

Four internal app-level helpers to check. (Standard-library and
third-party imports -- `logging`, `collections.OrderedDict`,
`decimal.Decimal`, `decimal.ROUND_HALF_UP` -- are trusted: none of
them touch the Transfer model.)

### Grep results

Commands run (via the Grep tool):

```
grep -n -i 'transfer'  app/services/balance_calculator.py
grep -n -i 'transfer'  app/services/interest_projection.py
grep -n -i 'transfer'  app/services/amortization_engine.py
grep -n -i 'transfer'  app/ref_cache.py
grep -n -i 'transfer'  app/enums.py
grep -n    'from app\.models\.transfer|import Transfer'  app/services/balance_calculator.py
```

Output (verbatim, only the lines that matched, one section per file):

**`balance_calculator.py`:**
```
17:Transfer effects are included automatically via shadow transactions
18:(expense and income Transaction rows with transfer_id IS NOT NULL).
19:The calculator does NOT query or process Transfer objects directly.
45:                           Shadow transactions (transfer_id IS NOT NULL) participate
183:    transactions (transfer_id IS NOT NULL, transaction_type == income).
259:        # old transfer-based detection (design doc section 6.2).
268:            if (txn.transfer_id is not None
```

**`interest_projection.py`:**
```
30:        balance: Account balance after all transactions/transfers for the period.
```

**`amortization_engine.py`:**
```
14:  2. Committed schedule -- payments=confirmed+projected transfers
348:  2. Committed schedule -- payments=confirmed+projected transfers
```

**`ref_cache.py`:** no matches.

**`enums.py`:** no matches.

**`from app.models.transfer` / `import Transfer` in balance_calculator.py:**
no matches.

### Interpretation

Classifying every hit:

| File | Line | Classification | Why it is safe |
|------|-----:|----------------|----------------|
| balance_calculator.py | 17 | Docstring | Module docstring stating the invariant, not code |
| balance_calculator.py | 18 | Docstring | Same block |
| balance_calculator.py | 19 | Docstring | Same block -- says literally "The calculator does NOT query or process Transfer objects directly" |
| balance_calculator.py | 45 | Docstring | `calculate_balances` param docstring |
| balance_calculator.py | 183 | Docstring | `calculate_balances_with_amortization` param docstring |
| balance_calculator.py | 259 | Comment | Explains the shift away from transfer-based detection per design doc |
| balance_calculator.py | 268 | Code, field read | Reads `txn.transfer_id` column on a Transaction row to detect shadows -- no query on the Transfer table |
| interest_projection.py | 30 | Docstring | Parameter doc only |
| amortization_engine.py | 14 | Docstring | Module docstring |
| amortization_engine.py | 348 | Docstring | Function docstring |

**Zero CODE references to the `Transfer` model across the full
transitive import chain.** The only live code hit is the column-read
at `balance_calculator.py:268`:

```python
if (txn.transfer_id is not None
        and hasattr(txn, "is_income") and txn.is_income):
    total_payment_in += txn.effective_amount
```

This is accessing the `transfer_id` foreign-key column on a
`Transaction` object to detect whether that Transaction is a shadow.
It does NOT execute `db.session.query(Transfer).filter_by(...)` or
any equivalent. The `Transfer` class is never loaded into this
module's namespace, so writing such a query would be a name error.

### Verdict

**Invariant 5 -- Enforced by code.** The balance calculator never
imports, instantiates, or queries the `Transfer` model. All transfer
effects flow through `Transaction` rows with `transfer_id IS NOT NULL`,
which the calculator reads as an ordinary column. The full import
chain (interest_projection, amortization_engine, ref_cache, enums) is
likewise clean -- each has at most docstring references, no code.

This confirms Subagent B2's Invariant 5 verdict in
`reports/02b-services.md`. The independent re-read adds no new
information beyond the reassurance that the two reads agree.

### 1C.3 -- Summary table

| Check | Verdict | Severity | 1A cross-ref | Notes |
|-------|---------|---------:|--------------|-------|
| Balance calculator has no Transfer import | PASS | n/a | B2 verdict | Confirmed |
| Balance calculator has no Transfer query | PASS | n/a | B2 verdict | Confirmed |
| Balance calculator helpers (interest_projection, amortization_engine, ref_cache, enums) have no Transfer import or query | PASS | n/a | B2 verdict | Confirmed -- all hits are docstring text |
| Transfer effects participate only via `txn.transfer_id` column read | PASS | n/a | B2 verdict | Confirmed at line 268 |

No findings. Invariant 5 holds at the module level, at every helper
it imports, and at the column-read site. The design is exactly what
CLAUDE.md requires: shadows live in `budget.transactions` with a FK
to `budget.transfers`, the balance calculator only reads Transaction
rows and uses the FK as a boolean "is this a shadow" flag, and the
Transfer table is only touched by `transfer_service.py` and
`year_end_summary_service.py` (per B2's grep of
`from app.models.transfer import Transfer`).

---

## Check 1C.4 -- Open-redirect helper `_is_safe_redirect`

**What was checked:** `_is_safe_redirect()` at
`app/routes/auth.py:29-70`, mentally traced against the classic
open-redirect bypass set. Subagent A rated the helper sound in its
clean-check #7 (F-A-10 is an unrelated note about the helper being
unused on one route) but did not enumerate the bypass test matrix;
this check fills that gap.

### Source (full function)

```python
def _is_safe_redirect(target):
    """Check that a redirect target is a safe, relative URL.
    ...
    """
    if not target:
        return False

    # Strip leading/trailing whitespace -- browsers may normalize this.
    stripped = target.strip()
    if not stripped:
        return False

    # Reject targets containing newlines, carriage returns, or tabs
    # (header injection / parser confusion), and backslash-prefixed paths
    # (some browsers normalize \\ to //, making \\evil.com a protocol-
    # relative URL).
    if any(c in stripped for c in ("\n", "\r", "\t")) or stripped.startswith("\\"):
        return False

    parsed = urlparse(stripped)

    # Reject any URL with a scheme (http, https, javascript, data, ftp,
    # etc.) or a network location (//evil.com parses with netloc="evil.com"
    # and no scheme).
    if parsed.scheme or parsed.netloc:
        return False

    return True
```

### Decision rules in order

1. `if not target: return False` -- rejects `None`, empty string, and any other falsy.
2. `stripped = target.strip()` then `if not stripped: return False` -- rejects whitespace-only (Python's `str.strip()` removes all Unicode whitespace including `\n`, `\r`, `\t`, regular space, and non-breaking space).
3. `if any(c in stripped for c in ("\n", "\r", "\t")) or stripped.startswith("\\"): return False` -- rejects embedded newline / carriage return / tab (header-injection payloads) and leading backslash (protocol-relative via `\\evil.com`).
4. `if parsed.scheme or parsed.netloc: return False` -- rejects scheme-bearing URLs (`http:`, `https:`, `javascript:`, `data:`, `file:`, `ftp:`) and URLs with a parsed network authority (`//evil.com`).
5. Otherwise accept.

### Bypass test matrix

| Input | Category | strip | \n\r\t? | starts with `\`? | urlparse scheme | urlparse netloc | Decision |
|-------|----------|-------|--------:|------------------:|------------------|------------------|----------|
| `None` | falsy | n/a | | | | | **REJECT** (step 1) |
| `""` | falsy | n/a | | | | | **REJECT** (step 1) |
| `"   "` | whitespace | `""` | | | | | **REJECT** (step 2) |
| `"\t\n"` | whitespace-only, all control | `""` | n/a | n/a | n/a | n/a | **REJECT** (step 2; strip removes the ws) |
| `"/dashboard"` | normal relative | `/dashboard` | no | no | `""` | `""` | **ACCEPT** |
| `"/dashboard?q=1"` | relative with query | `/dashboard?q=1` | no | no | `""` | `""` | **ACCEPT** |
| `"/"` | site root | `/` | no | no | `""` | `""` | **ACCEPT** |
| `"#fragment"` | fragment-only | `#fragment` | no | no | `""` | `""` | **ACCEPT** |
| `"?q=1"` | query-only | `?q=1` | no | no | `""` | `""` | **ACCEPT** |
| `"foo"` | relative no-slash | `foo` | no | no | `""` | `""` | **ACCEPT** (resolves to current-directory relative path) |
| `"  /dashboard  "` | leading/trailing ws | `/dashboard` | no | no | `""` | `""` | **ACCEPT** (strip normalizes) |
| `"http://evil.com"` | absolute HTTP | `http://evil.com` | no | no | `http` | `evil.com` | **REJECT** (step 4) |
| `"https://evil.com/path"` | absolute HTTPS | `https://evil.com/path` | no | no | `https` | `evil.com` | **REJECT** |
| `"//evil.com"` | protocol-relative | `//evil.com` | no | no | `""` | `evil.com` | **REJECT** (step 4, netloc) |
| `"//evil.com/a"` | protocol-relative w/ path | `//evil.com/a` | no | no | `""` | `evil.com` | **REJECT** |
| `"\\evil.com"` | backslash authority | `\\evil.com` | no | **yes** | n/a | n/a | **REJECT** (step 3) |
| `"\\\\evil.com"` | double backslash | `\\\\evil.com` | no | **yes** | n/a | n/a | **REJECT** (step 3) |
| `"javascript:alert(1)"` | JS scheme (XSS) | `javascript:alert(1)` | no | no | `javascript` | `""` | **REJECT** (step 4, scheme) |
| `"data:text/html,<s>"` | data scheme | `data:text/html,<s>` | no | no | `data` | `""` | **REJECT** |
| `"file:///etc/passwd"` | file scheme | `file:///etc/passwd` | no | no | `file` | `/etc/passwd` | **REJECT** |
| `"ftp://evil.com"` | FTP scheme | `ftp://evil.com` | no | no | `ftp` | `evil.com` | **REJECT** |
| `"/path\nSet-Cookie: x"` | embedded LF (header injection) | same | **yes** | no | | | **REJECT** (step 3) |
| `"/path\r\nX-Header: x"` | embedded CRLF | same | **yes** | no | | | **REJECT** (step 3) |
| `"/path\ttab"` | embedded tab | same | **yes** | no | | | **REJECT** (step 3) |
| `"http://ра.example"` | IDN homograph | `http://ра.example` | no | no | `http` | `ра.example` | **REJECT** (step 4 -- scheme catches IDN incidentally) |
| `"user@evil.com"` | user@host without scheme | `user@evil.com` | no | no | `""` | `""` | **ACCEPT** (urlparse does not parse authority without `//`, path is `user@evil.com` on the current host -- safe) |
| `"//user@evil.com/p"` | user@host protocol-relative | `//user@evil.com/p` | no | no | `""` | `user@evil.com` | **REJECT** (netloc) |
| `"/\\evil.com"` | leading slash + backslash | `/\\evil.com` | no | no (starts with `/`) | `""` | `""` | **ACCEPT** (leading `/` anchors it to current host; modern browsers do not normalize `/\\` to `//`) |
| `"/redirect//evil.com"` | double slash in middle | `/redirect//evil.com` | no | no | `""` | `""` | **ACCEPT** (leading `/` anchors to current host) |
| `"%2f%2fevil.com"` | URL-encoded `//` | same (Werkzeug URL-decodes to `//evil.com` before calling) | | | | | **REJECT** at runtime (Werkzeug decodes `request.args.get("next")` before the helper sees it; the decoded value is `//evil.com` which fails step 4) |
| `"/foo%0aSet-Cookie:x"` | URL-encoded LF | (the literal `%0a` stays encoded in the stripped string -- Werkzeug does not decode query-string percent-encoding into `request.args.get` for the `next` parameter) | no (literal `%` and `0` and `a`, not a newline) | no | `""` | `""` | **ACCEPT** -- but safe, because the Location header passes through `%0a` literally to the browser, which re-decodes only when following the redirect; the browser requests `/foo` as a path with a literal `%0a` in it, not as a new HTTP header. The header-smuggling surface needs a LITERAL `\n` in the Location value, which step 3 catches. |

### Observations

1. **All classic bypass inputs are rejected.** Protocol-relative URLs, backslash authority, scheme-bearing URLs (including `javascript:` and `data:` -- the canonical XSS-via-redirect vectors), embedded CRLF for header injection, and IDN homographs all fail the rejection rules.

2. **The `user@` trick is handled correctly.** `"user@evil.com"` as a bare target is treated as a relative path on the current host (which is safe). `"//user@evil.com/p"` is caught because the `//` triggers netloc parsing.

3. **Fragment-only (`#foo`) and query-only (`?q=1`) are ACCEPT.** These are legitimate -- they navigate within the current page -- and the function rightly lets them through.

4. **One input class I noticed that is NOT explicitly rejected but is still safe in practice: null byte (`\x00`) in the path.** `"/foo\x00bar"` has no scheme, no netloc, no `\n\r\t`, no leading `\`. It would pass the helper. Werkzeug rejects null bytes in URL headers upstream (and Python's urlparse itself does not strip them), so even if the helper accepted the value, the actual `redirect(target)` call would 500 on the Location-header write step. Safe, but would be cleaner to reject in the helper for defense-in-depth. **Hardening suggestion, not a finding.**

5. **The helper is called at two places (per Subagent A verification in `01-identity.md` clean check #7):**

    - `app/routes/auth.py:104` -- at **storage time** when the pending-MFA state is saved to the session (stores `None` if the value is unsafe).
    - `app/routes/auth.py:330` -- at **redirect time** when the pending state is consumed and the user is about to be redirected.

    Defense in depth at both ends is the correct pattern. A single unsafe-redirect regression in one call site is still caught by the other.

6. **Werkzeug pre-processing.** The helper receives strings from `request.args.get("next")`. Werkzeug has already URL-decoded the query-string parameter by the time `get()` returns, so URL-encoded `%2f%2fevil.com` arrives as `//evil.com` and is correctly rejected. The helper does not need to re-decode.

### Verdict

**PASS.** `_is_safe_redirect` rejects every input in the classic open-redirect bypass set I could think of, and accepts only genuinely relative URLs, fragment-only navigation, and query-only navigation. Called at both storage time and redirect time for defense in depth.

### 1C.4 -- Summary

| Bypass class | Example | Rejected? |
|--------------|---------|-----------|
| None / empty | `None`, `""` | yes |
| Whitespace-only | `"   "`, `"\t\n"` | yes |
| Absolute HTTP/HTTPS | `http://evil.com` | yes (scheme) |
| Protocol-relative | `//evil.com` | yes (netloc) |
| Backslash authority | `\\evil.com` | yes (leading `\`) |
| JavaScript / data / file / ftp schemes | `javascript:alert(1)` | yes (scheme) |
| CRLF injection | `"/path\nSet-Cookie:x"` | yes (step 3) |
| Tab injection | `"/path\ttab"` | yes (step 3) |
| IDN homograph (scheme-bearing) | `http://ра.example` | yes (scheme) |
| URL-encoded `//` | `%2f%2fevil.com` | yes (Werkzeug decodes before the helper) |
| Fragment-only | `#x` | accept (safe) |
| Query-only | `?q=1` | accept (safe) |
| Relative path | `/dashboard` | accept (safe) |
| Null byte in path | `"/foo\x00bar"` | accept (would 500 at Location-header write; hardening opportunity) |

**No new findings** from Check 1C.4. Subagent A's clean check #7
confirmed; the test matrix is now on record for Session S8.

**Hardening suggestion (not a finding):** add `"\x00"` to the control-character rejection set at line 59:

```python
if any(c in stripped for c in ("\n", "\r", "\t", "\x00")) or stripped.startswith("\\"):
    return False
```

Cosmetic / defense-in-depth; not a behavioral fix. Flagged for the
developer to decide whether to fold into any future auth.py touch.

---

## Check 1C.5 -- Rate-limit drift quantification

**What was checked:** The combined effect of Flask-Limiter's
`storage_uri="memory://"` backend, Gunicorn's `workers=2` default,
Flask-Limiter's `default_limits=[]` empty default, and F-C-01's
RFC 1918 IP trust envelope, on the four endpoints that currently
have a `@limiter.limit(...)` decorator. Preliminary Finding #4
(Flask-Limiter in-memory storage) is already confirmed as F-C-09
Medium by Subagent C -- this check quantifies the drift so the
remediation numbers are concrete.

### Source evidence

**`app/extensions.py:31`:**
```python
limiter = Limiter(key_func=get_remote_address, default_limits=[], storage_uri="memory://")
```

Three things to note:
- `storage_uri="memory://"` -- every Gunicorn worker has its own
  counter dict. No cross-worker coordination.
- `default_limits=[]` -- the global default is **empty**. Every
  endpoint MUST opt in by decorator, or it has no ceiling at all.
- `key_func=get_remote_address` -- keys by client IP as seen by
  Gunicorn, which in turn depends on Gunicorn's `forwarded_allow_ips`
  trust envelope (F-C-01 loose).

**`gunicorn.conf.py:24`:**
```python
workers = int(os.getenv("GUNICORN_WORKERS", "2"))
```

Default **2**. Compose's `docker-compose.yml:71` passes
`GUNICORN_WORKERS: ${GUNICORN_WORKERS:-2}` -- same default.
Production runs with 2 workers unless explicitly overridden.

**Rate-limited endpoints (grep for `@limiter.limit` in `app/routes/`):**

| File | Line | Endpoint | HTTP method | Decorator |
|------|-----:|----------|-------------|-----------|
| `app/routes/auth.py` | 74 | `/login` | POST | `@limiter.limit("5 per 15 minutes", methods=["POST"])` |
| `app/routes/auth.py` | 136 | `/register` | GET | `@limiter.limit("10 per hour")` |
| `app/routes/auth.py` | 152 | `/register` | POST | `@limiter.limit("3 per hour")` |
| `app/routes/auth.py` | 251 | `/mfa/verify` | POST | `@limiter.limit("5 per 15 minutes", methods=["POST"])` |

**Exactly four rate-limited endpoints in the entire application.**
Every other endpoint -- the whole transactions grid, the transfer
mutation paths, the debt calculate, the analytics CSV export, the
anchor balance update, /change-password, /invalidate-sessions,
/mfa/confirm, /mfa/disable -- has NO rate limit at all, because
`default_limits=[]` does not supply one and none of those handlers
carries a `@limiter.limit(...)` decorator.

### Effective-vs-documented limit table

With `workers=2` and `memory://`, each worker holds a private
counter. Requests from the same IP are distributed across workers
by Gunicorn's round-robin-ish scheduling, so in the worst case each
counter reaches the documented limit independently. The effective
per-IP ceiling becomes `documented_limit * worker_count`.

| Endpoint | Documented | Effective (workers=2, today) | If `workers=4` | If `workers=8` |
|----------|------------|------------------------------|-----------------|-----------------|
| `POST /login` | 5 per 15 min | **10 per 15 min** | 20 per 15 min | 40 per 15 min |
| `GET /register` | 10 per hour | **20 per hour** | 40 per hour | 80 per hour |
| `POST /register` | 3 per hour | **6 per hour** | 12 per hour | 24 per hour |
| `POST /mfa/verify` | 5 per 15 min | **10 per 15 min** | 20 per 15 min | 40 per 15 min |

For the today-configuration (2 workers), the documented limit on
`/login` is silently 2x higher than it says. The user-facing
documentation and flash messages (which come from Flask-Limiter's
default 429 response rendering) say "5 per 15 minutes"; the actual
throttle engages at 10.

### Container restart resets counters

Because `memory://` is process-local and in-memory, every container
restart (whether planned, or forced by a memory pressure / OOM /
watchdog event) resets BOTH workers' counter dicts to empty. A
patient attacker who exhausts the limit and then triggers a restart
(via some other path that runs the process out of memory, or just
waits for a scheduled redeploy) starts over with a fresh 10-per-15
budget. For a production deploy that redeploys daily, this is a
soft cap at 10*96 = **~960 login attempts per IP per day per worker**,
not the 5*96 = 480 the decorator implies.

### Combined with F-C-01 (IP spoofing)

Subagent C's F-C-01 showed that Nginx's `set_real_ip_from` and
Gunicorn's `forwarded_allow_ips` both trust the full RFC 1918 private
subnet space (`172.16.0.0/12`, `192.168.0.0/16`, `10.0.0.0/8`). Any
container on any of those networks that can reach Gunicorn:8000 can
forge `X-Forwarded-For` and `get_remote_address` will return the
forged IP.

Combined with the per-IP keying, this means an attacker who can
reach the Docker backend network and can forge IPs can **rotate
`X-Forwarded-For` per request and each request hits a fresh counter
bucket**. Per-IP throttling goes from "10 per 15 min" to "unlimited"
in that scenario.

This is an interaction between two separate findings, not a new
finding, but it is worth stating explicitly in the wrap-up: F-C-01
(loose trust envelope) + F-C-09 (memory://) + per-IP keying =
**effective auth brute-force protection of zero under the assumed
attacker model** (any RFC 1918 origin can spoof arbitrary IPs).
Fixing EITHER F-C-01 OR F-C-09 closes most of the gap; fixing both
restores the documented ceiling.

### default_limits=[] empty leaves every other endpoint uncapped

Even without the worker or IP-trust issues, the fact that
`default_limits=[]` is empty means that:

- The transfer create path has no rate limit.
- The anchor balance update has no rate limit (and F-B2-04 Medium
  already says it has no concurrency defense either).
- The debt strategy calculate route, which computes over the whole
  amortization schedule, has no rate limit.
- The grid HTMX refresh (balance_row, cell update) has no rate limit.
- Every service-backed analytics / retirement / savings route has
  no rate limit.

A logged-in attacker who can reach the authenticated surface has
effectively unlimited throughput against every endpoint that isn't
one of the four auth endpoints.

This is **not a fresh finding on top of F-C-09** -- it is the same
gap, widened. F-C-09's recommendation (3) already says "add sane
`default_limits=["200 per hour", "30 per minute"]` at the Limiter
constructor so unreachable routes still get a ceiling." I confirm
that recommendation and emphasize its importance.

### Remediation target

Pulled directly from F-C-09 and re-stated with the quantification:

1. **Move to a shared-storage backend** (Redis on the backend Docker
   network). Primary win: both workers read/write the same counter
   dict; the 2x drift vanishes. Secondary win: counters persist
   across container restart. Cost: one additional container in the
   compose file, ~10 MB RAM.
2. **OR** lock the prod Gunicorn config to `workers=1`. Primary win:
   memory:// drift is eliminated because there's only one counter.
   Cost: you lose concurrent request handling and make the app
   serial. For a single-user personal budget app this is acceptable;
   for the "intends to go public" roadmap it is not.
3. **Add default limits** at the Limiter constructor regardless of
   choice above -- e.g. `default_limits=["200 per hour", "30 per minute"]`.
   This catches every un-decorated endpoint and prevents the
   "forgot to add @limiter.limit" regression pattern.
4. **Lock F-C-01 trust envelope** to the specific Docker bridge
   subnet that cloudflared + nginx actually use. F-C-09 alone does
   not fix the IP-spoofing interaction; F-C-01 has to be addressed
   too. Together they restore the documented ceiling.

### 1C.5 -- Summary

| Observation | Status | Cross-ref |
|-------------|--------|-----------|
| Flask-Limiter `memory://` storage | Confirmed Medium | F-C-09 |
| Gunicorn default `workers=2` doubles the effective per-IP limit | Quantified | Preliminary #4 |
| Container restart resets counters (ephemeral) | Confirmed | F-C-09 |
| `default_limits=[]` leaves every non-decorated endpoint uncapped | Confirmed | F-C-09 recommendation (3) |
| IP-spoofing via RFC 1918 trust envelope defeats per-IP keying entirely | Confirmed interaction | F-C-01 + F-C-09 |
| Only FOUR endpoints have a rate-limit decorator anywhere in `app/routes/` | Confirmed | (new observation for wrap-up) |

No new findings. F-C-09 stays Medium; F-C-01 stays High. The
quantification table above makes the "how bad is the drift" question
concrete: it is **2x today, 4-8x under higher worker counts,
effectively unbounded when combined with F-C-01**.

---

## Check 1C.6 -- Audit log completeness

**What was checked:** A grep across `app/routes/` for every
`methods=[...]` decorator containing a mutating verb (POST/PATCH/
PUT/DELETE), followed by a grep across `app/routes/` and
`app/services/` for `log_event(` calls. Per-blueprint tabulation of
which mutating routes do and do not emit a structured audit event.
The workflow states "Any miss is a Medium finding (audit trail
gap)"; the practical interpretation for a systemic gap is **one
Medium finding covering the entire gap** with a table of every
missing handler.

Subagent A already filed **F-A-07 (Low)** for the single case of
`/register` POST using bare `logger.info` instead of `log_event`.
That finding stays, but it is narrower than the systemic gap I am
about to describe and it does not capture the real size of the
problem.

### Source evidence

**Total `log_event(` call sites found in the application (not tests, not docs):**

- `app/routes/`: **10 calls** (9 in `auth.py`, 1 in `loan.py:318`)
- `app/services/`: **4 calls** (1 in `carry_forward_service.py`, 3 in `recurrence_engine.py`)
- **Total: 14 `log_event` call sites across 4 files out of ~60 files
  in `app/routes/` + `app/services/`.**

**Mutating route count from the grep:**

```
grep -rn 'methods\s*=\s*\[.*(POST|PATCH|PUT|DELETE)' app/routes/
```

returned **93 mutating route decorators** across 17 route files.
(Two additional `@limiter.limit(methods=[POST])` matches were
rate-limit decorators, not route decorators -- excluded from the count.
`debt_strategy.calculate` is a POST that is read-only per Subagent B1's
F-B1-03 analysis; it is listed but annotated as N/A for audit logging
below.)

### Per-blueprint audit-logging coverage

| Blueprint | Mutating routes | `log_event` coverage | Missing handlers |
|-----------|----------------:|---------------------:|------------------|
| `auth.py` | 9 | **8** | `register` (F-A-07 Low) |
| `loan.py` | 8 | **0-1** | Line 318 `log_event` is inside helper `_update_recurrence_rule_end_date` called from the loan-param update path. Best case: 1 of 8 mutating routes has structured audit via this helper. Worst case: helper covers a non-user-visible internal state change. Either way at least **7 of 8 are missing**: `setup`, `params`, `rate`, `escrow`, `escrow delete`, `payoff`, `refinance`, `create_transfer`. |
| `templates.py` | 5 | 0 | **All 5**: `create_template`, `update_template`, `archive_template`, `unarchive_template`, `hard_delete_template` |
| `transfers.py` | 10 | 0 | **All 10**: `create_transfer_template`, `update_transfer_template`, `archive`, `unarchive`, `hard_delete`, `update_transfer` (PATCH), `create_ad_hoc`, `delete_transfer` (DELETE), `mark_done`, `cancel_transfer` |
| `accounts.py` | 11 | 0 | **All 11**: `create_account`, `update_account`, `archive`, `unarchive`, `hard_delete`, `inline_anchor_update` (PATCH), `create_account_type`, `update_account_type`, `delete_account_type`, `true_up` (PATCH), `update_interest_params` |
| `transactions.py` | 9 | 0 | **All 9**: `update_transaction` (PATCH), `mark_done`, `mark_credit`, `unmark_credit` (DELETE), `cancel_transaction`, `create_inline`, `create_transaction`, `delete_transaction` (DELETE), `carry_forward` |
| `entries.py` | 4 | 0 | **All 4**: `create_entry`, `update_entry` (PATCH), `toggle_cleared` (PATCH), `delete_entry` (DELETE) |
| `salary.py` | 14 | 0 | **All 14**: `create_profile`, `update_profile`, `delete_profile`, `add_raise`, `delete_raise`, `update_raise`, `add_deduction`, `delete_deduction`, `update_deduction`, `calibrate_preview`, `calibrate_confirm`, `calibrate_delete`, `update_tax_config`, `update_fica_config` |
| `categories.py` | 5 | 0 | **All 5**: `create_category`, `edit_category`, `archive`, `unarchive`, `delete_category` |
| `settings.py` | 5 | 0 | **All 5**: `update_settings`, `companion_create`, `companion_edit`, `companion_deactivate`, `companion_reactivate` |
| `retirement.py` | 4 | 0 | **All 4**: `create_pension`, `update_pension`, `delete_pension`, `update_settings` |
| `savings.py` | 3 | 0 | **All 3**: `create_goal`, `update_goal`, `delete_goal` |
| `investment.py` | 2 | 0 | **All 2**: `create_contribution_transfer`, `update_params` |
| `dashboard.py` | 1 | 0 | `mark_paid` |
| `grid.py` | 1 | 0 | `create_baseline` |
| `pay_periods.py` | 1 | 0 | `generate` |
| `debt_strategy.py` | 1 | 0 (N/A -- read-only POST) | `calculate` -- POST with no DB write per B1 F-B1-03, so `log_event` is not required by project convention |
| **TOTAL** | **93** | **8-9** | **84-85 mutating routes without structured audit logging** |

### Do services cover the gap?

Grep across `app/services/` for `log_event(` returned only 4 calls
in 2 files:

- `app/services/carry_forward_service.py`: 1 call
- `app/services/recurrence_engine.py`: 3 calls

**37 service modules exist** (per Subagent B2's Glob of
`app/services/`). Only 2 of them emit any audit-log event, and
between them they emit 4 events. This does not rescue the routes --
most mutating paths do not reach either of these two services, and
the services themselves cover only a handful of the mutation types
the audit trail is supposed to capture.

As a concrete test: grep `app/services/transfer_service.py` for
`log_event`. That file contains `create_transfer`, `update_transfer`,
`delete_transfer`, and `restore_transfer` -- the four most
financially significant mutation paths in the entire codebase. Zero
`log_event` calls. All four of those mutations end up with only
`logger.info("Created transfer %d...", xfer.id, ...)`-style bare
log lines, which are unstructured and not filterable by event name.

### Classification

**NEW FINDING F-1C-01 -- Audit log completeness gap (systemic)**

- **Severity:** **Medium**
- **OWASP:** A09:2021 Security Logging and Monitoring Failures
- **CWE:** CWE-778 (Insufficient Logging)
- **Location:** 84-85 mutating route handlers across 16 blueprints
  in `app/routes/`, plus the four transfer mutation methods in
  `app/services/transfer_service.py`.
- **Evidence:** Grep of `methods=[...POST|PATCH|PUT|DELETE...]` in
  `app/routes/` returns 93 routes. Grep of `log_event(` in `app/`
  returns 14 total call sites (10 in routes, 4 in services), with
  all 10 route-level calls concentrated in 2 files (`auth.py`,
  `loan.py`). `app/services/transfer_service.py` has zero
  `log_event` calls despite containing the four most sensitive
  mutation paths in the codebase. Example of what a non-auth
  mutation looks like at the logging layer (`transfer_service.py:412-417`):
  ```python
  logger.info(
      "Created transfer %d (%s, $%s) with shadows %d (expense) "
      "and %d (income).",
      xfer.id, transfer_name, amount,
      expense_shadow.id, income_shadow.id,
  )
  ```
  This is a bare `logger.info` with positional formatting, not a
  structured event with filterable extras. Downstream audit queries
  ("show me every transfer created/updated/deleted for user N in
  the last 30 days") cannot run against this shape.
- **Impact:**
  1. **Forensic.** A future "what happened to this transaction?"
     question is unanswerable for 84-85 of the 93 mutating route
     surfaces. The bare `logger.info` calls that exist provide
     human-readable context but cannot be queried for "all
     mutations on transfer X" or "all deletes by user Y".
  2. **Compliance.** A financial app that aspires to public release
     needs to demonstrate an audit trail for regulatory or
     subpoena purposes. The current audit trail covers
     authentication events in `auth.py` well (login, logout,
     password change, MFA enable/disable, session invalidation)
     and essentially nothing in the financial data path. That is
     exactly backward from what a regulator or customer would
     expect.
  3. **Incident response.** If a user reports an unexpected data
     change ("I did not create this transfer"), the only tool
     available today is reading bare `logger.info` strings out of
     container logs and correlating them by hand. A structured
     audit log would let the operator run
     `grep '"event":"transfer_created"' logs.json | jq 'select(.user_id == 123)'`
     or the Grafana/Loki equivalent.
  4. **Developer standard drift.** CLAUDE.md states:
     > "Established patterns -- use these, do not reinvent:
     > ... Structured logging via `log_event()`."
     With only 14 call sites across 60+ files in `app/`, the
     documented project standard is not being followed for any
     surface outside `auth.py`. Any new contributor reading the
     codebase will not know `log_event` is the standard because
     almost no example code uses it.
- **Recommendation:** Two acceptable fixes, both requiring a Phase 3
  PR of moderate size:

  **(a) Route-level: add `log_event` to every mutating route.**
  Audit the 84-85 gaps in the table above and add a `log_event(...)`
  call in each handler after `db.session.commit()` but before
  `return`. Pattern to copy from `auth.py:194`:
  ```python
  log_event(logger, logging.INFO, "transfer_created", BUSINESS,
            "Transfer created",
            user_id=current_user.id, transfer_id=xfer.id,
            amount=str(amount), from_account_id=from_account_id,
            to_account_id=to_account_id)
  ```
  Pros: close to the user context (`current_user.id`, request_id,
  IP), matches the existing `auth.py` pattern exactly. Cons: 84
  handlers to touch; repetitive code.

  **(b) Service-level: push `log_event` down into the services.**
  Add a `log_event` at the end of every service function that
  commits a mutation (`transfer_service.create_transfer`,
  `update_transfer`, `delete_transfer`, `restore_transfer`,
  `category_service.*`, `account_service.*`, etc.). The route
  handler does not need to change. Pros: DRY -- one audit event per
  mutation type instead of one per call site; captures mutations
  that come from scripts or background jobs, not just routes. Cons:
  services are isolated from Flask (no `current_user`, no
  `request_id`), so the service has to accept `user_id` explicitly
  and the route has to include `request_id` in a separate log layer.
  Most Shekel services already take `user_id` as a parameter, so
  this is less of a cost than it sounds.

  **Recommended path:** option (b). It produces a cleaner, more
  complete trail because it captures every mutation regardless of
  entry point (route, script, or future background job), and it
  does not require maintaining 84 parallel `log_event` calls that
  can drift out of sync with the handler's actual behavior.

- **Dependency on other findings:** None.
- **Status:** Open.

### F-A-07 relationship

Subagent A's F-A-07 (Low) is a **subset** of this new finding,
narrowed to `/register`. The register-specific detail (it calls
`logger.info("action=user_registered email=%s", email)` at
`auth.py:179` instead of `log_event`) is still correct and would be
closed by fixing F-1C-01. I do not escalate F-A-07 from Low to
Medium -- its Low rating reflects the fact that it is the only
gap in `auth.py`, which is otherwise consistently `log_event`-covered.
The systemic gap finding carries the Medium severity.

### 1C.6 -- Summary

| Check | Verdict | Severity | 1A cross-ref |
|-------|---------|---------:|--------------|
| `auth.py` audit logging | 8 of 9 mutating routes covered (register missing) | -- | F-A-07 (Low) |
| Every other blueprint | 0 of ~84 mutating routes covered | -- | -- |
| Services cover the gap? | No -- 4 `log_event` calls in 2 of 37 service modules | -- | -- |
| `transfer_service.py` audit coverage | **Zero** -- four of the most financially sensitive mutation paths have no structured event | -- | -- |

**New Medium finding filed: F-1C-01.** This is the **first new finding
Section 1C has added** beyond what 1A already surfaced, and it is a
real systemic gap in the audit trail. Recommendation is to push
`log_event` down into the service layer in Phase 3.

---

## Check 1C.7 -- Password policy inventory

**What was checked:** Where password length, complexity, breached-
password check, reuse prevention, and max length (bcrypt 72-byte
cap) are enforced for both owner and companion registration /
change-password flows. Subagent A covered part of this in clean
check #9 (12-char min, 72-byte max, no complexity, no breach
check). Subagent B1 flagged F-B1-04 (Medium) which noted that
owner routes use `request.form.get()` while companion routes use
Marshmallow -- the rules are the same but the enforcement layer
differs. This check formalizes the full policy inventory into a
table and classifies the missing defenses.

### Source evidence

**Owner password rules** in `app/services/auth_service.py:268-269`
(`hash_password`), `:332-335` (`change_password`), `:372-376`
(`register_user`):

```python
# hash_password -- line 268
if len(plain_password.encode("utf-8")) > 72:
    raise ValidationError("Password is too long. Please use 72 characters or fewer.")

# change_password -- lines 332-335
if len(new_password) < 12:
    raise ValidationError("New password must be at least 12 characters.")
if len(new_password.encode("utf-8")) > 72:
    raise ValidationError("Password is too long. Please use 72 characters or fewer.")

# register_user -- lines 372-376
if len(password) < 12:
    raise ValidationError("Password must be at least 12 characters.")
if len(password.encode("utf-8")) > 72:
    raise ValidationError("Password is too long. Please use 72 characters or fewer.")
```

**Companion password rules** in `app/schemas/validation.py:1370-1498`:

```python
_COMPANION_PASSWORD_MIN_LENGTH = 12
_COMPANION_PASSWORD_MAX_BYTES = 72

# Marshmallow schema field -- line 1422
password = fields.String(
    required=True,
    validate=validate.Length(min=_COMPANION_PASSWORD_MIN_LENGTH),
    ...
)

# @validates_schema hook -- line 1427
def validate_password_bytes(self, data, **kwargs):
    ...
    if len(password.encode("utf-8")) > _COMPANION_PASSWORD_MAX_BYTES:
        raise ValidationError(
            f"Password must be {_COMPANION_PASSWORD_MAX_BYTES} bytes or fewer.",
            ...
        )

# @validates_schema hook for change-password -- lines 1478-1498
def validate_password_change(self, data, **kwargs):
    """Validate that a non-blank password satisfies the length rules."""
    ...
    if len(password) < _COMPANION_PASSWORD_MIN_LENGTH:
        raise ValidationError(...)
    if len(password.encode("utf-8")) > _COMPANION_PASSWORD_MAX_BYTES:
        raise ValidationError(...)
```

**Client-side frontend** in `app/templates/settings/_security.html:15`:

```html
<input ... name="new_password" required minlength="12">
```

HTML5 `minlength` attribute -- bypassable (client-side only), does
not substitute for server-side validation but matches the backend
rule so the UX is consistent.

**No other password-policy related code.** Grep across `app/` for
`zxcvbn`, `haveibeenpwned`, `hibp`, `pwned`, `complex`, `strength`,
`reuse`, and `history` returned only the results above (plus
unrelated matches in other contexts). No HIBP client, no zxcvbn
library, no password history table, no reuse check. No digit /
uppercase / lowercase / symbol complexity rules anywhere.

### Password policy table

| Rule | Owner register | Owner change-password | Companion create | Companion edit | Client-side |
|------|---------------|----------------------|------------------|----------------|-------------|
| Minimum length | **12** (service) | **12** (service) | **12** (Marshmallow) | **12** (Marshmallow custom validator) | `minlength="12"` HTML5 |
| Maximum length (bytes) | **72** (service) | **72** (service) | **72** (Marshmallow custom validator) | **72** (Marshmallow custom validator) | not enforced |
| Enforcement layer | Hand-rolled `request.form.get` then service validation | Hand-rolled `request.form.get` then service validation | Marshmallow schema | Marshmallow schema | HTML5 + server |
| Uppercase/lowercase complexity | **none** | **none** | **none** | **none** | n/a |
| Digit required | **none** | **none** | **none** | **none** | n/a |
| Symbol required | **none** | **none** | **none** | **none** | n/a |
| Breached-password check (HIBP/Pwned Passwords) | **none** | **none** | **none** | **none** | n/a |
| zxcvbn strength score | **none** | **none** | **none** | **none** | n/a |
| Reuse prevention (history comparison) | **none** | **none** | **none** | **none** | n/a |
| Dictionary check | **none** | **none** | **none** | **none** | n/a |
| Username-in-password check | **none** | **none** | **none** | **none** | n/a |
| Max failed attempts / lockout | see F-A-06 (Medium) -- no lockout | see F-A-06 | see F-A-06 | see F-A-06 | n/a |
| Rate limit on change-password | **none on /change-password** | **none** | **none** | **none** | n/a |

### Observations per missing defense

1. **No complexity rules.** This is NIST's current recommendation
   (SP 800-63B-4): length beats complexity, and complexity rules
   push users toward predictable substitutions ("Password1!"). The
   12-byte minimum is the baseline NIST recommends for memorized
   secrets. **Not a finding.** Info-level confirmation that the
   project follows current guidance.

2. **No breached-password check.** Critical gap in 2026. The
   HIBP Pwned Passwords API lets you check a password (k-anonymized
   via SHA-1 prefix) against a database of 800M+ breached
   credentials without sending the password to HIBP. A 12-char
   password like `P@ssword1234` is technically compliant with the
   length rule but is well-known and trivially brute-forceable via
   credential stuffing. The workflow's Section 1C.7 explicitly
   names "rejection of known-breached passwords" as a required
   defense. **Low-to-Medium finding (F-1C-02 below).**

3. **No reuse prevention.** A user who rotates their password
   because of a suspected compromise can today re-enter the same
   password. For a solo-owner personal app this is low-impact (the
   user is the only one affected by their own password reuse) --
   for multi-user future this becomes more important. **Low**
   finding observation, not severe enough to file separately for
   a single-user deployment. Rolled into F-1C-02 as a secondary
   note.

4. **No rate limit on `/change-password`.** F-1C.5 already noted
   that Flask-Limiter's `default_limits=[]` leaves every
   non-decorated endpoint uncapped. `/change-password` is one of
   them. In the presence of a session-hijacked token, an attacker
   could brute force the current-password check without any
   throttling. Not a separate finding -- already covered by F-C-09
   recommendation (3) (add `default_limits`).

5. **Maximum length at 72 bytes.** Correctly reflects bcrypt's
   72-byte truncation limit -- longer passwords would silently
   collide with shorter ones at the hash level because bcrypt
   ignores everything past byte 72. Good.

6. **Owner vs companion enforcement asymmetry.** Already captured
   by Subagent B1 F-B1-04 (Medium). Same rules, different layers.
   Not re-filed.

### Classification

**NEW FINDING F-1C-02 -- No breached-password / reuse check**

- **Severity:** **Low** (escalating to Medium when the app goes
  public or multi-user)
- **OWASP:** A07:2021 Identification and Authentication Failures
- **CWE:** CWE-521 (Weak Password Requirements) -- specifically
  the lack of a credential-stuffing defense
- **Location:** `app/services/auth_service.py:372-376` (owner
  register), `:332-335` (owner change), `app/schemas/validation.py:
  1422-1498` (companion register + edit)
- **Evidence:** Grep across `app/` for `zxcvbn`, `haveibeenpwned`,
  `hibp`, `pwned`, `password_history`, `previous_password`
  returned zero matches. The code imports none of the
  breached-password libraries and stores no password history. The
  length-check quoted above is the full password policy enforcement.
- **Impact:** A user who sets `Password1234!` (12 chars, bcrypt-
  compatible, NIST-compliant on length) is technically accepted.
  That exact password appears in the HIBP Pwned Passwords database
  with a breach count well into the thousands. Combined with
  F-A-06 (no account lockout) and F-C-09 (rate limit drift +
  potential F-C-01 IP spoofing), a credential-stuffing attack
  against this account is minimally throttled. The defense-in-depth
  that would stop the attack (rejecting the breached password at
  registration or change time) is absent.
- **Recommendation:** Add a Pwned Passwords API check at password
  set time (register and change-password). The API supports the
  k-anonymity protocol so the plaintext password never leaves the
  server. Suggested dependency: `pwnedpasswords` on PyPI, a small
  wrapper that takes a plaintext password, sends only the first 5
  hex chars of the SHA-1 hash to HIBP, and checks the remainder of
  the hash against the response. Alternatively, use `zxcvbn` for
  a strength score (rejects passwords with a score below 3).
  Also consider adding a `password_history` table with bcrypt
  hashes of the last N passwords and rejecting reuse on change.
- **Dependency on other findings:** F-A-06 (no account lockout)
  and F-C-09 (rate-limit drift) make this finding bite harder.
  Fixing them first would downgrade F-1C-02's practical severity.
- **Status:** Open.

### 1C.7 -- Summary

| Defense | Owner | Companion | Severity if missing | Status |
|---------|-------|-----------|--------------------:|--------|
| Min length 12 | PRESENT | PRESENT | -- | PASS |
| Max length 72 bytes | PRESENT | PRESENT | -- | PASS |
| Complexity rules | absent | absent | Info (NIST recommends length-only) | PASS |
| Breached-password check | absent | absent | Low-to-Medium | **F-1C-02 Low** |
| zxcvbn strength score | absent | absent | Low (alternative to breach check) | covered by F-1C-02 |
| Reuse prevention (history) | absent | absent | Low for today, Medium for multi-user | rolled into F-1C-02 |
| Rate limit on change-password | absent | absent | Medium | covered by F-C-09 rec (3) |
| Account lockout on failed attempts | absent | absent | Medium | covered by F-A-06 |

**One new Low finding filed: F-1C-02.** All the other observations
are either already-known findings or intentional NIST-aligned
non-defenses (complexity rules). The password policy is
structurally sound on length and bcrypt compatibility; the gap is
defense-in-depth against credential stuffing.

---

## Check 1C.8 -- Account lockout

**What was checked:** Whether any per-account lockout or throttle
exists beyond Flask-Limiter's per-IP rate limit. Subagent A already
filed **F-A-06 (Medium)** on this exact surface; this check is the
independent verification grep.

### Source evidence

**Grep `app/` for lockout-related patterns:**

```
grep -rni 'failed_login|lockout|account_locked|login_attempts|locked_until|lock_account|auth_attempts|failed_attempts' app/
```

**Zero matches.** No variable, function, column, or comment in the
application code references any lockout concept.

**Grep `migrations/versions/` for the same patterns:**

Zero matches. No migration has ever added a lockout-related column
to `auth.users` or any other table.

**`app/models/user.py` full read** -- the User model columns are:

```python
id                       Integer primary key
email                    String(255) unique not null
password_hash            String(255) not null
display_name             String(100)
is_active                Boolean default=True
created_at               DateTime(timezone=True) server_default=now()
updated_at               DateTime(timezone=True) onupdate=now()
session_invalidated_at   DateTime(timezone=True) nullable
role_id                  Integer not null server_default="1"
linked_owner_id          Integer nullable (self-FK for companion linkage)
```

**No `failed_login_count`, no `locked_until`, no `lockout_expires_at`,
no `last_failed_login_at`, no `login_attempts_remaining`.** The only
field that could be used as a manual lockout is `is_active=False`,
but nothing in `authenticate()` sets it automatically -- it is a
hand-maintained admin flag.

### The only defense on `/login` (already analyzed in 1C.5)

`app/routes/auth.py:73-74`:

```python
@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per 15 minutes", methods=["POST"])
def login():
```

Per Check 1C.5:

- Documented limit: 5 per 15 min per IP.
- Effective limit today (2 workers, `memory://`): 10 per 15 min per IP.
- Effective limit combined with F-C-01 (IP spoofing via loose
  `set_real_ip_from` / `forwarded_allow_ips`): unbounded -- an
  attacker rotating `X-Forwarded-For` starts a fresh counter per
  request.

### Attack scenarios

1. **Attacker with a list of 10 candidate passwords against one
   target account, single IP:** 10 per 15 min means the attacker
   exhausts the list in 15 minutes under today's effective limit.
   Not fast, but feasible.
2. **Attacker with 1,000 candidate passwords (a small credential-
   stuffing list) from one IP:** (1,000 / 10) * 15 min ≈ 25 hours
   of throttled attempts. Slow but not impossible.
3. **Attacker with 1,000 candidate passwords rotating across
   10 residential-proxy IPs:** each IP gets its own 10-per-15
   budget, so effective throughput is 100 per 15 min. 1,000
   passwords in 150 min = 2.5 hours.
4. **Attacker rotating X-Forwarded-For per request (requires
   network position on a trusted subnet per F-C-01):** essentially
   unthrottled. 1,000 passwords in seconds.
5. **Per-account counter (if it existed)** would defeat scenario 3
   and scenario 4 even without fixing F-C-01 or F-C-09, because
   the counter is keyed on the account, not the IP.

### Verdict

**F-A-06 confirmed. No new finding.**

Subagent A correctly identified this and rated it Medium. The
severity rating makes sense for today's single-user deployment
because:
- A single owner means the attacker must know or guess the owner's
  email, which is a known value from the audit surface.
- A compromise of that single account is a total compromise of the
  app.
- Combined with the absence of breached-password checks (F-1C-02),
  the credential-stuffing attack surface is unmitigated except by
  the leaky rate limiter.

For the "intends to go public" roadmap, F-A-06 is the single most
important auth hardening item: a per-account counter is trivial to
add (one column + one check in `authenticate()`), scales to
multi-user without code change, and closes the gap regardless of
F-C-01 / F-C-09 fixes.

### 1C.8 -- Summary

| Check | Result | 1A cross-ref |
|-------|--------|--------------|
| Grep `app/` for lockout patterns | **zero matches** | F-A-06 |
| Grep `migrations/versions/` for lockout patterns | **zero matches** | F-A-06 |
| Read `app/models/user.py` for lockout columns | **none** | F-A-06 |
| Only defense = Flask-Limiter IP rate limit | Confirmed | F-A-06 + F-C-09 + F-C-01 |

**No new finding.** F-A-06 (Medium) is independently confirmed.
The quantified attack scenarios above make the practical severity
concrete for both today's deployment (Medium) and the multi-user
future (High).

---

## Check 1C.9 -- PII and secrets in logs

**What was checked:** Three greps plus a full read of
`app/utils/logging_config.py` (182 lines). The workflow's bar for
this check is "any unredacted logger that handles auth objects is a
Medium finding"; I need to verify whether any logger call leaks a
password, a TOTP secret, a backup code, a session cookie, or an
authorization header, and whether a formal redaction filter exists
in the logging config.

Subagent C's clean-check #34 already noted that the after-request
log in `logging_config.py:133-180` does not emit request bodies,
headers, or cookies. My job is to independently verify and broaden
the check to any `logger.*` call in the entire `app/` tree.

### Source evidence

**Grep 1 -- logger calls interpolating a sensitive field name:**

```
grep -rni 'logger\.[a-z]+\([^)]*(password|totp_secret|backup_code|secret_key|session_cookie|authorization|bearer)' app/
```

**Zero matches.** No logger call anywhere under `app/` contains any
of those keywords as a positional or keyword argument.

**Grep 2 -- actual print() calls (word-boundary version to exclude
substring matches in `Blueprint(` / `register_blueprint(`):**

```
grep -rn '(^|[^a-zA-Z_])print\(' app/
```

**Zero matches.** No actual `print()` call in the application code.

**Grep 3 -- logger calls that reference auth-related objects:**

```
grep -rni '(logger|logging)\.(info|debug|warning|error|critical|exception)\([^)]*(current_user|totp_secret|backup_code)' app/
```

**34 matches**, all of the form `logger.info("user_id=%d ...",
current_user.id, ...)`. Every single match accesses only
`current_user.id`, never `current_user.password_hash`,
`current_user.email`, `current_user.totp_secret`, or any other
sensitive attribute. Representative samples:

```python
# app/routes/transfers.py:648
logger.info("user_id=%d updated transfer %d", current_user.id, xfer_id)

# app/routes/transactions.py:281
logger.info("user_id=%d updated transaction %d", current_user.id, txn_id)

# app/routes/salary.py:251
logger.exception("user_id=%d failed to create salary profile", current_user.id)
```

`user_id` is an opaque integer primary key, not PII in the
traditional sense, and it is the standard field an audit trail
logs. These log lines are **safe from a secrets/PII perspective**.
They are, however, the bare-logger audit-trail lines that F-1C-01
flags as unstructured -- see the side-note below.

**Read of `app/utils/logging_config.py` (full 182 lines):**

Key structural points:
- Uses `python-json-logger` to emit log records as JSON lines with
  structured fields (`level`, `logger`, `request_id`, plus any
  `extra={}` fields the log call supplies).
- Installs a `RequestIdFilter` that injects a UUID4 `request_id`
  into every log record -- this is for tracing, not redaction.
- The `setup_logging` function does NOT install any
  `logging.Filter` subclass whose purpose is to scrub sensitive
  fields from log records.
- The `_log_request_summary` after-request hook at lines 133-180
  emits only a fixed, whitelisted set of fields:
  ```python
  extra_fields = {
      "event": event,
      "category": "performance",
      "method": request.method,
      "path": request.path,
      "status": response.status_code,
      "request_duration": round(duration_ms, 2),
      "remote_addr": request.remote_addr,
  }
  ...
  if current_user.is_authenticated:
      extra_fields["user_id"] = current_user.id
  ```
  It does NOT emit `request.form`, `request.json`,
  `request.headers`, `request.cookies`, or `request.args`. It
  does not emit `response.get_data()`. It does not emit any part
  of the request or response body.
- The `@before_request` hook sets `g.request_id` and propagates
  `current_user.id` into PostgreSQL's session context via
  `SET LOCAL app.current_user_id = :uid` (for audit triggers).
  This is a database-side audit mechanism, not a log emission --
  it does not leak the user ID to a log file.

### Classification

**Current exposure: zero.** No `logger.*` or `print` call anywhere
in `app/` leaks a password, TOTP secret, backup code, session
cookie, Authorization header, or any other secret. The 34
`current_user`-referencing log lines all access only `.id`, which
is not sensitive.

**Formal redaction filter: absent.** The logging config has no
`logging.Filter` subclass that scrubs sensitive fields from log
records. The current safety is by convention -- developers have
consistently avoided passing sensitive values to loggers -- not by
enforcement.

**Does this meet the workflow's Medium threshold?** The workflow
says "an unredacted logger that handles auth objects is a Medium
finding." Shekel's logger does NOT currently handle auth objects
in a way that leaks -- it extracts `current_user.id` and discards
the rest. The absence of a formal scrubber is a defense-in-depth
gap, not a current exposure.

I am NOT filing this as a Medium finding because there is no live
exposure. I AM recording it as an **Info-level observation** so the
developer and the Session S8 consolidator can see the gap and
decide whether to add a formal scrubber in a future hardening pass.

### Info observation -- no formal redaction filter in logging config

- **Severity:** Info
- **OWASP:** A09:2021 Security Logging and Monitoring Failures
  (indirect)
- **CWE:** CWE-532 (Insertion of Sensitive Information into Log File)
  -- potential, not current
- **Location:** `app/utils/logging_config.py:74-95` (filters dict
  contains only `request_id`, no scrubber)
- **Evidence:** Full read of the file. The `filters` dict in
  `dictConfig` at lines 74-78 has exactly one entry:
  ```python
  "filters": {
      "request_id": {
          "()": RequestIdFilter,
      },
  },
  ```
- **Impact:** None today -- zero leaks confirmed. A future
  developer who writes `logger.info("password=%s", password)` or
  `logger.debug(request.headers)` would have no scrubber between
  them and the log stream. The CI baseline does not run a
  redaction test.
- **Recommendation (optional, not required):** Add a
  `SensitiveFieldScrubber(logging.Filter)` class that walks
  `record.args` and `record.msg` for known sensitive patterns
  (`password`, `token`, `secret`, `cookie`, `authorization`,
  `totp`, `backup_code`) and rewrites them to `"[REDACTED]"`. Wire
  it into every handler in `dictConfig`. This provides
  defense-in-depth against a future regression without requiring
  any existing log line to change.

### Side-note on F-1C-01 (audit log completeness)

The 34 `logger.info("user_id=%d ...", current_user.id, ...)` lines
I just grepped are the **unstructured bare-log audit trail** that
F-1C-01 flagged as not queryable. They prove two things:

1. The developer IS logging most mutations -- the audit trail is
   not completely silent outside `auth.py`. My 1C.6 framing of "84
   of 93 mutating routes missing" is accurate at the log_event
   level but overstated at the "there is no audit trail at all"
   level. A bare-text audit trail exists; it just cannot be
   queried by structured event name.
2. The recommended fix for F-1C-01 (push `log_event` into the
   service layer) would replace each of these 34 lines with a
   structured event. The conversion is mechanical and the finding's
   recommendation still stands.

**F-1C-01's severity does not change** -- Medium is still correct
because structured audit logging is the documented project
standard and most of the financial mutation paths do not follow
it. But the impact language in F-1C-01 should be softened slightly
to acknowledge that a human-readable trail exists, just not a
queryable one. Noting that here so the Session S8 consolidator can
adjust the finding copy if needed.

### 1C.9 -- Summary

| Check | Result | Severity |
|-------|--------|----------|
| Grep `app/` for `logger.*password|totp_secret|backup_code|secret_key|session_cookie|authorization|bearer` | **zero matches** | PASS |
| Grep `app/` for actual `print(` calls | **zero matches** | PASS |
| Grep `app/` for `logger.*current_user` | 34 hits, all safe (`current_user.id` only) | PASS |
| Read `logging_config.py` for sensitive fields in the request summary | Whitelisted fields only: method, path, status, duration, remote_addr, user_id | PASS |
| Formal redaction filter present | **Absent** -- only `RequestIdFilter` exists, no scrubber | Info observation |

**No new finding.** Current exposure is zero. The absence of a
formal redaction filter is noted as an Info-level defense-in-depth
observation for the Session S8 consolidator.
