"""Software-layer protection for registered original data, not a script sandbox."""
import json
import os
import time
from pathlib import Path

KEY = 'protectedPaths'

def entries(r):
    return r.config(r.database()).get(KEY, [])

def check(r, value, operation='write', recursive=False, registered=None):
    lexical = Path(os.path.abspath(os.path.expanduser(str(value))))
    resolved = lexical.resolve(strict=False)
    for item in entries(r) if registered is None else registered:
        roots = {Path(item['path']), Path(item['resolved']), Path(item['path']).resolve(strict=False)}
        blocked = any(p == root or root in p.parents or (recursive and p in root.parents)
                      for p in (lexical, resolved) for root in roots)
        if not blocked and resolved.is_file():
            blocked = any(root.is_file() and os.path.samefile(resolved, root) for root in roots)
            # Hard links outside a protected directory still modify its original inode.
            if not blocked and resolved.stat().st_nlink > 1:
                identity = (resolved.stat().st_dev, resolved.stat().st_ino)
                def inaccessible(error):
                    raise ValueError('无法核对硬链接的原始数据保护范围，未执行修改') from error
                for root in roots:
                    if not root.is_dir():continue
                    for folder, _, names in os.walk(root, followlinks=False, onerror=inaccessible):
                        for name in names:
                            info = (Path(folder)/name).stat()
                            if (info.st_dev,info.st_ino) == identity:
                                blocked = True;break
                        if blocked:break
                    if blocked:break
        if blocked:
            r.audit(r.database(), 'protection.blocked', {'path':str(lexical), 'operation':operation, 'protectedPath':item['path']})
            raise ValueError('原始数据保护：不能写入、覆盖、移动或删除此路径：' + str(lexical))

def dispatch(r, action, body, actor):
    if action == 'protected_paths':
        return entries(r)
    if actor != 'user':
        raise ValueError('原始数据保护列表只能由用户在服务器管理中修改')
    if action not in ('protected_path_add', 'protected_path_remove') or set(body) != {'path'}:
        raise ValueError('保护路径参数不正确')
    value = body['path']
    if not isinstance(value, str) or not value.strip() or any(c in value for c in ('\0','\n','\r')):
        raise ValueError('请输入原始数据的绝对路径')
    path = Path(value.strip()).expanduser()
    if not path.is_absolute():
        raise ValueError('保护路径必须是绝对路径')
    path = Path(os.path.abspath(path))
    db = r.database()
    db.execute('BEGIN IMMEDIATE')
    try:
        rows = r.config(db).get(KEY, [])
        if action == 'protected_path_add':
            resolved = path.resolve(strict=True)
            if not (resolved.is_dir() or resolved.is_file()):
                raise ValueError('请选择已有的普通文件或目录')
            if not any(row['path'] == str(path) for row in rows):
                rows.append({'path':str(path), 'resolved':str(resolved), 'directory':resolved.is_dir(), 'created':time.time()})
        else:
            rows = [row for row in rows if row['path'] != str(path)]
        db.execute('INSERT OR REPLACE INTO config VALUES(?,?)', (KEY,json.dumps(rows)))
        r.audit(db, 'protection.added' if action.endswith('_add') else 'protection.removed', {'path':str(path)})
        db.commit()
        return rows
    except BaseException:
        db.rollback()
        raise
