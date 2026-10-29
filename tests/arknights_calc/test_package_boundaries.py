import ast
from pathlib import Path

import arkdata

ARKDATA_DIR = Path(arkdata.__file__).parent


def _imported_modules(path):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module


def test_arkdata_never_imports_arknights_calc():
    # arkdata is the generic library; only arknights_calc may depend on it, never the reverse.
    offenders = [
        (path.relative_to(ARKDATA_DIR).as_posix(), module)
        for path in ARKDATA_DIR.rglob('*.py')
        for module in _imported_modules(path)
        if module == 'arknights_calc' or module.startswith('arknights_calc.')
    ]
    assert offenders == []


def test_boundary_check_finds_arkdata_modules():
    assert len(list(ARKDATA_DIR.rglob('*.py'))) >= 10
