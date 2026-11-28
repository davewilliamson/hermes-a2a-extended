"""Profile-local journals; never persist request text, replies or TLS material."""

import os
import sqlite3
import stat


def private_db(path):
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        info = os.lstat(path)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise OSError("journal must be a private regular file")
    else:
        os.close(fd)
    return sqlite3.connect(path, timeout=0.25)


def new_receipts(path):
    db = private_db(path)
    try:
        with db:
            db.execute("CREATE TABLE IF NOT EXISTS receipt (task TEXT PRIMARY KEY, context TEXT, received TEXT)")
            # The execution owner is process-local. Never replay a prior process's tasks.
            db.execute("DELETE FROM receipt")
        return db
    except BaseException:
        db.close()
        raise
