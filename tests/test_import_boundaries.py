import ast
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1] / "app"
ALLOWED = (APP_ROOT / "llm" / "openai_provider.py").resolve()


def _imports_openai(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "openai" or alias.name.startswith("openai."):
                    return True
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "openai" or module.startswith("openai."):
                return True
    return False


def test_openai_imported_only_in_adapter():
    """T-11 / R-07: openai is imported nowhere outside app/llm/openai_provider.py."""
    offenders = [
        str(path.relative_to(APP_ROOT.parent))
        for path in APP_ROOT.rglob("*.py")
        if _imports_openai(path) and path.resolve() != ALLOWED
    ]
    assert offenders == []
