"""Handbook corpus registry and search adapter."""
from __future__ import annotations
from dataclasses import dataclass
import os
from pathlib import Path
import re
from typing import Any, Callable, Iterable, Mapping
import yaml
from corrosion_engineering_mcp.core.errors import CorrosionPublicError
CORPUS_ENV_VAR = 'CORROSION_CORPUS_PATH'
DEFAULT_SOURCES_PATH = Path(__file__).resolve().parents[2] / 'data' / 'handbook_sources.yaml'
TEXT_EXTENSIONS = {'.txt', '.md', '.rst', '.csv', '.json', '.yaml', '.yml'}

class CorpusUnavailableError(CorrosionPublicError):
    """Raised when no handbook corpus is configured."""

    def __init__(self, *, query: str | None=None) -> None:
        super().__init__('CORPUS_UNAVAILABLE', f'CORPUS_UNAVAILABLE: handbook corpus is not configured; set {CORPUS_ENV_VAR} to a directory containing licensed/public source extracts.', details={'env_var': CORPUS_ENV_VAR, 'query': query, 'registry': str(DEFAULT_SOURCES_PATH)})

class OutOfScopeError(CorrosionPublicError):
    """Raised when the configured corpus cannot support a query."""

    def __init__(self, query: str) -> None:
        super().__init__('OUT_OF_SCOPE', 'OUT_OF_SCOPE: configured handbook corpus did not contain support for this query.', details={'query': query})

@dataclass(frozen=True)
class HandbookSource:
    """Registry entry for a handbook source pointer."""
    retrieval_id: str
    title: str
    license_flag: str
    access: str
    citation: str
    abstract: str
    topics: tuple[str, ...]
    pointer: str | None = None
    corpus_relative_path: str | None = None

def _coerce_source(raw: Mapping[str, Any]) -> HandbookSource:
    license_flag = str(raw['license_flag'])
    if license_flag not in {'public', 'licensed'}:
        raise ValueError(f'Invalid license_flag for {raw.get('retrieval_id')}: {license_flag}')
    return HandbookSource(retrieval_id=str(raw['retrieval_id']), title=str(raw['title']), license_flag=license_flag, access=str(raw['access']), pointer=str(raw['pointer']) if raw.get('pointer') else None, corpus_relative_path=str(raw['corpus_relative_path']) if raw.get('corpus_relative_path') else None, citation=str(raw['citation']), abstract=str(raw['abstract']), topics=tuple((str(topic) for topic in raw.get('topics', []))))

def load_handbook_sources(path: Path | str=DEFAULT_SOURCES_PATH) -> dict[str, HandbookSource]:
    """Load the handbook source registry keyed by stable retrieval ID."""
    registry_path = Path(path)
    with registry_path.open('r', encoding='utf-8') as handle:
        payload = yaml.safe_load(handle)
    if not isinstance(payload, dict) or not isinstance(payload.get('sources'), list):
        raise ValueError('handbook_sources.yaml must contain a top-level sources list')
    sources: dict[str, HandbookSource] = {}
    for raw_source in payload['sources']:
        source = _coerce_source(raw_source)
        if source.retrieval_id in sources:
            raise ValueError(f'Duplicate handbook retrieval_id: {source.retrieval_id}')
        sources[source.retrieval_id] = source
    return sources

def _tokenize(text: str) -> set[str]:
    return {token for token in re.findall('[a-z0-9][a-z0-9_\\-+]*', text.lower()) if len(token) > 1}

