"""Readonly application queries for the recovered historical archive.

This repository deliberately exposes no credential fields or mutating methods.
It provides the first UI's data contract while the original workflows and the
operational schema are being verified. All Oracle numeric values stay strings
or Decimal; SQLite arithmetic is not used for financial calculations.
"""

from dataclasses import dataclass
from decimal import Decimal, localcontext
import json
from pathlib import Path

from bifma.database import session


@dataclass(frozen=True)
class Page:
    columns: list[tuple[str, str]]
    rows: list[dict]
    total: int


@dataclass(frozen=True)
class Listing:
    table: str
    select: str
    columns: tuple[tuple[str, str], ...]
    search: tuple[str, ...]
    order: str
    company: str | None = None
    condition: str | None = None


LISTINGS = {
    "products": Listing(
        "PUR_ITEM p LEFT JOIN UOM u ON u.UOM_ID=p.UOM_ID_SMALL "
        "LEFT JOIN PUR_ITEM_GROUP_DTL g ON g.SUB_CODE=p.SUB_CODE",
        "p.ICODE AS id,p.ICODE AS code,p.INAME AS name,p.INAME_URDU AS urdu,"
        "u.UOM_DESC AS unit,g.SUB_DESC AS category,p.SALE_RATE AS sale_rate,p.LPUR_RATE AS purchase_rate",
        (("code", "Code"), ("name", "Product"), ("urdu", "Urdu name"), ("unit", "Unit"),
         ("category", "Category"), ("sale_rate", "Sale rate"), ("purchase_rate", "Last purchase rate")),
        ("p.ICODE", "p.INAME", "p.INAME_URDU", "g.SUB_DESC"), "p.INAME,p.ICODE"),
    "accounts": Listing(
        "CHART a LEFT JOIN CITY c ON c.PK_CCODE=a.FK_CCODE",
        "a.CHCODE AS id,a.CHCODE AS code,a.CHDESC AS name,a.CHDESC_URDU AS urdu,"
        "a.CHLEVEL AS level,a.ACCTYPE AS type,c.CNAME AS city,a.MOBILE AS mobile",
        (("code", "Code"), ("name", "Account"), ("urdu", "Urdu name"), ("level", "Level"),
         ("type", "Type"), ("city", "City"), ("mobile", "Mobile")),
        ("a.CHCODE", "a.CHDESC", "a.CHDESC_URDU", "a.MOBILE", "c.CNAME"), "a.CHCODE"),
    "stores": Listing("STORE_LOC s", "s.STORE_ID AS id,s.STORE_ID AS code,s.STORE_DESC AS name,s.STORE_DESC_URDU AS urdu",
        (("code", "Code"), ("name", "Store"), ("urdu", "Urdu name")),
        ("s.STORE_ID", "s.STORE_DESC", "s.STORE_DESC_URDU"), "s.STORE_DESC", "s.CMP_ID"),
    "units": Listing("UOM u", "u.UOM_ID AS id,u.UOM_ID AS code,u.UOM_DESC AS name,u.UOM_DESC_URDU AS urdu",
        (("code", "Code"), ("name", "Unit"), ("urdu", "Urdu name")),
        ("u.UOM_ID", "u.UOM_DESC", "u.UOM_DESC_URDU"), "u.UOM_DESC"),
    "journals": Listing("JMST m LEFT JOIN ENTRY_TYPE e ON e.TYPE_CODE=m.VTYPE",
        "m.TRNID AS id,m.TRNNO AS number,m.TRNDATE AS date,e.DESCRIPTION AS type,m.CANCEL AS cancelled",
        (("number", "Voucher"), ("date", "Date"), ("type", "Voucher type"), ("cancelled", "Cancellation marker")),
        ("m.TRNNO", "m.TRNDATE", "e.DESCRIPTION"), "m.TRNDATE DESC,length(m.TRNNO) DESC,m.TRNNO DESC", "m.CMP_ID"),
}

# ENTRY_TYPE: 2 = CPV, 3 = CRV. Keep the unfiltered journal listing so
# opening balances and other original voucher types remain accessible too.
JOURNAL_TYPES = {"journals": None, "cash_receipts": "3", "cash_payments": "2"}
for _section, _type in JOURNAL_TYPES.items():
    if _type is None:
        continue
    _journal = LISTINGS["journals"]
    LISTINGS[_section] = Listing(
        _journal.table, _journal.select,
        _journal.columns + (("amount", "Received" if _type == "3" else "Paid"),),
        _journal.search, _journal.order, _journal.company, f"m.VTYPE='{_type}'",
    )

