from pathlib import Path
import py_compile
import re


ROOT = Path(__file__).resolve().parents[1]


def test_chapter_files_exist() -> None:
    assert (ROOT / "chapters/01-agent-loop/README.md").is_file()
    assert (ROOT / "chapters/02-tools/README.md").is_file()


def test_python_sources_compile() -> None:
    for source in (ROOT / "src/mini_claude").glob("*.py"):
        py_compile.compile(str(source), doraise=True)


def test_repository_contains_no_live_api_key() -> None:
    secret_pattern = re.compile(r"sk-[A-Za-z0-9_-]{16,}")
    ignored_directories = {".git", ".venv", ".pytest_cache", "__pycache__"}
    for path in ROOT.rglob("*"):
        if ignored_directories.intersection(path.parts) or not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        assert secret_pattern.search(content) is None, f"疑似密钥出现在 {path}"
