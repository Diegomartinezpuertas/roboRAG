"""Tests for the Markdown section chunker (headers kept with their body)."""

from robot_rag.knowledge_base import chunk_markdown


def test_header_kept_with_its_body():
    md = '## Safety rules\n\nThe robot must stop.\n\n## Nav\n\nGo carefully.'
    chunks = chunk_markdown(md)
    assert len(chunks) == 2
    assert chunks[0].startswith('## Safety rules')
    assert 'must stop' in chunks[0]
    assert chunks[1].startswith('## Nav')
    assert 'carefully' in chunks[1]


def test_no_headers_is_single_chunk():
    assert len(chunk_markdown('just text\n\nmore text')) == 1


def test_empty_and_whitespace():
    assert chunk_markdown('') == []
    assert chunk_markdown('\n\n   \n') == []


def test_preamble_before_first_header_is_its_own_chunk():
    chunks = chunk_markdown('intro line\n\n## Section\n\nbody')
    assert chunks[0] == 'intro line'
    assert chunks[1].startswith('## Section')
    assert 'body' in chunks[1]