DOCUMENTS = {
    "sales": ("SMST", "SDTL", "Sales invoice"),
    "purchases": ("PMST", "PDTL", "Purchase invoice"),
    "breakage_receive": ("BRMST", "BRDTL", "Breakage receive voucher"),
    "breakage_dispatch": ("BDMST", "BDDTL", "Breakage dispatch voucher"),
    "purchase_returns": ("SMST", "SDTL", "Purchase return voucher"),
    "sales_returns": ("PMST", "PDTL", "Sales return voucher"),
}
DOCUMENT_TYPES = {"sales": "8", "purchases": "7", "purchase_returns": "9",
                  "sales_returns": "10", "breakage_receive": "11", "breakage_dispatch": "12"}
for _section, (_master, _detail, _title) in DOCUMENTS.items():
    _received_select = ",m.AMOUNT_RCV AS received" if _section == "sales" else ""
    _received_column = (("received", "Received"),) if _section == "sales" else ()
    LISTINGS[_section] = Listing(
        f"{_master} m LEFT JOIN CHART a ON a.CHCODE=m.CHCODE LEFT JOIN ENTRY_TYPE e ON e.TYPE_CODE=m.TRN_TYPE",
        "m.TRNID AS id,m.TRNNO AS number,m.TRNDATE AS date,m.BILLNO AS bill,"
        "COALESCE(a.CHDESC,m.CUSTOMER,'') AS account,e.DESCRIPTION AS type" + _received_select,
        (("number", "Number"), ("bill", "Bill"), ("date", "Date"), ("account", "Account / customer"), ("type", "Type")) + _received_column,
        ("m.TRNNO", "m.BILLNO", "m.TRNDATE", "a.CHDESC", "m.CUSTOMER", "e.DESCRIPTION"),
        "m.TRNDATE DESC,length(m.TRNNO) DESC,m.TRNNO DESC", "m.CMP_ID",
        f"m.TRN_TYPE='{DOCUMENT_TYPES[_section]}'",
    )


def literal_search(text):
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def decimal_text(number):
    if number == 0:
        return "0"
    value = format(number, "f")
    return value.rstrip("0").rstrip(".") if "." in value else value


