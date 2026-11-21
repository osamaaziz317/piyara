"""One authorized document register over immutable originals and new entries.

Original headers are read from the verified archive, including records that
cannot serve as return originals. They never become native posted documents.
Two ordered read streams are merged before pagination under a live company/
module authorization transaction. No arithmetic uses SQLite floating point.
"""

from datetime import date
from decimal import Context, Decimal, localcontext
from heapq import merge
from itertools import islice
import json
from pathlib import Path

from bifma.baseline import BaselineError, _source_digest, baseline_info
from bifma.database import session
from bifma.legacy_ledger import LegacyLedger, _ARMS
from bifma.legacy_repository import LegacyRepository, decimal_text, literal_search
from bifma.native_schema import native_transaction
from bifma.security import AuthorizationError, SecurityService
from bifma.transactions import MODULES, TransactionError, TransactionService, _id


_TRADES = {
    "sale": ("SMST", "SDTL", "8", "S_TYPE", "sales", "sale_party"),
    "cash_sale": ("SMST", "SDTL", "8", "S_TYPE", "sales", "sale_party"),
    "purchase": ("PMST", "PDTL", "7", "P_TYPE", "purchases", "purchase_party"),
    "cash_purchase": ("PMST", "PDTL", "7", "P_TYPE", "purchases", "purchase_party"),
    "sale_return": ("PMST", "PDTL", "10", "P_TYPE", "sales_returns", "purchase_party"),
    "cash_sale_return": ("PMST", "PDTL", "10", "P_TYPE", "sales_returns", "purchase_party"),
    "purchase_return": ("SMST", "SDTL", "9", "S_TYPE", "purchase_returns", "sale_party"),
    "cash_purchase_return": ("SMST", "SDTL", "9", "S_TYPE", "purchase_returns", "sale_party"),
    "breakage_receive": ("BRMST", "BRDTL", "11", None, "breakage_receive", "breakage_return_party"),
    "breakage_dispatch": ("BDMST", "BDDTL", "12", None, "breakage_dispatch", "purchase_return_party"),
}
_JOURNALS = {"journal": "1", "cash_payment": "2", "cash_receipt": "3",
             "bank_payment": "4", "bank_receipt": "5", "opening_balance": "6"}
_ARMS_BY_NAME = {arm.name: arm for arm in _ARMS}
_DEFAULT_ARCHIVE = Path(__file__).resolve().parents[1] / "data" / "legacy_recovered.sqlite3"


def _signature(path):
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _dates(date_from, date_to):
    for value in (date_from, date_to):
        if value is not None:
            try:
                valid = isinstance(value, str) and len(value) == 10 and date.fromisoformat(value).isoformat() == value
            except ValueError:
                valid = False
            if not valid:
                raise TransactionError("Use dates in YYYY-MM-DD format.")
    if date_from is not None and date_to is not None and date_from > date_to:
        raise TransactionError("The start date must not follow the end date.")


def _key(row):
    return row["business_date"] or "", row["document_number"] or "", row["source"], row["id"]


