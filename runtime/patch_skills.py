"""Fix a pinned upstream catalogue call whose removed parameter causes HTTP 500."""
from pathlib import Path


def patch(root):
    path=Path(root)/'gateway/platforms/api_server.py'
    old='''find_all_skills(
                    skip_disabled=False, include_editorial=True
                )'''
    new='find_all_skills(skip_disabled=False)'
    text=path.read_text()
    if text.count(old)!=1:
        raise RuntimeError('Pinned Hermes skills patch no longer matches exactly once')
    path.write_text(text.replace(old,new))


if __name__=='__main__':patch('/opt/hermes')