class LegacyRepository:
    def __init__(self, path):
        self.path = Path(path).resolve()
        with session(self.path) as connection:
            row = connection.execute("SELECT value FROM _recovery_metadata WHERE key='report'").fetchone()
            if row is None:
                raise ValueError("This file has no recovery manifest. Create a staging archive first.")
            self.manifest = json.loads(row[0])
            if self.manifest.get("status") != "recovered_unverified":
                raise ValueError("Unsupported archive status")

    def companies(self):
        with session(self.path) as connection:
            return [tuple(row) for row in connection.execute("SELECT CMP_ID,CMPNAME FROM CONFIG ORDER BY CMPNAME")]

    def summary(self, company_id):
        with session(self.path) as connection:
            return {
                "product_count": connection.execute("SELECT COUNT(*) FROM PUR_ITEM").fetchone()[0],
                "account_count": connection.execute("SELECT COUNT(*) FROM CHART").fetchone()[0],
                "sale_count": connection.execute("SELECT COUNT(*) FROM SMST WHERE CMP_ID=? AND TRN_TYPE='8'", (company_id,)).fetchone()[0],
                "journal_count": connection.execute("SELECT COUNT(*) FROM JMST WHERE CMP_ID=?", (company_id,)).fetchone()[0],
                "latest_sale_date": connection.execute("SELECT MAX(TRNDATE) FROM SMST WHERE CMP_ID=? AND TRN_TYPE='8'", (company_id,)).fetchone()[0],
            }

    def stock_stores(self, company_id):
        """Include historically used stores even when source ownership differs."""
        with session(self.path) as connection:
            return [tuple(row) for row in connection.execute("""
                SELECT s.STORE_ID,s.STORE_DESC FROM STORE_LOC s WHERE s.CMP_ID=? OR s.STORE_ID IN (
                    SELECT STORE_ID FROM OPEN_STOCK WHERE CMP_ID=?
                    UNION SELECT d.STORE_ID FROM SMST m JOIN SDTL d ON d.TRNID=m.TRNID WHERE m.CMP_ID=?
                    UNION SELECT d.STORE_ID FROM PMST m JOIN PDTL d ON d.TRNID=m.TRNID WHERE m.CMP_ID=?
                    UNION SELECT STORE_ID FROM BRMST WHERE CMP_ID=?
                    UNION SELECT d.STORE_ID FROM BDMST m JOIN BDDTL d ON d.TRNID=m.TRNID WHERE m.CMP_ID=?
                ) ORDER BY s.STORE_DESC,s.STORE_ID
                """, [company_id] * 6)]

    def records(self, section, company_id, search="", offset=0, limit=100):
        if section not in LISTINGS:
            raise ValueError("Unknown application section")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("Page size must be between 1 and 500")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("Page offset must be a nonnegative integer")
        definition = LISTINGS[section]
        conditions, parameters = [], []
        if definition.condition:
            conditions.append(definition.condition)
        if definition.company:
            conditions.append(f"{definition.company}=?")
            parameters.append(company_id)
        if search.strip():
            search_conditions = [f"{field} LIKE ? ESCAPE '\\'" for field in definition.search]
            pattern = literal_search(search.strip())
            parameters.extend([pattern] * len(definition.search))
            if section in JOURNAL_TYPES:
                # A voucher may contain many accounts. EXISTS searches its
                # detail without duplicating headers or changing pagination.
                search_conditions.append(
                    "EXISTS (SELECT 1 FROM JDTL d LEFT JOIN CHART a ON a.CHCODE=d.CHCODE "
                    "WHERE d.TRNID=m.TRNID AND (d.CHCODE LIKE ? ESCAPE '\\' "
                    "OR a.CHDESC LIKE ? ESCAPE '\\' OR a.CHDESC_URDU LIKE ? ESCAPE '\\' "
                    "OR d.NARRATION LIKE ? ESCAPE '\\'))")
                parameters.extend([pattern] * 4)
            conditions.append("(" + " OR ".join(search_conditions) + ")")
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with session(self.path) as connection:
            total = connection.execute(f"SELECT COUNT(*) FROM {definition.table}{where}", parameters).fetchone()[0]
            rows = [dict(row) for row in connection.execute(
                f"SELECT {definition.select} FROM {definition.table}{where} ORDER BY {definition.order} LIMIT ? OFFSET ?",
                parameters + [limit, offset],
            )]
            if section in {"cash_receipts", "cash_payments"} and rows:
                amount_column = "CR" if section == "cash_receipts" else "DR"
                amounts = {row["id"]: Decimal(0) for row in rows}
                placeholders = ",".join("?" for _ in amounts)
                with localcontext() as context:
                    context.prec = 1000
                    for detail in connection.execute(
                        f"SELECT TRNID,{amount_column} AS amount FROM JDTL WHERE TRNID IN ({placeholders})",
                        list(amounts),
                    ):
                        amounts[detail["TRNID"]] += Decimal(detail["amount"] or "0")
                for row in rows:
                    row["amount"] = decimal_text(amounts[row["id"]])
        return Page(list(definition.columns), rows, total)

    def document(self, section, record_id, company_id):
        if section in JOURNAL_TYPES:
            return self._journal(record_id, company_id, expected_type=JOURNAL_TYPES[section])
        if section not in DOCUMENTS:
            raise ValueError("This section has no document detail")
        master, detail, title = DOCUMENTS[section]
        with session(self.path) as connection:
            header = connection.execute(
                f"SELECT m.*,a.CHDESC AS ACCOUNT_NAME,e.DESCRIPTION AS TYPE_NAME FROM {master} m "
                "LEFT JOIN CHART a ON a.CHCODE=m.CHCODE LEFT JOIN ENTRY_TYPE e ON e.TYPE_CODE=m.TRN_TYPE "
                "WHERE m.TRNID=? AND m.CMP_ID=? AND m.TRN_TYPE=?", (record_id, company_id, DOCUMENT_TYPES[section]),
            ).fetchone()
            if header is None:
                raise ValueError("Document not found in the selected company and section")
            # BR uses the header's store; all other detail families have STORE_ID.
            store = "?" if section == "breakage_receive" else "d.STORE_ID"
            params = [header["STORE_ID"], record_id] if section == "breakage_receive" else [record_id]
            details = connection.execute(
                f"SELECT d.*,p.INAME AS PRODUCT_NAME,s.STORE_DESC AS STORE_NAME FROM {detail} d "
                f"LEFT JOIN PUR_ITEM p ON p.ICODE=d.ICODE LEFT JOIN STORE_LOC s ON s.STORE_ID={store} "
                "WHERE d.TRNID=? ORDER BY CAST(d.LINE_NUM AS INTEGER),d.VNO", params,
            ).fetchall()
        fields = [("Number", header["TRNNO"]), ("Bill", header["BILLNO"]), ("Date", header["TRNDATE"]),
                  ("Account", header["ACCOUNT_NAME"] or header["CUSTOMER"] or ""),
                  ("Type", header["TYPE_NAME"] or title)]
        for key, label in (("DISCOUNT", "Header discount"), ("BREAKAGE", "Breakage"), ("FREIGHT", "Freight"),
                           ("LABOUR", "Labour"), ("GST_RATE", "GST rate"), ("AMOUNT_RCV", "Received"), ("REMARKS", "Remarks")):
            if key in header.keys():
                fields.append((label, header[key] or "0" if key != "REMARKS" else header[key] or ""))
        rows = []
        with localcontext() as context:
            context.prec = 1000
            subtotal = Decimal(0)
            for row in details:
                quantity, rate, discount = (Decimal(row[key] or "0") for key in ("QTY", "RATE", "DIS_PER"))
                net = quantity * rate * (1 - discount / 100)
                subtotal += net
                rows.append({"code": row["ICODE"], "product": row["PRODUCT_NAME"] or "", "store": row["STORE_NAME"] or "",
                             "quantity": row["QTY"], "rate": row["RATE"], "discount": row["DIS_PER"] or "0", "net": decimal_text(net)})
        fields.append(("Line net subtotal (before rounding)", decimal_text(subtotal)))
        return {"title": header["TYPE_NAME"] or title, "fields": fields, "columns": [
            ("code", "Code"), ("product", "Product"), ("store", "Store"), ("quantity", "Quantity"),
            ("rate", "Rate"), ("discount", "Discount %"), ("net", "Line net")], "rows": rows,
            "notes": ["Historical source values. The line net subtotal excludes header adjustments.",
                      "Final invoice and ledger totals await reconciliation with the original reports."]}

    def _journal(self, record_id, company_id, *, expected_type=None):
        with session(self.path) as connection:
            type_filter = " AND m.VTYPE=?" if expected_type is not None else ""
            parameters = [record_id, company_id]
            if expected_type is not None:
                parameters.append(expected_type)
            header = connection.execute(
                "SELECT m.*,e.DESCRIPTION AS TYPE_NAME FROM JMST m LEFT JOIN ENTRY_TYPE e ON e.TYPE_CODE=m.VTYPE "
                "WHERE m.TRNID=? AND m.CMP_ID=?" + type_filter, parameters,
            ).fetchone()
            if header is None:
                raise ValueError("Voucher not found in the selected company and section")
            rows = [dict(row) for row in connection.execute(
                "SELECT d.CHCODE AS code,a.CHDESC AS account,d.NARRATION AS narration,d.DR AS debit,d.CR AS credit "
                "FROM JDTL d LEFT JOIN CHART a ON a.CHCODE=d.CHCODE WHERE d.TRNID=? ORDER BY CAST(d.SR AS INTEGER),d.DTL_ID",
                (record_id,),
            )]
            cash = None
            if header["VTYPE"] in {"2", "3"}:
                cash = connection.execute(
                    "SELECT c.CHCODE_CASH AS code,a.CHDESC AS name FROM CONFIG c "
                    "LEFT JOIN CHART a ON a.CHCODE=c.CHCODE_CASH WHERE c.CMP_ID=?",
                    (company_id,),
                ).fetchone()
        with localcontext() as context:
            context.prec = 1000
            dr = sum((Decimal(row["debit"] or "0") for row in rows), Decimal(0))
            cr = sum((Decimal(row["credit"] or "0") for row in rows), Decimal(0))
        fields = [("Number", header["TRNNO"]), ("Date", header["TRNDATE"])]
        notes = []
        if header["VTYPE"] in {"2", "3"}:
            received = header["VTYPE"] == "3"
            fields.append(("Cash received" if received else "Cash paid", decimal_text(cr if received else dr)))
            fields.append(("Cash account", " · ".join(str(cash[key]) for key in ("code", "name") if cash[key]) if cash else None))
            side = "debit" if received else "credit"
            notes.append(f"The original ledger generates the cash-account {side} from these detail rows. "
                         "That counterpart is not another stored detail line.")
        fields.extend([("Cancellation marker", header["CANCEL"]),
                       ("Ledger inclusion", "Included" if header["CANCEL"] is None else "Excluded (cancellation marker)"),
                       ("Detail debit", decimal_text(dr)), ("Detail credit", decimal_text(cr))])
        if header["CANCEL"] is not None:
            notes.append("The original ledger excludes this voucher because it has a cancellation marker. "
                         "Its stored details are preserved here.")
        return {"title": header["TYPE_NAME"] or {"2": "Cash Payment Voucher", "3": "Cash Receipt Voucher"}.get(header["VTYPE"], "Journal voucher"),
                "fields": fields,
                "columns": [("code", "Account code"), ("account", "Account"), ("narration", "Narration"),
                            ("debit", "Debit"), ("credit", "Credit")], "rows": rows,
                "notes": notes}