class DocumentRegister:
    def __init__(self, path, archive_path=None, *, security=None):
        # Native startup and archive hashing must precede any managed session.
        self.path = Path(path).resolve()
        self.security = security or SecurityService(self.path)
        self.baseline = baseline_info(self.path)
        self.archive_path = None
        self.repository = None
        self._archive_signature = None
        if self.baseline is not None:
            self.archive_path = Path(archive_path or _DEFAULT_ARCHIVE).resolve()
            self.verify_archive()
            self.repository = LegacyRepository(self.archive_path)
            self.assert_archive_unchanged()
        elif archive_path is not None:
            raise BaselineError("This working database has no verified association with the original archive.")

    def verify_archive(self):
        """Verify original-file lineage outside database sessions when changed."""
        if self.archive_path is None:
            return
        try:
            signature = _signature(self.archive_path)
            if signature != self._archive_signature:
                if _source_digest(self.archive_path) != self.baseline.source_sha256:
                    raise BaselineError("The original archive does not match this working database's baseline.")
                if _signature(self.archive_path) != signature:
                    raise BaselineError("The original archive changed during verification; reopen the application.")
                self._archive_signature = signature
        except FileNotFoundError:
            raise BaselineError("The original archive is unavailable. Restore its matching file to browse the complete records.") from None

    def assert_archive_unchanged(self):
        if self.archive_path is not None:
            try:
                unchanged = _signature(self.archive_path) == self._archive_signature
            except FileNotFoundError:
                unchanged = False
            if not unchanged:
                raise BaselineError("The original archive changed while records were being read; reopen the application.")

    def source_company(self, connection, company_id):
        """Resolve only the sealed CONFIG identity, never a guessed code/name."""
        source = connection.execute("SELECT source_sha256 FROM baseline_imports WHERE singleton=1").fetchone()
        if source is None:
            if self.baseline is not None:
                raise BaselineError("The working database's baseline association changed.")
            return None
        if self.baseline is None or source[0] != self.baseline.source_sha256:
            raise BaselineError("The working database no longer matches the verified original archive.")
        row = connection.execute(
            "SELECT source_key,source_sha256 FROM baseline_mappings "
            "WHERE source_table='CONFIG' AND native_table='companies' AND native_id=?", (company_id,)).fetchone()
        if row is not None and row["source_sha256"] != self.baseline.source_sha256:
            raise BaselineError("The selected company's original-data mapping is inconsistent.")
        return row["source_key"] if row else None

    def _authorize(self, connection, actor, company_id, kind):
        if kind != "reversal":
            self.security.require(actor, company_id, MODULES[kind], "read", connection=connection)
            return [kind]
        allowed = []
        for candidate, module in MODULES.items():
            try:
                self.security.require(actor, company_id, module, "read", connection=connection)
                allowed.append(candidate)
            except AuthorizationError:
                pass
        if not allowed:
            raise AuthorizationError("No document read permission is available.")
        return allowed

    @staticmethod
    def _kind(kind):
        if not isinstance(kind, str) or kind not in MODULES and kind != "reversal":
            raise TransactionError("Select a supported document section.")

    def list_documents(self, actor, company_id, kind, *, search="", offset=0, limit=100,
                       date_from=None, date_to=None):
        self._kind(kind)
        company_id = _id(company_id, "company_id")
        _dates(date_from, date_to)
        if not isinstance(search, str) or len(search) > 200 or "\x00" in search:
            raise TransactionError("Search must contain at most 200 characters without NUL.")
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 500:
            raise TransactionError("Use a nonnegative offset and a page size from 1 to 500.")
        self.verify_archive()
        with native_transaction(self.path) as connection:
            allowed = self._authorize(connection, actor, company_id, kind)
            source_company = self.source_company(connection, company_id)
            native = self._native_rows(connection, company_id, kind, allowed, search, date_from, date_to)
            if source_company is None or kind == "reversal":
                page = list(islice(native, offset, offset + limit))
            else:
                with session(self.archive_path) as original:
                    original.execute("BEGIN")
                    rows = self._original_rows(original, source_company, kind, search, date_from, date_to)
                    page = list(islice(merge(native, rows, key=_key, reverse=True), offset, offset + limit))
                    self._original_amounts(original, source_company, kind, [row for row in page if row["source"] == "original"])
            self._native_account_labels(connection, [row for row in page if row["source"] == "current"])
            self.assert_archive_unchanged()
            return page

    def date_bounds(self, actor, company_id, kind):
        """Initial From/To values covering valid dates in the combined register.

        Only header dates are read; no original amounts or details are loaded.
        The same live section grants and archive verification apply as browsing.
        """
        self._kind(kind)
        company_id = _id(company_id, "company_id")
        self.verify_archive()
        with native_transaction(self.path) as connection:
            allowed = self._authorize(connection, actor, company_id, kind)
            source_company = self.source_company(connection, company_id)
            dates = []
            def collect(rows):
                for row in rows:
                    value = row["business_date"]
                    try:
                        _dates(value, value)
                    except TransactionError:
                        continue
                    if value:
                        dates.append(value)
            collect(self._native_rows(connection, company_id, kind, allowed, "", None, None))
            if source_company is not None and kind != "reversal":
                with session(self.archive_path) as original:
                    original.execute("BEGIN")
                    collect(self._original_rows(original, source_company, kind, "", None, None))
            self.assert_archive_unchanged()
            return {"date_from": min(dates) if dates else None,
                    "date_to": max(dates) if dates else None}

    @staticmethod
    def _native_rows(connection, company, kind, allowed, search, date_from, date_to):
        placeholders = ",".join("?" for _ in allowed)
        where = ["d.company_id=?"]
        parameters = [company]
        if kind == "reversal":
            where.append(f"d.kind='reversal' AND o.kind IN ({placeholders})")
            parameters.extend(allowed)
        else:
            where.append("(d.kind=? OR (d.kind='reversal' AND o.kind=?))")
            parameters.extend((kind, kind))
        if date_from is not None:
            where.append("d.business_date>=?")
            parameters.append(date_from)
        if date_to is not None:
            where.append("d.business_date<=?")
            parameters.append(date_to)
        if search:
            pattern = literal_search(search)
            where.append("(d.document_number LIKE ? ESCAPE '\\' OR d.notes LIKE ? ESCAPE '\\' "
                         "OR a.code LIKE ? ESCAPE '\\' OR a.name LIKE ? ESCAPE '\\' OR a.name_urdu LIKE ? ESCAPE '\\' "
                         "OR EXISTS(SELECT 1 FROM journal_entries e JOIN journal_lines l ON l.entry_id=e.id "
                         "JOIN accounts x ON x.id=l.account_id WHERE e.document_id=d.id AND "
                         "(x.code LIKE ? ESCAPE '\\' OR x.name LIKE ? ESCAPE '\\' OR x.name_urdu LIKE ? ESCAPE '\\' OR l.narration LIKE ? ESCAPE '\\')) "
                         "OR EXISTS(SELECT 1 FROM document_metadata m,json_each(m.input_json,'$.lines') j "
                         "LEFT JOIN accounts x ON x.id=json_extract(j.value,'$.account_id') WHERE m.document_id=d.id AND "
                         "(x.code LIKE ? ESCAPE '\\' OR x.name LIKE ? ESCAPE '\\' OR x.name_urdu LIKE ? ESCAPE '\\' "
                         "OR json_extract(j.value,'$.narration') LIKE ? ESCAPE '\\')) "
                         "OR EXISTS(SELECT 1 FROM document_metadata m,json_each(m.snapshot_json,'$.accounts') j "
                         "WHERE m.document_id=d.id AND (json_extract(j.value,'$.code') LIKE ? ESCAPE '\\' "
                         "OR json_extract(j.value,'$.name') LIKE ? ESCAPE '\\' "
                         "OR json_extract(j.value,'$.name_urdu') LIKE ? ESCAPE '\\')))")
            parameters.extend([pattern] * 16)
        for source in connection.execute(
            "SELECT d.id,d.kind,d.document_number,d.business_date,d.status,d.total_amount,d.account_id,"
            "COALESCE(a.name,'') AS account,a.code AS account_code,o.kind AS original_kind,"
            "r.id AS reversal_id,r.document_number AS reversal_number "
            "FROM documents d LEFT JOIN documents o ON o.id=d.reverses_document_id AND o.company_id=d.company_id "
            "LEFT JOIN documents r ON r.reverses_document_id=d.id AND r.company_id=d.company_id AND r.status='posted' "
            "LEFT JOIN accounts a ON a.id=d.account_id WHERE " + " AND ".join(where)
            + " ORDER BY d.business_date DESC,d.document_number DESC,d.id DESC", parameters):
            row = dict(source)
            row.update(source="current", status_display="Reversal" if row["kind"] == "reversal" else "Reversed" if row["reversal_id"] else row["status"].title())
            yield row

    @staticmethod
    def _native_account_labels(connection, rows):
        selected = {row["id"]: row for row in rows
                    if (row["original_kind"] if row["kind"] == "reversal" else row["kind"]) in _JOURNALS}
        if not selected:
            return
        placeholders = ",".join("?" for _ in selected)
        metadata = {row["document_id"]: (json.loads(row["input_json"]), json.loads(row["snapshot_json"]))
                    for row in connection.execute("SELECT document_id,input_json,snapshot_json FROM document_metadata "
                                                  f"WHERE document_id IN ({placeholders})", list(selected))}
        accounts = {identifier: {} for identifier in selected}
        for line in connection.execute(
            "SELECT e.document_id,l.account_id,a.code,a.name FROM journal_entries e JOIN journal_lines l ON l.entry_id=e.id "
            f"JOIN accounts a ON a.id=l.account_id WHERE e.document_id IN ({placeholders}) ORDER BY e.document_id,l.line_number", list(selected)):
            row = selected[line["document_id"]]
            if row["account_id"] == line["account_id"]:
                continue
            saved = metadata.get(line["document_id"], ({}, {}))[1].get("accounts", {}).get(line["account_id"], {})
            accounts[row["id"]][line["account_id"]] = saved.get("name") or line["name"] or line["code"]
        for identifier, row in selected.items():
            if not accounts[identifier] and identifier in metadata:
                values, snapshot = metadata[identifier]
                for line in values.get("lines", []):
                    account_id = line.get("account_id")
                    if account_id and account_id != row["account_id"]:
                        saved = snapshot.get("accounts", {}).get(account_id, {})
                        if saved.get("name") or saved.get("code"):
                            accounts[identifier][account_id] = saved.get("name") or saved["code"]
            row["account"] = " · ".join(accounts[identifier].values())
            row["counterpart_account_ids"] = list(accounts[identifier])

    @staticmethod
    def _original_rows(connection, company, kind, search, date_from, date_to, identifier=None):
        where, parameters = ["m.CMP_ID=?"], [company]
        if kind in _TRADES:
            table, detail, code, payment, section, _ = _TRADES[kind]
            where.append("m.TRN_TYPE=?")
            parameters.append(code)
            if payment:
                where.append(f"m.{payment}='C'" if kind.startswith("cash_") else f"(m.{payment} IS NULL OR m.{payment}<>'C')")
            selected = ("m.TRNID AS id,m.TRNNO AS document_number,substr(m.TRNDATE,1,10) AS business_date,"
                        "m.TRNDATE AS original_date,m.CHCODE AS account_code,COALESCE(a.CHDESC,m.CUSTOMER,'') AS account,"
                        + (f"m.{payment}" if payment else "NULL") + " AS original_payment_type,m.TRN_TYPE AS original_type,NULL AS cancellation_marker")
            source = f"{table} m LEFT JOIN CHART a ON a.CHCODE=m.CHCODE"
            search_fields = ("m.TRNNO", "m.TRNDATE", "m.BILLNO", "m.REMARKS", "m.CUSTOMER", "m.CHCODE", "a.CHDESC", "a.CHDESC_URDU")
            date_column = "m.TRNDATE"
        elif kind in _JOURNALS:
            table, section = "JMST", "journals"
            if kind == "journal":
                where.append("(m.VTYPE='1' OR m.VTYPE IS NULL OR m.VTYPE NOT IN ('2','3','4','5','6'))")
            else:
                where.append("m.VTYPE=?")
                parameters.append(_JOURNALS[kind])
            selected = ("m.TRNID AS id,m.TRNNO AS document_number,substr(m.TRNDATE,1,10) AS business_date,"
                        "m.TRNDATE AS original_date,NULL AS account_code,'' AS account,NULL AS original_payment_type,"
                        "m.VTYPE AS original_type,m.CANCEL AS cancellation_marker")
            source = "JMST m LEFT JOIN ENTRY_TYPE e ON e.TYPE_CODE=m.VTYPE"
            search_fields = ("m.TRNNO", "m.TRNDATE", "e.DESCRIPTION")
            date_column = "m.TRNDATE"
        elif kind == "stock_opening":
            table, section = "OPEN_STOCK", "stock_opening"
            selected = ("m.TRNID AS id,m.TRNID AS document_number,substr(m.OPEN_DATE,1,10) AS business_date,"
                        "m.OPEN_DATE AS original_date,NULL AS account_code,'' AS account,NULL AS original_payment_type,"
                        "NULL AS original_type,NULL AS cancellation_marker,m.OPEN_QTY AS quantity,m.OPEN_RATE AS rate,"
                        "m.ICODE AS product_code,p.INAME AS product_name,s.STORE_DESC AS store_name")
            source = "OPEN_STOCK m LEFT JOIN PUR_ITEM p ON p.ICODE=m.ICODE LEFT JOIN STORE_LOC s ON s.STORE_ID=m.STORE_ID"
            search_fields = ("m.TRNID", "m.OPEN_DATE", "m.ICODE", "p.INAME", "p.INAME_URDU", "s.STORE_DESC", "s.STORE_DESC_URDU")
            date_column = "m.OPEN_DATE"
        else:
            return
        if identifier is not None:
            where.append("m.TRNID=?")
            parameters.append(identifier)
        if date_from is not None:
            where.append(f"substr({date_column},1,10)>=?")
            parameters.append(date_from)
        if date_to is not None:
            where.append(f"substr({date_column},1,10)<=?")
            parameters.append(date_to)
        if search:
            pattern = literal_search(search)
            predicates = [field + " LIKE ? ESCAPE '\\'" for field in search_fields]
            parameters.extend([pattern] * len(predicates))
            if kind in _JOURNALS:
                predicates.append("EXISTS(SELECT 1 FROM JDTL d LEFT JOIN CHART a ON a.CHCODE=d.CHCODE WHERE d.TRNID=m.TRNID "
                                  "AND (d.CHCODE LIKE ? ESCAPE '\\' OR a.CHDESC LIKE ? ESCAPE '\\' "
                                  "OR a.CHDESC_URDU LIKE ? ESCAPE '\\' OR d.NARRATION LIKE ? ESCAPE '\\'))")
                parameters.extend([pattern] * 4)
            where.append("(" + " OR ".join(predicates) + ")")
        for record in connection.execute("SELECT " + selected + " FROM " + source + " WHERE " + " AND ".join(where)
                                         + " ORDER BY business_date DESC,document_number DESC,id DESC", parameters):
            row = dict(record)
            row.update(source="original", kind=kind, status="historical" if row["cancellation_marker"] is None else "cancelled",
                       total_amount=None, account_id=None, source_table=table, section=section, ref=row["id"])
            yield row

    @staticmethod
    def _original_amounts(connection, company, kind, rows):
        if not rows:
            return
        by_id = {row["id"]: row for row in rows}
        placeholders = ",".join("?" for _ in by_id)
        with localcontext(Context(prec=1000)):
            if kind in _TRADES:
                arm = _ARMS_BY_NAME[_TRADES[kind][-1]]
                entries = LegacyLedger._invoice_arm(connection, f"M.CMP_ID=? AND M.TRNID IN ({placeholders})", [company, *by_id], arm)
                amounts = {}
                for entry in entries:
                    amounts.setdefault(entry.transaction_id, []).append(entry.debit if arm.side == "debit" else entry.credit)
                for identifier, values in amounts.items():
                    if identifier in by_id and len(values) == 1:
                        by_id[identifier]["total_amount"] = decimal_text(values[0])
            elif kind in _JOURNALS:
                values = {identifier: {"debit": Decimal(0), "credit": Decimal(0), "accounts": {}, "count": 0} for identifier in by_id}
                for detail in connection.execute(
                    "SELECT d.TRNID,d.DR,d.CR,d.CHCODE,a.CHDESC FROM JDTL d LEFT JOIN CHART a ON a.CHCODE=d.CHCODE "
                    f"WHERE d.TRNID IN ({placeholders}) ORDER BY d.CHCODE,d.DTL_ID", list(by_id)):
                    total = values[detail["TRNID"]]
                    total["debit"] += Decimal(detail["DR"] or "0")
                    total["credit"] += Decimal(detail["CR"] or "0")
                    total["count"] += 1
                    total["accounts"][detail["CHCODE"]] = detail["CHDESC"] or detail["CHCODE"] or ""
                for identifier, total in values.items():
                    row = by_id[identifier]
                    row["account"] = " · ".join(total["accounts"].values())
                    row.update(debit_total=decimal_text(total["debit"]), credit_total=decimal_text(total["credit"]))
                    amount = (total["credit"] if kind in {"cash_receipt", "bank_receipt"} else
                              total["debit"] if kind in {"cash_payment", "bank_payment"} or total["debit"] == total["credit"] else None)
                    row["total_amount"] = decimal_text(amount) if total["count"] and amount is not None else None
            elif kind == "stock_opening":
                for row in rows:
                    if row["quantity"] is not None and row["rate"] is not None:
                        row["total_amount"] = decimal_text(Decimal(row["quantity"]) * Decimal(row["rate"]))

    def get(self, actor, company_id, kind, identifier, *, source):
        self._kind(kind)
        company_id = _id(company_id, "company_id")
        if source not in {"original", "current"}:
            raise TransactionError("Choose an original or current document.")
        if not isinstance(identifier, str) or not identifier or len(identifier) > 300 or "\x00" in identifier:
            raise TransactionError("A saved document identifier is required.")
        self.verify_archive()
        with native_transaction(self.path) as connection:
            allowed = self._authorize(connection, actor, company_id, kind)
            if source == "current":
                document = TransactionService._document(connection, company_id, identifier)
                if document["kind"] == "reversal":
                    original_kind = connection.execute("SELECT kind FROM documents WHERE id=? AND company_id=?",
                                                       (document["reverses_document_id"], company_id)).fetchone()
                    valid = original_kind is not None and original_kind[0] in allowed
                else:
                    valid = document["kind"] == kind
                if not valid:
                    raise TransactionError("Document not found in the selected company and section.")
                return {**TransactionService._get(connection, company_id, identifier), "source": "current"}
            source_company = self.source_company(connection, company_id)
            if source_company is None or kind == "reversal":
                raise TransactionError("Original document not found in this company and section.")
            with session(self.archive_path) as original:
                original.execute("BEGIN")
                candidates = list(self._original_rows(original, source_company, kind, "", None, None, identifier))
                if len(candidates) != 1:
                    raise TransactionError("Original document not found in this company and section.")
                row = candidates[0]
                self._original_amounts(original, source_company, kind, [row])
                detail = self._opening_detail(original, source_company, row) if kind == "stock_opening" else self.repository.document(row["section"], identifier, source_company)
                if kind in _TRADES:
                    detail["fields"].append(("Recorded party amount", row["total_amount"]))
                    detail["notes"] = [note for note in detail["notes"] if not note.startswith("Final invoice and ledger totals await")]
                    detail["notes"].append("The line subtotal excludes header adjustments; the recorded party amount follows the original posting rules."
                                           if row["total_amount"] is not None else
                                           "The original posting rules do not establish one unambiguous party amount for this record; no total has been invented.")
            self.assert_archive_unchanged()
            return {**detail, **row, "company_id": company_id}

    @staticmethod
    def _opening_detail(connection, company, row):
        original = connection.execute(
            "SELECT o.*,u.UOM_DESC AS unit,s.STORE_DESC AS store,p.INAME AS product FROM OPEN_STOCK o "
            "LEFT JOIN UOM u ON u.UOM_ID=o.UOM_ID_SMALL LEFT JOIN STORE_LOC s ON s.STORE_ID=o.STORE_ID "
            "LEFT JOIN PUR_ITEM p ON p.ICODE=o.ICODE WHERE o.CMP_ID=? AND o.TRNID=?", (company, row["id"])).fetchone()
        return {"title": "Opening stock", "fields": [("Source record", row["document_number"]), ("Date", original["OPEN_DATE"]),
                  ("Item", original["product"]), ("Store", original["store"]), ("Base unit", original["unit"]),
                  ("Original quantity", original["OPEN_QTY"]), ("Original rate", original["OPEN_RATE"]),
                  ("Quantity × original rate", row["total_amount"])],
                "columns": [("product", "Item"), ("store", "Store"), ("quantity", "Quantity"), ("rate", "Rate")],
                "rows": [{"product": original["product"], "store": original["store"], "quantity": original["OPEN_QTY"], "rate": original["OPEN_RATE"]}],
                "notes": ["Original opening-stock row. Its source record identifier is shown because the archive stores no separate document number.",
                          "These original values are preserved; the row is not a new posting."]}

    def document_counts(self, actor, company_id):
        """Count originals and all current headers; current includes drafts."""
        company_id = _id(company_id, "company_id")
        self.verify_archive()
        with native_transaction(self.path) as connection:
            self.security.require(actor, company_id, "dashboard", "read", connection=connection)
            counts = {"original": 0, "current": connection.execute("SELECT COUNT(*) FROM documents WHERE company_id=?", (company_id,)).fetchone()[0]}
            source_company = self.source_company(connection, company_id)
            if source_company is not None:
                with session(self.archive_path) as original:
                    original.execute("BEGIN")
                    for table, condition in (("SMST", "TRN_TYPE IN ('8','9')"), ("PMST", "TRN_TYPE IN ('7','10')"),
                                             ("BRMST", "TRN_TYPE='11'"), ("BDMST", "TRN_TYPE='12'"),
                                             ("JMST", "1"), ("OPEN_STOCK", "1")):
                        counts["original"] += original.execute(f"SELECT COUNT(*) FROM {table} WHERE CMP_ID=? AND {condition}", (source_company,)).fetchone()[0]
            self.assert_archive_unchanged()
            return counts
