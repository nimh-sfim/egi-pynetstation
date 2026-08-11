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