def _excerpt(text: str, query_tokens: set[str], *, limit: int=1800) -> str:
    if len(text) <= limit:
        return text.strip()
    lowered = text.lower()
    positions = [lowered.find(token) for token in query_tokens if lowered.find(token) >= 0]
    center = min(positions) if positions else 0
    start = max(0, center - limit // 3)
    end = min(len(text), start + limit)
    return text[start:end].strip()

class HandbookCorpusAdapter:
    """Search adapter for injected MCP search or a configured local corpus."""

    def __init__(self, mcp_search_function: Callable[..., list[dict[str, Any]]] | None=None, *, corpus_path: str | Path | None=None, sources_path: Path | str=DEFAULT_SOURCES_PATH) -> None:
        self._mcp_search = mcp_search_function
        self._corpus_path = Path(corpus_path) if corpus_path else None
        self.sources = load_handbook_sources(sources_path)

    def search(self, query: str, *, mode: str='rerank', top_k: int=5) -> list[dict[str, Any]]:
        """Search the configured handbook corpus, preserving license metadata."""
        if self._mcp_search:
            raw_results = self._mcp_search(query=query, mode=mode, top_k=top_k)
            results = [self._normalize_external_result(result) for result in raw_results]
        else:
            corpus_root = self._resolve_corpus_root(query)
            results = self._search_local_files(corpus_root, query=query, top_k=top_k)
        if not results:
            raise OutOfScopeError(query)
        return results

    def _resolve_corpus_root(self, query: str) -> Path:
        configured = self._corpus_path or (Path(os.environ[CORPUS_ENV_VAR]) if os.environ.get(CORPUS_ENV_VAR) else None)
        if configured is None or not configured.exists() or (not configured.is_dir()):
            raise CorpusUnavailableError(query=query)
        return configured

    def _normalize_external_result(self, result: Mapping[str, Any]) -> dict[str, Any]:
        retrieval_id = str(result.get('retrieval_id') or result.get('source_id') or '')
        source = self.sources.get(retrieval_id)
        if source is None:
            source = HandbookSource(retrieval_id=retrieval_id or 'EXTERNAL_UNREGISTERED', title=str(result.get('path') or 'External corpus result'), license_flag=str(result.get('license_flag') or 'public'), access='external_search', pointer=None, corpus_relative_path=None, citation=str(result.get('citation') or result.get('path') or 'External corpus'), abstract=str(result.get('abstract') or ''), topics=())
        text = str(result.get('text') or '')
        safe_text = self._license_safe_text(source, text=text, relative_path=str(result.get('path') or ''))
        return {**dict(result), 'text': safe_text, 'retrieval_id': source.retrieval_id, 'license_flag': source.license_flag, 'citation': source.citation, 'abstract': source.abstract}

    def _search_local_files(self, corpus_root: Path, *, query: str, top_k: int) -> list[dict[str, Any]]:
        query_tokens = _tokenize(query)
        candidates: list[tuple[float, Path, str, HandbookSource | None]] = []
        for path in self._iter_text_files(corpus_root):
            try:
                text = path.read_text(encoding='utf-8', errors='ignore')
            except OSError:
                continue
            text_tokens = _tokenize(text)
            score = len(query_tokens & text_tokens) / max(len(query_tokens), 1)
            if score <= 0:
                continue
            candidates.append((score, path, text, self._source_for_path(corpus_root, path)))
        candidates.sort(key=lambda item: item[0], reverse=True)
        results = []
        for score, path, text, source in candidates[:top_k]:
            relative_path = str(path.relative_to(corpus_root))
            source = source or self._unregistered_source(relative_path)
            safe_text = self._license_safe_text(source, text=_excerpt(text, query_tokens), relative_path=relative_path)
            results.append({'text': safe_text, 'score': score, 'path': f'{CORPUS_ENV_VAR}/{relative_path}', 'retrieval_id': source.retrieval_id, 'license_flag': source.license_flag, 'citation': source.citation, 'abstract': source.abstract})
        return results

    def _iter_text_files(self, corpus_root: Path) -> Iterable[Path]:
        for path in corpus_root.rglob('*'):
            if path.is_file() and path.suffix.lower() in TEXT_EXTENSIONS:
                yield path

    def _source_for_path(self, corpus_root: Path, path: Path) -> HandbookSource | None:
        relative = path.relative_to(corpus_root).as_posix()
        for source in self.sources.values():
            source_prefix = source.corpus_relative_path
            if source_prefix and relative.startswith(source_prefix.strip('/') + '/'):
                return source
            if source.retrieval_id.lower() in relative.lower():
                return source
        return None

    def _unregistered_source(self, relative_path: str) -> HandbookSource:
        return HandbookSource(retrieval_id='LOCAL_UNREGISTERED', title='Local unregistered corpus file', license_flag='licensed', access='external_corpus', pointer=None, corpus_relative_path=relative_path, citation='Local unregistered corpus file', abstract='Local corpus file not mapped to data/handbook_sources.yaml.', topics=())

    def _license_safe_text(self, source: HandbookSource, *, text: str, relative_path: str) -> str:
        if source.license_flag == 'public':
            return text
        location = f'{CORPUS_ENV_VAR}/{relative_path}' if relative_path else CORPUS_ENV_VAR
        return f'{source.citation}. {source.abstract} Licensed source; see corpus path {location} for full text.'
__all__ = ['CORPUS_ENV_VAR', 'DEFAULT_SOURCES_PATH', 'CorpusUnavailableError', 'HandbookCorpusAdapter', 'HandbookSource', 'OutOfScopeError', 'load_handbook_sources']
