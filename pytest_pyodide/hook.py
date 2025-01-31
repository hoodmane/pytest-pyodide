import ast
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from _pytest.assertion.rewrite import AssertionRewritingHook, rewrite_asserts
from _pytest.python import (
    pytest_pycollect_makemodule as orig_pytest_pycollect_makemodule,
)

from .utils import parse_xfail_browsers

RUNTIMES = ["firefox", "chrome", "node"]


def pytest_configure(config):

    config.addinivalue_line(
        "markers",
        "skip_refcount_check: Don't run refcount checks",
    )

    config.addinivalue_line(
        "markers",
        "skip_pyproxy_check: Don't run pyproxy allocation checks",
    )

    config.addinivalue_line(
        "markers",
        "driver_timeout: Set script timeout in WebDriver",
    )

    config.addinivalue_line(
        "markers",
        "xfail_browsers: xfail a test in specific browsers",
    )

    pytest.pyodide_dist_dir = config.getoption("--dist-dir")


@pytest.hookimpl(tryfirst=True)
def pytest_addoption(parser):
    group = parser.getgroup("general")
    group.addoption(
        "--dist-dir",
        action="store",
        default="pyodide",
        help="Path to the pyodide dist directory",
        type=Path,
    )
    group.addoption(
        "--runner",
        default="selenium",
        choices=["selenium", "playwright"],
        help="Select testing frameworks, selenium or playwright (default: %(default)s)",
    )
    group.addoption(
        "--rt",
        "--runtime",
        dest="runtime",
        default="node",
        choices=RUNTIMES + ["all", "host"],
        help="Select runtime, firefox, chrome, node, all, or host (default: %(default)s)",
    )


# Handling for pytest assertion rewrites
# First we find the pytest rewrite config. It's an attribute of the pytest
# assertion rewriting meta_path_finder, so we locate that to get the config.


def _get_pytest_rewrite_config() -> Any:
    for meta_path_finder in sys.meta_path:
        if isinstance(meta_path_finder, AssertionRewritingHook):
            break
    else:
        return None
    return meta_path_finder.config


# Now we need to parse the ast of the files, rewrite the ast, and store the
# original and rewritten ast into dictionaries. `run_in_pyodide` will look the
# ast up in the appropriate dictionary depending on whether or not it is using
# pytest assert rewrites.

REWRITE_CONFIG = _get_pytest_rewrite_config()
del _get_pytest_rewrite_config

ORIGINAL_MODULE_ASTS: dict[str, ast.Module] = {}
REWRITTEN_MODULE_ASTS: dict[str, ast.Module] = {}


def pytest_pycollect_makemodule(module_path: Path, path: Any, parent: Any) -> None:
    source = module_path.read_bytes()
    strfn = str(module_path)
    tree = ast.parse(source, filename=strfn)
    ORIGINAL_MODULE_ASTS[strfn] = tree
    tree2 = deepcopy(tree)
    rewrite_asserts(tree2, source, strfn, REWRITE_CONFIG)
    REWRITTEN_MODULE_ASTS[strfn] = tree2
    orig_pytest_pycollect_makemodule(module_path, parent)


def pytest_generate_tests(metafunc: Any) -> None:
    if "runtime" in metafunc.fixturenames:
        runtime = metafunc.config.option.runtime

        if runtime == "all":
            runtime = RUNTIMES

        metafunc.parametrize("runtime", [runtime], scope="module")


def pytest_collection_modifyitems(items: list[Any]) -> None:
    for item in items:
        if not hasattr(item, "fixturenames"):
            # Some items like DoctestItem has no fixture
            continue
        if item.config.option.runtime == "host" and "runtime" in item.fixturenames:
            item.add_marker(pytest.mark.skip(reason="Non-host test"))
        elif (
            item.config.option.runtime != "host" and "runtime" not in item.fixturenames
        ):
            item.add_marker(pytest.mark.skip(reason="Host test"))


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):

    browser = None
    for fixture in item._fixtureinfo.argnames:
        if fixture.startswith("selenium"):
            browser = item.funcargs[fixture]
            break

    if not browser:
        yield
        return

    xfail_msg = parse_xfail_browsers(item).get(browser.browser, None)
    if xfail_msg is not None:
        pytest.xfail(xfail_msg)

    yield
    return
