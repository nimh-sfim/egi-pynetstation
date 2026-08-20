"""Keep the declared version consistent across the project.

Before 2.0.0 these had silently diverged: pyproject.toml said 1.0.1,
setup.py and __init__.py said 1.0.0, and the docs said 0.0.0rc1. Only
pyproject.toml affects what PyPI receives, which is exactly why the others
drifted unnoticed.
"""

import re
from pathlib import Path

import egi_pynetstation


ROOT = Path(__file__).resolve().parents[2]


def read_declared(path, pattern):
    text = (ROOT / path).read_text(encoding='utf-8')
    match = re.search(pattern, text, re.MULTILINE)
    assert match, f'no version found in {path}'
    return match.group(1)


def test_pyproject_matches_package_version():
    # poetry-core is the build backend, so pyproject.toml is what PyPI sees.
    declared = read_declared('pyproject.toml', r'^version = "([^"]+)"')
    assert declared == egi_pynetstation.__version__


def test_setup_py_matches_package_version():
    declared = read_declared('setup.py', r"version='([^']+)'")
    assert declared == egi_pynetstation.__version__


def test_docs_release_matches_package_version():
    declared = read_declared('docs/conf.py', r"^release = '([^']+)'")
    assert declared == egi_pynetstation.__version__


def test_setup_py_name_matches_pyproject():
    """setup.py once declared name='eci', a package that does not exist.

    poetry-core is the build backend, so setup.py is never exercised by pip
    and the mismatch went unnoticed. If it is kept as a fallback it has to
    agree with pyproject.toml, or a setuptools-based build would publish
    under the wrong name.
    """
    pyproject = read_declared('pyproject.toml', r'^name = "([^"]+)"')
    setup_py = read_declared('setup.py', r"name='([^']+)'")
    assert setup_py == pyproject == 'egi_pynetstation'


def test_setup_py_url_matches_pyproject_repository():
    pyproject = read_declared('pyproject.toml', r'^repository = "([^"]+)"')
    setup_py = read_declared('setup.py', r"url='([^']+)'")
    assert setup_py == pyproject


def test_setup_py_python_requires_matches_pyproject():
    # pyproject uses a caret constraint; setup.py uses a floor. Compare the
    # minimum version they each declare.
    pyproject = read_declared('pyproject.toml', r'^python = "\^([\d.]+)"')
    setup_py = read_declared('setup.py', r"python_requires='>=([\d.]+)'")
    assert setup_py == pyproject


# --- the declared Python floor -------------------------------------------

"""These drifted the same way the version numbers once did.

pyproject.toml declared ^3.8 while CI tested only 3.10, and eci_env.yml
asked for 3.7 -- below even the declared minimum. Nothing caught it,
because nothing compared them.
"""

MIN_PYTHON = '3.9'


def test_pyproject_declares_the_supported_floor():
    declared = read_declared('pyproject.toml', r'^python = "\^([\d.]+)"')
    assert declared == MIN_PYTHON


def test_setup_py_declares_the_same_floor():
    declared = read_declared('setup.py', r"python_requires='>=([\d.]+)'")
    assert declared == MIN_PYTHON


def test_conda_env_is_not_below_the_floor():
    declared = read_declared('eci_env.yml', r'- python>=([\d.]+)')
    assert declared == MIN_PYTHON


def test_ci_matrix_covers_the_floor():
    """A declared minimum nobody tests is a guess, not a guarantee."""
    workflow = (ROOT / '.github/workflows/python-app.yml').read_text()
    versions = re.findall(r'"(\d+\.\d+)"', workflow)
    assert MIN_PYTHON in versions, f'{MIN_PYTHON} is declared but untested'


def test_ci_lints_a_directory_that_exists():
    """The old command targeted `eci`, which has never existed here."""
    workflow = (ROOT / '.github/workflows/python-app.yml').read_text()
    assert 'flake8 egi_pynetstation' in workflow
    # A check that cannot fail is not a check. Comments may mention the
    # flag; what matters is that no command actually passes it.
    commands = [
        line for line in workflow.splitlines()
        if not line.lstrip().startswith('#')
    ]
    assert not any('--exit-zero' in line for line in commands)


def test_author_matches_between_packaging_files():
    """setup.py and pyproject.toml must name the same author.

    These had diverged: pyproject.toml said "Joshua Teves
    <jbtevespro@gmail.com>" while setup.py said "Joshua B. Teves" at an
    nih.gov address. Only pyproject.toml reaches PyPI, so the setup.py
    copy was wrong in public view and nothing said so.
    """
    poetry = read_declared('pyproject.toml', r'^authors = \["([^"]+)"\]')
    name = read_declared('setup.py', r"author='([^']+)'")
    email = read_declared('setup.py', r"author_email='([^']+)'")
    assert poetry == f'{name} <{email}>'


def test_author_matches_citation_metadata():
    """The packaged author is the one CITATION.cff credits first."""
    given = read_declared('CITATION.cff', r'^    given-names: (.+)$')
    family = read_declared('CITATION.cff', r'^  - family-names: (.+)$')
    assert read_declared('setup.py', r"author='([^']+)'") == (
        f'{given.strip()} {family.strip()}'
    )
