from pathlib import Path
import py_compile
import re


ROOT = Path(__file__).resolve().parents[1]


def test_chapter_files_exist() -> None:
    assert (ROOT / "chapters/01-agent-loop.md").is_file()
    assert (ROOT / "chapters/02-tools.md").is_file()


def test_readme_links_to_both_chapters() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "./chapters/01-agent-loop.md" in readme
    assert "./chapters/02-tools.md" in readme


def test_reference_example_files_exist() -> None:
    assert (ROOT / "examples/chapter-01/agent.py").is_file()
    assert (ROOT / "examples/chapter-02/agent.py").is_file()
    assert (ROOT / "examples/chapter-02/tools.py").is_file()


def test_old_nested_chapter_layout_is_removed() -> None:
    assert not (ROOT / "chapters/01-agent-loop/README.md").exists()
    assert not (ROOT / "chapters/02-tools/README.md").exists()
    assert not (ROOT / "src/mini_claude").exists()


def test_chapters_link_to_matching_reference_files() -> None:
    chapter_1 = (ROOT / "chapters/01-agent-loop.md").read_text(encoding="utf-8")
    chapter_2 = (ROOT / "chapters/02-tools.md").read_text(encoding="utf-8")

    assert "../examples/chapter-01/agent.py" in chapter_1
    assert "../examples/chapter-02/agent.py" in chapter_2
    assert "../examples/chapter-02/tools.py" in chapter_2


def test_license_preserves_original_attribution() -> None:
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "Copyright (c) 2025 Windy3f3f3f3f" in license_text
    assert "Copyright (c) 2026 Xiaxia1997" in license_text


def test_python_sources_compile() -> None:
    for source in (ROOT / "examples").rglob("*.py"):
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
