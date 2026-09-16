"""Folds duplicate scene memories written before merge-on-write existed (ADR-025).

Merge-on-write stops *new* duplicates. Memories stored earlier stay as they
were: one entry per 0.5 m cell the robot happened to reach. This tool collapses
them with the same rule rag_node now applies live — same look, same zone, same
map session, within the merge radius — keeping, per group, the entry nearest
the group's centre (a pose the robot really reached) and recording on it how
many observations it now stands for. Documents and embeddings are untouched,
so it needs no Ollama.

Dry run by default. `--apply` refuses to run while rag_node is up (ChromaDB's
persistent store is not safe for two writers) and copies the store aside first.

Usage:
    ros2 run robot_rag compact_memory                  # show what would change
    ros2 run robot_rag compact_memory --apply          # do it (backup first)
    ros2 run robot_rag compact_memory --apply --prune-untagged
    ros2 run robot_rag compact_memory --apply --retag-session OLD NEW

`--prune-untagged` also deletes semantic_map memories with no map session at
all — written before ADR-019, invisible to every session's retrieval, and
unable to become valid again. Memories tagged with *another* session are never
touched: reloading that saved map brings them back (ADR-019). task_history is
left alone either way: it is re-ingested from data/logs on every start.

`--retag-session OLD NEW` moves semantic_map memories from one map session to
another. Its one legitimate use: memories written under a session id that
turned out to name the same coordinate frame as NEW — for instance the ids the
agent persisted before sessions were tied to world + spawn pose (ADR-028).
Retagging memories into a frame they were not recorded in would make their
coordinates lie, so it is never done implicitly.

This is a command-line tool, not a ROS node, so it reports with print().
"""

from __future__ import annotations

import argparse
import os
import shutil
import time
from collections.abc import Callable
from pathlib import Path

from robot_rag.chroma_manager import ChromaManager
from robot_rag.scene_merge import DEFAULT_MERGE_RADIUS_M, plan_compaction
from robot_rag.semantic_map import COLLECTION_NAME

WS_ROOT = Path(os.environ.get('ROBOT_WS', Path.home() / 'robot_ws'))
COLLECTIONS = ['semantic_map', 'knowledge_base', 'task_history']


def rag_node_running() -> bool:
    """True if a rag_node process is alive on this machine (reads /proc)."""
    for cmdline in Path('/proc').glob('[0-9]*/cmdline'):
        try:
            argv = cmdline.read_bytes().split(b'\0')
        except OSError:
            continue
        if any(arg.endswith(b'robot_rag/rag_node') for arg in argv):
            return True
    return False


def compact(
    chroma_db_path: str,
    radius: float = DEFAULT_MERGE_RADIUS_M,
    apply: bool = False,
    prune_untagged: bool = False,
    is_rag_running: Callable[[], bool] = rag_node_running,
    retag: tuple[str, str] | None = None,
) -> dict:
    """Plans, and optionally applies, the compaction of semantic_map.

    Args:
        chroma_db_path: ChromaDB persistence directory.
        radius: Merge radius in meters.
        apply: Write the changes; otherwise only report them.
        prune_untagged: Also delete semantic_map memories with no map session.
        is_rag_running: Liveness check for rag_node, injectable for tests.
        retag: Optional (old_session, new_session): semantic_map memories tagged
            old_session are moved to new_session before compacting.

    Returns:
        Dict with "before" and "after" entry counts, the "groups" planned, the
        "pruned" ids, the "retagged" ids, "applied" and the "backup" path.

    Raises:
        RuntimeError: If applying while rag_node is running.
    """
    if apply and is_rag_running():
        raise RuntimeError('rag_node is running — stop the stack before --apply')

    chroma = ChromaManager(chroma_db_path, COLLECTIONS)
    before = chroma.count(COLLECTION_NAME)
    entries = chroma.list_documents(COLLECTION_NAME, max(1, before))
    retagged: list[str] = []
    if retag is not None:
        old_session, new_session = retag
        for entry in entries:
            if (entry['metadata'] or {}).get('map_id') == old_session:
                entry['metadata'] = {**entry['metadata'], 'map_id': new_session}
                retagged.append(entry['id'])
    groups = plan_compaction(entries, radius)
    pruned: list[str] = []
    if prune_untagged:
        pruned = sorted(
            e['id'] for e in entries
            if not (e['metadata'] or {}).get('map_id')
            and isinstance((e['metadata'] or {}).get('pose_x'), (int, float))
        )
        # Groups never span map sessions, so an untagged group is wholly pruned:
        # folding it first would only report a "keep" that is then deleted.
        groups = [g for g in groups if g.keep_id not in set(pruned)]
    dropped = {entry_id for group in groups for entry_id in group.drop_ids}

    report = {
        'before': before, 'groups': groups, 'pruned': pruned, 'retagged': sorted(retagged),
        'after': before - len(dropped) - len(pruned), 'applied': False, 'backup': '',
    }
    if not apply or (not groups and not pruned and not retagged):
        return report

    backup = f'{chroma_db_path.rstrip("/")}.bak-{time.strftime("%Y%m%d-%H%M%S")}'
    shutil.copytree(chroma_db_path, backup)
    by_id = {e['id']: e for e in entries}
    if retagged:
        chroma.update_metadata(
            COLLECTION_NAME, sorted(retagged),
            [{'map_id': by_id[i]['metadata']['map_id']} for i in sorted(retagged)],
        )
    chroma.update_metadata(
        COLLECTION_NAME,
        [g.keep_id for g in groups],
        [
            {**by_id[g.keep_id]['metadata'], 'group_key': g.group_key,
             'observations': g.observations}
            for g in groups
        ],
    )
    chroma.delete(COLLECTION_NAME, sorted(dropped) + pruned)
    report.update(applied=True, backup=backup, after=chroma.count(COLLECTION_NAME))
    return report


def main(args: list[str] | None = None) -> None:
    """Entry point for the compact_memory executable."""
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--chroma-db-path', default=str(WS_ROOT / 'data' / 'chroma_db'))
    parser.add_argument('--radius', type=float, default=DEFAULT_MERGE_RADIUS_M)
    parser.add_argument('--apply', action='store_true', help='write the changes')
    parser.add_argument('--prune-untagged', action='store_true',
                        help='also delete semantic_map memories with no map session')
    parser.add_argument('--retag-session', nargs=2, metavar=('OLD', 'NEW'),
                        help='move semantic_map memories from session OLD to NEW first')
    opts = parser.parse_args(args)

    try:
        report = compact(
            opts.chroma_db_path, opts.radius, opts.apply, opts.prune_untagged,
            retag=tuple(opts.retag_session) if opts.retag_session else None,
        )
    except RuntimeError as exc:
        raise SystemExit(f'✗ {exc}') from exc

    for group in report['groups']:
        print(f'  keep {group.keep_id:22} ← fold {", ".join(group.drop_ids)}')
        print(f'       {group.group_key}  ({group.observations} observations)')
    if report['retagged']:
        print(f'  retag → {opts.retag_session[1]}: {len(report["retagged"])} memories')
    if report['pruned']:
        print(f'  prune (no map session): {", ".join(report["pruned"])}')
    verb = 'now' if report['applied'] else 'would become'
    print(f'semantic_map: {report["before"]} memories {verb} {report["after"]}')
    if report['applied']:
        print(f'backup: {report["backup"]}')
    elif report['groups'] or report['pruned'] or report['retagged']:
        print('dry run — nothing written. Re-run with --apply (stack stopped).')


if __name__ == '__main__':
    main()
