"""A helper module of the suite, for
:mod:`tests.test_arch.test_the_suite_rewrites_its_helper_modules`: neither a test module nor
a conftest, so pytest rewrites its assert only because the repository-root ``conftest.py``
registers ``tests`` for rewriting."""


def assert_is_one(value: int) -> None:
    """Assert that ``value`` is one."""
    assert value == 1
