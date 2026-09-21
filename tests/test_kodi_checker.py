from __future__ import annotations

import gzip
import inspect
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Iterator

import pytest
from kodi_addon_checker import check_addon  # type: ignore[import-untyped]
from kodi_addon_checker.addons.Repository import Repository  # type: ignore[import-untyped]
from requests.adapters import HTTPAdapter  # type: ignore[import-untyped]

from tools import kodi_checker

INDEX_PATH = '/addons/omega/addons.xml.gz'
INDEX = gzip.compress(b'<addons><addon id="script.example" version="1.0.0"/></addons>')
# A well-formed gzip stream whose XML is not an index; the checker sets
# `addons = []` before it fails to parse this.
NOT_AN_INDEX = gzip.compress(b'<html>Service unavailable')
# Unpatched, the adapter's backoff turns one bad source into minutes.
QUICK_FAILURE_SECONDS = 10


class Source:
  """A local stand-in for a mirror, answering with a fixed behavior."""

  def __init__(self, behavior: str) -> None:
    self.behavior = behavior
    self.requests = 0
    self.base = ''


@pytest.fixture
def start_source() -> Iterator[Callable[[str], Source]]:
  servers: list[ThreadingHTTPServer] = []

  def start(behavior: str) -> Source:
    source = Source(behavior)

    class Handler(BaseHTTPRequestHandler):
      def do_GET(self) -> None:
        source.requests += 1
        if source.behavior == 'error':
          self.send_response(503)
          self.end_headers()
          return
        body = INDEX if source.behavior == 'ok' else NOT_AN_INDEX
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

      def log_message(self, *_args: Any) -> None:
        pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    servers.append(server)
    source.base = f'http://127.0.0.1:{server.server_address[1]}'
    return source

  yield start
  for server in servers:
    server.shutdown()
    server.server_close()


@pytest.fixture(autouse=True)
def isolate(monkeypatch: pytest.MonkeyPatch) -> None:
  # install() patches the class for the whole process; undo it after each test,
  # and keep source order deterministic.
  monkeypatch.setattr(Repository, '__init__', Repository.__init__)
  monkeypatch.setattr(Repository._adapter, 'max_retries', Repository._adapter.max_retries)  # noqa: SLF001
  monkeypatch.setattr(kodi_checker.random, 'shuffle', lambda _items: None)


def use_sources(monkeypatch: pytest.MonkeyPatch, mirrors: list[Source], redirector: Source) -> str:
  monkeypatch.setattr(kodi_checker, 'MIRRORS', tuple(mirror.base for mirror in mirrors))
  monkeypatch.setattr(kodi_checker, 'REDIRECTOR', redirector.base)
  kodi_checker.install()
  return redirector.base + INDEX_PATH


def test_the_checker_internals_this_relies_on_are_unchanged() -> None:
  # These are private to kodi-addon-checker 0.0.36. If an upgrade changes them,
  # revisit tools/kodi_checker.py rather than letting the patch silently misfire.
  assert check_addon.ROOT_URL == kodi_checker.REDIRECTOR + '/addons/{branch}/addons.xml.gz'
  assert list(inspect.signature(Repository.__init__).parameters) == ['self', 'version', 'path']
  assert isinstance(Repository._adapter, HTTPAdapter)  # noqa: SLF001
  source = inspect.getsource(Repository.__init__)
  assert 'except requests.exceptions.RequestException:' in source
  # The partial-state cleanup relies on this ordering.
  assert source.index('self.addons = []') < source.index('ET.fromstring(content)')
  assert 'for addon in self.addons' in inspect.getsource(Repository.__contains__)


def test_a_failing_mirror_is_skipped_for_the_next(
  monkeypatch: pytest.MonkeyPatch, start_source: Callable[[str], Source]
) -> None:
  down, up, redirector = start_source('error'), start_source('ok'), start_source('error')
  path = use_sources(monkeypatch, [down, up], redirector)

  repository = Repository('omega', path)

  assert 'script.example' in repository
  assert (down.requests, up.requests, redirector.requests) == (1, 1, 0)


def test_a_mirror_returning_something_else_does_not_count_as_empty(
  monkeypatch: pytest.MonkeyPatch, start_source: Callable[[str], Source]
) -> None:
  wrong, up, redirector = start_source('garbage'), start_source('ok'), start_source('error')
  path = use_sources(monkeypatch, [wrong, up], redirector)

  repository = Repository('omega', path)

  assert 'script.example' in repository
  assert wrong.requests == up.requests == 1


def test_the_redirector_is_the_last_resort(
  monkeypatch: pytest.MonkeyPatch, start_source: Callable[[str], Source]
) -> None:
  down, redirector = start_source('error'), start_source('ok')
  path = use_sources(monkeypatch, [down], redirector)

  assert 'script.example' in Repository('omega', path)
  assert (down.requests, redirector.requests) == (1, 1)


def test_no_source_fails_quickly_and_names_what_was_tried(
  monkeypatch: pytest.MonkeyPatch, start_source: Callable[[str], Source]
) -> None:
  down, wrong, redirector = start_source('error'), start_source('garbage'), start_source('error')
  path = use_sources(monkeypatch, [down, wrong], redirector)

  started = time.monotonic()
  with pytest.raises(kodi_checker.IndexUnavailableError) as error:
    Repository('omega', path)

  assert time.monotonic() - started < QUICK_FAILURE_SECONDS
  for source in (down, wrong, redirector):
    assert source.base in str(error.value)
  assert 'not a problem with the checked add-on' in str(error.value)


def test_mirrors_are_shuffled_and_the_redirector_stays_last(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setattr(kodi_checker.random, 'shuffle', lambda items: items.reverse())
  assert kodi_checker.sources() == [*reversed(kodi_checker.MIRRORS), kodi_checker.REDIRECTOR]


def test_an_unexpected_index_url_is_reported() -> None:
  kodi_checker.install()
  with pytest.raises(kodi_checker.IndexUnavailableError, match='may have changed'):
    Repository('omega', 'https://example.invalid/addons/omega/addons.xml.gz')


def test_unpatched_the_checker_hides_the_failure(
  start_source: Callable[[str], Source],
) -> None:
  down = start_source('error')
  Repository._adapter.max_retries = kodi_checker.ONE_TRY_PER_SOURCE  # noqa: SLF001 - keep it fast.

  repository = Repository('omega', down.base + INDEX_PATH)

  # This is the upstream behavior the wrapper exists to replace.
  assert not hasattr(repository, 'addons')
  with pytest.raises(AttributeError):
    'script.example' in repository  # noqa: B015
