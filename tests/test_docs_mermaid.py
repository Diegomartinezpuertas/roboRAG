"""Guards the Mermaid diagrams the documentation renders on GitHub.

GitHub's Mermaid refused the README's architecture diagram with "Parse error …
got 'PS'" because an edge label carried parentheses — `-->|/robot/cmd_vel_manual
(WASD)|` — and the parser reads `(` as the start of a node shape. Rendering the
blocks properly needs a browser; this checks the one rule that broke, over every
block in the committed docs, without one.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BLOCK = re.compile(r'```mermaid\n(.*?)```', re.S)
# An edge with a |label|: --> |x|, --- |x|, -.-> |x|, ==> |x|
EDGE_LABEL = re.compile(r'(?:-->|---|-\.->|==>)\s*\|([^|]*)\|')
NEEDS_QUOTES = ('(', ')', '[', ']', '{', '}')


def markdown_files() -> list[Path]:
    """Every committed Markdown file; docs/promo is local-only working material."""
    files = sorted(ROOT.glob('*.md')) + sorted(ROOT.glob('docs/**/*.md'))
    return [f for f in files if 'promo' not in f.parts]


def mermaid_blocks() -> list[tuple[Path, str]]:
    return [(f, block) for f in markdown_files() for block in BLOCK.findall(f.read_text())]


def test_the_docs_still_contain_the_diagrams_this_guards():
    assert mermaid_blocks(), 'no Mermaid blocks found — has the README diagram moved?'


def test_edge_labels_with_brackets_are_quoted():
    offenders = []
    for path, block in mermaid_blocks():
        for line in block.splitlines():
            for label in EDGE_LABEL.findall(line):
                bare = label.strip()
                if any(char in bare for char in NEEDS_QUOTES) and not bare.startswith('"'):
                    offenders.append(f'{path.relative_to(ROOT)}: {line.strip()}')
    assert not offenders, (
        'quote these edge labels, GitHub will not parse them:\n' + '\n'.join(offenders)
    )


@pytest.mark.parametrize('label, ok', [
    ('/robot/goal', True),
    ('"/robot/cmd_vel_manual (WASD)"', True),
    ('/robot/cmd_vel_manual (WASD)', False),
    ('navigate / explore', True),
    ('reads zones[0]', False),
])
def test_the_rule_itself(label, ok):
    """The check is only worth having if it accepts and rejects the right shapes."""
    bare = label.strip()
    quoted = not any(char in bare for char in NEEDS_QUOTES) or bare.startswith('"')
    assert quoted is ok
