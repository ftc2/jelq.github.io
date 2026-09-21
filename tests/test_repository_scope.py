from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tools import repository_scope


@pytest.mark.parametrize(
  'paths',
  [
    ['repository.jelq/addon.xml'],
    ['repository.jelq/icon.png'],
    ['tools/catalog.py'],
    ['tools/check_package.py'],
    ['tools/kodi_checker.py'],
    ['uv.lock'],
    ['.github/workflows/pages.yml'],
    ['packages/skin.jelq/skin.jelq-0.3.2.zip', 'tools/check.py'],
  ],
)
def test_repository_changes_need_the_checker(paths: list[str]) -> None:
  assert repository_scope.needs_repository_check(paths)


@pytest.mark.parametrize(
  'paths',
  [
    [],
    ['packages/skin.jelq/skin.jelq-0.3.2.zip'],
    ['packages/script.jelq/script.jelq-0.3.2.zip'],
    ['README.md', 'AGENTS.md'],
    ['tests/test_catalog.py'],
    # A prefix match must stop at the directory boundary.
    ['repository.jelq-notes.md'],
    ['tools/catalog.py.orig'],
  ],
)
def test_package_and_unrelated_changes_skip_the_checker(paths: list[str]) -> None:
  assert not repository_scope.needs_repository_check(paths)


GIT = shutil.which('git')


def _git(root: Path, *arguments: str) -> str:
  if GIT is None:
    pytest.skip('git is required to exercise changed-path detection')
  result = subprocess.run(  # noqa: S603 - fixed git arguments in a temporary repository.
    (GIT, '-C', str(root), *arguments),
    check=True,
    capture_output=True,
    text=True,
  )
  return result.stdout.strip()


def _commit(root: Path, path: str, content: str) -> str:
  target = root / path
  target.parent.mkdir(parents=True, exist_ok=True)
  target.write_text(content, encoding='utf-8')
  _git(root, 'add', path)
  _git(root, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', path)
  return _git(root, 'rev-parse', 'HEAD')


@pytest.fixture
def history(tmp_path: Path) -> tuple[Path, str]:
  _git(tmp_path, 'init', '-q')
  return tmp_path, _commit(tmp_path, 'repository.jelq/addon.xml', '<addon/>')


def test_a_package_import_skips_the_checker(history: tuple[Path, str]) -> None:
  root, base = history
  head = _commit(root, 'packages/skin.jelq/skin.jelq-9.9.9.zip', 'zip')
  assert repository_scope.changed_paths(base, head, root) == [
    'packages/skin.jelq/skin.jelq-9.9.9.zip'
  ]
  assert repository_scope.decide(base, head, root)[0] is False


def test_a_repository_add_on_change_runs_the_checker(history: tuple[Path, str]) -> None:
  root, base = history
  head = _commit(root, 'repository.jelq/addon.xml', '<addon version="2"/>')
  assert repository_scope.decide(base, head, root)[0] is True


@pytest.mark.parametrize('base', ['', '0' * 40, 'f' * 40])
def test_an_unknown_base_runs_the_checker(history: tuple[Path, str], base: str) -> None:
  root, head = history
  # Empty and all-zero bases are what GitHub sends for new branches; an unknown
  # commit cannot be diffed. Either way the safe answer is to check.
  assert repository_scope.changed_paths(base, head, root) is None
  assert repository_scope.decide(base, head, root)[0] is True


def test_the_decision_is_written_for_github_actions(
  history: tuple[Path, str],
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
  capsys: pytest.CaptureFixture[str],
) -> None:
  root, base = history
  head = _commit(root, 'README.md', 'docs')
  output = tmp_path / 'github-output'
  monkeypatch.setenv('GITHUB_OUTPUT', str(output))
  monkeypatch.setattr(repository_scope, 'ROOT', root)
  assert repository_scope.main(['--base', base, '--head', head]) == 0
  assert output.read_text(encoding='utf-8') == 'check=false\n'
  assert 'Kodi add-on checker: false' in capsys.readouterr().out
