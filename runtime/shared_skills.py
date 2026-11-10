"""Fixed owner-authored shared skill root; never route through a profile home."""
import os
from pathlib import Path
import stat


def shared_skill_roots(root=Path('/workspace/.hermes/shared-skills')):
    root = Path(root).absolute()
    try:
        for parent in reversed((root, *root.parents)):
            if not stat.S_ISDIR(parent.lstat().st_mode):
                raise RuntimeError('Shared skill root must not follow links')
    except FileNotFoundError:
        return []
    if root.stat().st_mode & 0o022:
        raise RuntimeError('Shared skill root must not be group or world writable')
    count = 0
    for folder, directories, files in os.walk(root, followlinks=False):
        if len(Path(folder).relative_to(root).parts) > 10:
            raise RuntimeError('Shared skill tree exceeds bounds')
        for name in directories + files:
            count += 1
            info = (Path(folder) / name).lstat()
            if count > 4096 or not (stat.S_ISDIR(info.st_mode) or
                    (stat.S_ISREG(info.st_mode) and info.st_nlink == 1)):
                raise RuntimeError('Shared skill tree contains unsupported entries')
    return [root]
