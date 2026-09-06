"""
The document memory.

Everything the system has seen before, so a new document can be asked the only
question that matters before it is paid: **have we had this one already?**

This is the second source the project has now reached for twice from opposite
directions. Measured on 15 real documents, 58% of field errors sit in fields
nothing can check — invoice numbers, names, dates — because there is nothing to
compare them against. History is that something.

SQLite, from the standard library. A memory that needs a database server before
it runs is a memory that does not run in a bookkeeping office.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from typing import Iterator, Optional

from src.ledger.canonical import CanonicalDocument, number_core

__all__ = ["DocumentStore"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_key   TEXT NOT NULL,
    supplier_name  TEXT,
    supplier_tax_id TEXT,
    number_key     TEXT NOT NULL,
    number_core    TEXT,
    doc_number     TEXT,
    issue_date     TEXT,
    amount_key     INTEGER,
    total_amount   REAL,
    currency       TEXT,
    channel        TEXT,
    source_ref     TEXT,
    doc_type       TEXT,
    recorded_at    TEXT DEFAULT CURRENT_TIMESTAMP
);
-- The three ways one document is looked for again, each its own index because
-- each is a different question:
CREATE INDEX IF NOT EXISTS ix_identity ON documents(supplier_key, number_key);
CREATE INDEX IF NOT EXISTS ix_core     ON documents(supplier_key, number_core);
CREATE INDEX IF NOT EXISTS ix_amount   ON documents(supplier_key, amount_key);
"""


class DocumentStore:
    """
    A record of documents already seen.

    `:memory:` by default so tests and the demo need no file on disk; pass a
    path to keep it.
    """

    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with closing(self._conn.cursor()) as cur:
            cur.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "DocumentStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- writing -------------------------------------------------------------

    def record(self, doc: CanonicalDocument) -> int:
        """
        Remember a document. Returns its row id.

        Recording is deliberately separate from checking: a caller decides
        whether a flagged document should join the history, and on a duplicate
        the answer is usually no.
        """
        with closing(self._conn.cursor()) as cur:
            cur.execute(
                """INSERT INTO documents
                   (supplier_key, supplier_name, supplier_tax_id, number_key,
                    number_core, doc_number, issue_date, amount_key,
                    total_amount, currency, channel, source_ref, doc_type)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (doc.supplier_key, doc.supplier_name, doc.supplier_tax_id,
                 doc.number_key, doc.number_core, doc.doc_number,
                 doc.issue_date.isoformat() if doc.issue_date else None,
                 doc.amount_key, doc.total_amount, doc.currency,
                 doc.channel, doc.source_ref, doc.doc_type))
            self._conn.commit()
            return int(cur.lastrowid)

    def record_many(self, docs: Iterator[CanonicalDocument]) -> int:
        return sum(1 for d in docs if self.record(d))

    # -- reading -------------------------------------------------------------

    def by_identity(self, doc: CanonicalDocument) -> list[sqlite3.Row]:
        """Same supplier, same document number, character for character."""
        if not doc.supplier_key or not doc.number_key:
            return []
        return self._q("""SELECT * FROM documents
                          WHERE supplier_key=? AND number_key=?""",
                       (doc.supplier_key, doc.number_key))

    def by_number_core(self, doc: CanonicalDocument) -> list[sqlite3.Row]:
        """
        Same supplier, and a document number whose digit core matches or is a
        suffix of the other. This is the one that catches 'GIB-12345' against
        'GIB2026000012345' — the short form a person types from a PDF.

        A core shorter than four digits is not evidence of anything, so it is
        not looked up: '5' would match half the ledger.
        """
        core = doc.number_core
        if not doc.supplier_key or len(core) < 4:
            return []
        rows = self._q("""SELECT * FROM documents
                          WHERE supplier_key=? AND number_core IS NOT NULL
                            AND number_core != ''""",
                       (doc.supplier_key,))
        out = []
        for r in rows:
            other = r["number_core"] or ""
            if len(other) < 4:
                continue
            if other == core or other.endswith(core) or core.endswith(other):
                out.append(r)
        return out

    def by_amount_near_date(self, doc: CanonicalDocument,
                            days: int = 30) -> list[sqlite3.Row]:
        """Same supplier, same amount to the kuruş, issued within `days`."""
        if not doc.supplier_key or doc.amount_key is None:
            return []
        rows = self._q("""SELECT * FROM documents
                          WHERE supplier_key=? AND amount_key=?""",
                       (doc.supplier_key, doc.amount_key))
        if doc.issue_date is None:
            return rows
        lo = (doc.issue_date - timedelta(days=days)).isoformat()
        hi = (doc.issue_date + timedelta(days=days)).isoformat()
        return [r for r in rows
                if r["issue_date"] and lo <= r["issue_date"] <= hi]

    def supplier_history(self, doc: CanonicalDocument,
                         limit: int = 200) -> list[sqlite3.Row]:
        if not doc.supplier_key:
            return []
        return self._q("""SELECT * FROM documents WHERE supplier_key=?
                          ORDER BY issue_date DESC LIMIT ?""",
                       (doc.supplier_key, limit))

    def count(self) -> int:
        return self._q("SELECT COUNT(*) AS n FROM documents")[0]["n"]

    def _q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with closing(self._conn.cursor()) as cur:
            cur.execute(sql, args)
            return cur.fetchall()
