"""Minimal, pinned upstream fix: deleting a chat includes compression continuations."""
from pathlib import Path


def patch(root):
    path = Path(root) / 'gateway/platforms/api_server.py'
    old = 'db.delete_session, session_id, sessions_dir=sessions_dir, exclude_active_write_guards=True)'
    new = 'db.delete_session, session_id, sessions_dir=sessions_dir, exclude_active_write_guards=True, include_compression_chain=True)'
    text = path.read_text()
    if text.count(old) != 1:
        raise RuntimeError('Pinned Hermes history patch no longer matches exactly once')
    path.write_text(text.replace(old, new))


if __name__ == '__main__':
    patch('/opt/hermes')
