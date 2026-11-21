"""Import an immutable historical starting point into a separate live database.

Original rows remain in the read-only archive. Closing quantities and account
balances are carried independently, without synthesizing invoices, stock value
journals, classifications, credentials, or corrected historical transactions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unicodedata
from uuid import NAMESPACE_URL, uuid4, uuid5

from bifma.database import exclusive_file_access, new_destination, publish_new_file, session
from bifma.legacy_ledger import LegacyLedger, _line_net
from bifma.legacy_repository import LegacyRepository
from bifma.legacy_stock import LegacyStock, _date_bound, _exact_add
from bifma.native_schema import (
    _UNVERIFIED, _remember_startup_check, _startup_check, initialize_database,
    native_session, native_transaction, open_native_database,
)


class BaselineError(ValueError):
    """The original starting point cannot safely be prepared or reused."""


@dataclass(frozen=True)
class BaselineResult:
    path: Path
    created: bool
    source_sha256: str
    cutoff_date: str
    manifest: dict


_MASTERS = {
    "CITY": ("PK_CCODE", "cities"), "CHART": ("CHCODE", "accounts"),
    "CONFIG": ("CMP_ID", "companies"), "UOM": ("UOM_ID", "units"),
    "PUR_ITEM_GROUP_MST": ("MAIN_CODE", "product_groups"),
    "PUR_ITEM_GROUP_DTL": ("SUB_CODE", "product_groups"),
    "PUR_ITEM": ("ICODE", "products"), "STORE_LOC": ("STORE_ID", "stores"),
}
_DATES = {
    "OPEN_STOCK": "OPEN_DATE", "PMST": "TRNDATE", "SMST": "TRNDATE",
    "BRMST": "TRNDATE", "BDMST": "TRNDATE", "JMST": "TRNDATE", "DCMST": "DCDATE",
}
_KINDS = {"1": "journal", "2": "cash_payment", "3": "cash_receipt", "4": "bank_payment",
          "5": "bank_receipt", "6": "opening_balance", "7": "purchase", "8": "sale",
          "9": "purchase_return", "10": "sale_return", "11": "breakage_receive", "12": "breakage_dispatch"}
_IMMUTABLE = {
    "baseline_mappings": "source_table,source_key",
    "baseline_account_balances": "company_id,account_id",
    "baseline_stock_balances": "company_id,store_id,product_id,pool",
    "baseline_document_numbers": "source_table,source_key",
    "baseline_purchase_rates": "company_id,product_id",
    "baseline_trade_documents": "company_id,id",
    "baseline_trade_lines": "company_id,id",
}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _number(value):
    number = value if isinstance(value, Decimal) else Decimal(value)
    if not number.is_finite():
        raise BaselineError("The original data contains a non-finite numeric value.")
    if not number:
        return "0"
    text = format(number, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _day(value):
    if not isinstance(value, str):
        raise BaselineError("A historical business date is missing.")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise BaselineError("A historical business date is invalid.") from None
    if parsed.tzinfo is not None or parsed.microsecond:
        raise BaselineError("A historical date has unsupported timezone or subsecond precision.")
    return parsed.date().isoformat()


def _source_digest(path):
    # Opening/closing a raw descriptor must not release another managed session's
    # process-wide SQLite advisory locks. All hashing runs outside DB sessions.
    with exclusive_file_access(path):
        digest = hashlib.sha256()
        with Path(path).open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()


def _immutable_digest(connection):
    digest = hashlib.sha256()
    for table, order in _IMMUTABLE.items():
        digest.update((table + "\n").encode())
        for row in connection.execute(f"SELECT * FROM {table} ORDER BY {order}"):
            digest.update((_json(dict(row)) + "\n").encode("utf-8"))
    return digest.hexdigest()


def baseline_info(path):
    """Return verified sealed metadata, or None for an ordinary native database."""
    path = Path(path)
    path = path.parent.resolve() / path.name
    open_native_database(path)
    with native_session(path) as connection:
        # Pin manifest and immutable rows to one committed read snapshot.
        connection.execute("BEGIN")
        return _read_baseline_info(connection, path)


def _read_baseline_info(connection, path, *, allow_cache=True):
    """Read one already-pinned snapshot, including during a guarded upgrade."""
    row = connection.execute("SELECT * FROM baseline_imports WHERE singleton=1").fetchone()
    if row is None:
        return None
    manifest = json.loads(row["manifest_json"])
    kind = ("baseline-immutable", tuple(_IMMUTABLE.items()))
    before, immutable_digest = _startup_check(path, kind) if allow_cache else (None, _UNVERIFIED)
    if immutable_digest is _UNVERIFIED:
        immutable_digest = _immutable_digest(connection)
    if (manifest.get("format") != 1 or manifest.get("source_sha256") != row["source_sha256"]
            or manifest.get("cutoff_date") != row["cutoff_date"]
            or manifest.get("immutable_sha256") != immutable_digest):
        raise BaselineError("The saved historical baseline no longer matches its import manifest.")
    _remember_startup_check(path, kind, before, immutable_digest)
    return BaselineResult(path, False, row["source_sha256"], row["cutoff_date"], manifest)


def prepare_native_login(path, *, source_sha256=None):
    """Verify a working file and upgrade the supported preceding login schema.

    Ordinary service constructors still require the current schema. Only this
    explicit startup step permits the version-five username upgrade; older
    files keep their established manual-upgrade path. The baseline is verified
    under the upgrade write lock before backup or mutation, and a default
    application's source association must match there as well.
    """
    from bifma.native_schema import MIGRATIONS, NativeSchemaError, migrate_database

    path = Path(path)
    path = path.parent.resolve() / path.name
    try:
        info = baseline_info(path)
    except NativeSchemaError:
        version = open_native_database(path, require_current=False)
        if version not in {5, MIGRATIONS[-1].version}:
            raise

        def checked_baseline(connection):
            info = _read_baseline_info(connection, path, allow_cache=False)
            if source_sha256 is not None and (info is None or info.source_sha256 != source_sha256):
                raise BaselineError("This live database does not belong to the selected original baseline.")

        if version == 5:
            migrate_database(path, pre_upgrade_check=checked_baseline)
        # A competing starter may have completed the upgrade after the first
        # current-schema check. Verify that new snapshot instead of rethrowing
        # the now-stale upgrade-required error.
        info = baseline_info(path)
    if source_sha256 is not None and (info is None or info.source_sha256 != source_sha256):
        raise BaselineError("This live database does not belong to the selected original baseline.")
    return info


def require_after_cutoff(connection, business_date):
    """Reject backdating new effects into the preserved historical interval."""
    row = connection.execute("SELECT cutoff_date FROM baseline_imports WHERE singleton=1").fetchone()
    if row is not None and business_date <= row[0]:
        raise BaselineError(f"New transactions must be dated after the historical closing date {row[0]}.")


def baseline_stock(connection, company_id, store_id, product_id, pool):
    row = connection.execute(
        "SELECT quantity FROM baseline_stock_balances WHERE company_id=? AND store_id=? AND product_id=? AND pool=?",
        (company_id, store_id, product_id, pool),
    ).fetchone()
    return Decimal(row[0]) if row is not None else Decimal(0)


def baseline_purchase_rate(connection, company_id, product_id):
    """Return the original cost candidate and provenance, or None without import."""
    row = connection.execute("SELECT * FROM baseline_purchase_rates WHERE company_id=? AND product_id=?",
                             (company_id, product_id)).fetchone()
    return dict(row) if row is not None else None


def _read_source(source):
    LegacyRepository(source)  # Validate the recovery archive manifest first.
    with session(source) as connection:
        connection.execute("BEGIN")
        masters = {table: [dict(row) for row in connection.execute(f"SELECT * FROM {table}")] for table in _MASTERS}
        headers = {table: [dict(row) for row in connection.execute(f"SELECT * FROM {table}")] for table in _DATES}
        details = {table: [dict(row) for row in connection.execute(f"SELECT * FROM {table}")]
                   for table in ("PDTL", "SDTL", "BRDTL", "BDDTL", "DCDTL")}
    dates = [_day(row[column]) for table, column in _DATES.items() for row in headers[table]]
    if not dates:
        raise BaselineError("The recovered archive contains no dated starting records.")
    if not masters["CONFIG"]:
        raise BaselineError("The recovered archive contains no companies.")
    return masters, headers, details, max(dates)


def _prepare_rows(masters, headers, details, source_hash):
    mappings, records = [], {target: {} for _, target in _MASTERS.values()}
    ids = {}
    normalized = {}

    def text(value, *, table, optional=False):
        if value is None:
            if optional:
                return None
            raise BaselineError(f"A required original master label is missing in {table}.")
        result = unicodedata.normalize("NFC", value.strip())
        if result != value:
            normalized[table] = normalized.get(table, 0) + 1
        if not result and optional:
            return None
        if not result or any(unicodedata.category(char) in {"Cc", "Cs"} for char in result):
            raise BaselineError(f"An original master label in {table} cannot be used without review.")
        return result

    for table, (key, target) in _MASTERS.items():
        for row in masters[table]:
            source_key = row[key]
            if source_key is None or (table, source_key) in ids:
                raise BaselineError("Original master keys are missing or duplicated.")
            native_id = str(uuid5(NAMESPACE_URL, f"bifma:baseline:{source_hash}:{table}:{source_key}"))
            ids[table, source_key] = native_id
            mappings.append(dict(source_table=table, source_key=source_key, native_table=target,
                                 native_id=native_id, source_sha256=source_hash))

    def ref(table, key, optional=False):
        if key is None and optional:
            return None
        try:
            return ids[table, key]
        except KeyError:
            raise BaselineError(f"An original reference to {table} does not resolve.") from None

    def put(table, source, code, name, urdu):
        key, target = _MASTERS[table]
        row = dict(id=ref(table, source[key]), code=text(code, table=table),
                   name=text(source[name], table=table), name_urdu=text(source.get(urdu), table=table, optional=True))
        records[target][row["id"]] = row
        return row

    cash_codes = {row["CHCODE_CASH"] for row in masters["CONFIG"]}
    for source in masters["CITY"]:
        put("CITY", source, source["PK_CCODE"], "CNAME", "CNAME_URDU")
    for source in masters["CHART"]:
        row = put("CHART", source, source["CHCODE"], "CHDESC", "CHDESC_URDU")
        if source.get("ALLOWED") not in {None, "0", "1"}:
            raise BaselineError("An original account has an unsupported posting permission marker.")
        row.update(city_id=ref("CITY", source.get("FK_CCODE"), True),
                   kind="cash" if source["CHCODE"] in cash_codes else "general",
                   is_postable=int(source.get("ALLOWED") == "1"), is_active=1)
        for original, column in (("ADDRESS", "address"), ("PHNO", "phone"), ("MOBILE", "mobile"),
                                 ("EMAIL", "email"), ("WEBSITE", "website"), ("STAX_NUM", "sales_tax_number"), ("NTN_NUM", "national_tax_number")):
            row[column] = text(source.get(original), table="CHART", optional=True)
    for source in masters["CONFIG"]:
        row = put("CONFIG", source, source["CMP_ID"], "CMPNAME", "CMPNAME_URDU")
        row.update(cash_account_id=ref("CHART", source["CHCODE_CASH"]),
                   fiscal_start=_day(source["SDATE"]), fiscal_end=_day(source["EDATE"]), is_active=1)
        for original, column in (("CMPADDRESS", "address"), ("CMPADDRESS_URDU", "address_urdu"),
                                 ("PH_NUM", "phone"), ("FAX_NUM", "fax"), ("WEB", "website"), ("EMAIL", "email"),
                                 ("GST_NUM", "sales_tax_number"), ("NTN_NUM", "national_tax_number")):
            row[column] = text(source.get(original), table="CONFIG", optional=True)
    for source in masters["UOM"]:
        put("UOM", source, source["UOM_ID"], "UOM_DESC", "UOM_DESC_URDU")
    for source in masters["PUR_ITEM_GROUP_MST"]:
        put("PUR_ITEM_GROUP_MST", source, "M:" + source["MAIN_CODE"], "MAIN_DESC", "MAIN_DESC_URDU")
    for source in masters["PUR_ITEM_GROUP_DTL"]:
        row = put("PUR_ITEM_GROUP_DTL", source, "S:" + source["SUB_CODE"], "SUB_DESC", "SUB_DESC_URDU")
        row["parent_id"] = ref("PUR_ITEM_GROUP_MST", source["MAIN_CODE"])
    for source in masters["PUR_ITEM"]:
        row = put("PUR_ITEM", source, source["ICODE"], "INAME", "INAME_URDU")
        row.update(group_id=ref("PUR_ITEM_GROUP_DTL", source["SUB_CODE"]),
                   small_unit_id=ref("UOM", source["UOM_ID_SMALL"]), large_unit_id=ref("UOM", source["UOM_ID_LARGE"]),
                   unit_factor=_number(source["QTY_SMALL_UOM"]), purchase_account_id=ref("CHART", source["PUR_CODE"]),
                   sale_account_id=ref("CHART", source["EXP_CODE"]), inventory_account_id=None,
                   sale_rate_known=int(source.get("SALE_RATE") is not None), purchase_rate_known=int(source.get("LPUR_RATE") is not None),
                   sale_rate=_number(source.get("SALE_RATE") or "0"), purchase_rate=_number(source.get("LPUR_RATE") or "0"))
    memberships = {}
    for source in masters["STORE_LOC"]:
        row = put("STORE_LOC", source, source["STORE_ID"], "STORE_DESC", "STORE_DESC_URDU")
        if source.get("CMP_ID") is not None:
            memberships[ref("CONFIG", source["CMP_ID"]), row["id"]] = 1
    observed = {(row["CMP_ID"], row["STORE_ID"]) for row in headers["OPEN_STOCK"] + headers["BRMST"]}
    for table, master in (("PDTL", "PMST"), ("SDTL", "SMST"), ("BDDTL", "BDMST"), ("DCDTL", "DCMST")):
        company = {row["TRNID"]: row["CMP_ID"] for row in headers[master]}
        observed.update((company[row["TRNID"]], row["STORE_ID"]) for row in details[table])
    for company, store in observed:
        memberships.setdefault((ref("CONFIG", company), ref("STORE_LOC", store)), 0)

    product_issues = {}
    products = {row["ICODE"]: row for row in masters["PUR_ITEM"]}
    for row in headers["OPEN_STOCK"] + sum((details[table] for table in ("PDTL", "SDTL", "BRDTL", "BDDTL", "DCDTL")), []):
        if row.get("ICODE") not in products:
            if row.get("ICODE") is None:
                continue  # Original views also omit opening rows without an item.
            raise BaselineError("An original stock item does not resolve.")
        product = products[row["ICODE"]]
        if row.get("UOM_ID_SMALL") is not None and row["UOM_ID_SMALL"] != product["UOM_ID_SMALL"]:
            product_id = ref("PUR_ITEM", row["ICODE"])
            issue = product_issues.setdefault(product_id, dict(unit_mismatch_rows=0, source_units=[], base_unit_id=ref("UOM", product["UOM_ID_SMALL"])))
            issue["unit_mismatch_rows"] += 1
            issue["source_units"] = sorted(set(issue["source_units"]) | {row["UOM_ID_SMALL"]})
    numbers = []
    for table in ("PMST", "SMST", "BRMST", "BDMST", "JMST", "DCMST"):
        for source in headers[table]:
            type_code = source.get("VTYPE") if table == "JMST" else source.get("TRN_TYPE")
            kind = "delivery_challan" if table == "DCMST" else _KINDS.get(type_code)
            if kind is None:
                raise BaselineError("An original document type cannot be mapped without review.")
            if table in {"PMST", "SMST"} and source.get("P_TYPE" if table == "PMST" else "S_TYPE") == "C":
                kind = "cash_" + kind
            number = source.get("DCNO" if table == "DCMST" else "TRNNO")
            if number is None:
                raise BaselineError("An original document number is missing.")
            numbers.append(dict(company_id=ref("CONFIG", source["CMP_ID"]), kind=kind, document_number=number,
                                source_table=table, source_key=source["TRNID"]))
    return records, mappings, memberships, numbers, product_issues, normalized, ref


def _insert(connection, table, rows):
    for row in rows:
        columns = tuple(row)
        connection.execute(f"INSERT INTO {table}({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                           tuple(row[column] for column in columns))


def _trade_rows(masters, headers, details, records, mappings, ref, source_hash, entries):
    """Keep actionable invoice identities without creating native posted rows."""
    documents, lines, line_index = {}, {}, {}
    source_products = {row["ICODE"]: row for row in masters["PUR_ITEM"]}
    party_entries = {}
    for entry in entries:
        if entry.source_arm in {"sale_party", "purchase_party"}:
            table = "SMST" if entry.source_arm == "sale_party" else "PMST"
            party_entries.setdefault((entry.company_id, table, entry.transaction_id), []).append(entry)

    def identity(table, key, target):
        if key is None:
            raise BaselineError("An original invoice or line key is missing.")
        identifier = str(uuid5(NAMESPACE_URL, f"bifma:baseline:{source_hash}:{table}:{key}"))
        mappings.append(dict(source_table=table, source_key=key, native_table=target,
                             native_id=identifier, source_sha256=source_hash))
        return identifier

    def precision(value, *, positive=False, maximum=None):
        if value is None:
            return False
        number = Decimal(value)
        _, digits, exponent = number.as_tuple()
        return (number.is_finite() and number >= 0 and (not positive or number > 0)
                and (maximum is None or number <= maximum)
                and max(len(digits) + exponent, 0) <= 18 and max(-exponent, 0) <= 6)

    for table, detail, type_code, family in (("SMST", "SDTL", "8", "sale"), ("PMST", "PDTL", "7", "purchase")):
        by_header = {}
        for row in details[detail]:
            by_header.setdefault(row["TRNID"], []).append(row)
        for source in headers[table]:
            if source.get("TRN_TYPE") != type_code:
                continue
            identifier = identity(table, source["TRNID"], "baseline_trade_documents")
            company = ref("CONFIG", source["CMP_ID"])
            matching = party_entries.get((source["CMP_ID"], table, source["TRNID"]), [])
            total = None
            account_id = None
            reason = None
            if len(matching) == 1 and matching[0].account_code is not None:
                entry = matching[0]
                account_id = ref("CHART", entry.account_code)
                total = entry.debit if family == "sale" else entry.credit
                if total <= 0:
                    reason = "original_total_not_positive"
            else:
                reason = "original_party_total_ambiguous_or_absent"
            payment_type = source.get("S_TYPE" if family == "sale" else "P_TYPE")
            kind = "cash_" + family if payment_type == "C" else family
            if payment_type not in {"C", "R"}:
                reason = "original_payment_type_unknown"
            source_snapshot = {"company": records["companies"][company], "accounts": {}, "lines": []}
            if account_id is not None:
                source_snapshot["accounts"][account_id] = records["accounts"][account_id]
            if kind.startswith("cash_") and account_id is not None:
                source_snapshot["cash_settlement"] = dict(mode="cash", cash_account_id=account_id,
                                                           counterparty_name=source.get("CUSTOMER") or "",
                                                           counterparty_mobile=source.get("CUS_MOBILE") or "")
            inputs = dict(document_number=source["TRNNO"], business_date=_day(source["TRNDATE"]), account_id=account_id,
                          rounding_policy="legacy_whole", posting_places=0, notes=source.get("REMARKS") or "", lines=[],
                          **{field.lower(): source.get(field) or "0" for field in ("DISCOUNT", "BREAKAGE", "FREIGHT")})
            document = dict(id=identifier, company_id=company, kind=kind, document_number=source["TRNNO"],
                            business_date=_day(source["TRNDATE"]), account_id=account_id,
                            total_amount=None if total is None else _number(total), historical_returned_amount="0",
                            rounding_policy="legacy_whole", posting_places=0, input_json=None, snapshot_json=None,
                            source_table=table, source_key=source["TRNID"], source_json=_json(source),
                            return_eligible=int(reason is None), exclusion_reason=reason)
            documents[identifier] = document
            rows = by_header.get(source["TRNID"], [])
            rows.sort(key=lambda row: (Decimal(row.get("LINE_NUM") or "0"), Decimal(row["VNO"])))
            for number, row in enumerate(rows, 1):
                line_id = identity(detail, row["VNO"], "baseline_trade_lines")
                if (detail, row["VNO"]) in line_index:
                    raise BaselineError("Original invoice line keys are duplicated.")
                product_id, store_id = ref("PUR_ITEM", row["ICODE"]), ref("STORE_LOC", row["STORE_ID"])
                product = dict(records["products"][product_id])
                product["small_unit"] = records["units"][product["small_unit_id"]]
                product["large_unit"] = records["units"].get(product["large_unit_id"])
                posting_account = product["sale_account_id" if family == "sale" else "purchase_account_id"]
                line_snapshot = dict(line_id=line_id, product=product, store=records["stores"][store_id],
                                     posting_account=records["accounts"][posting_account])
                source_snapshot["lines"].append(line_snapshot)
                source_snapshot["accounts"][posting_account] = records["accounts"][posting_account]
                discount = row.get("DIS_PER") or "0"
                line_reason = None
                if not precision(row.get("QTY"), positive=True):
                    line_reason = "original_quantity_not_supported"
                elif not precision(row.get("RATE")):
                    line_reason = "original_rate_unknown_or_not_supported"
                elif not precision(discount, maximum=100):
                    line_reason = "original_line_discount_not_supported"
                elif row.get("UOM_ID_SMALL") != source_products[row["ICODE"]]["UOM_ID_SMALL"]:
                    line_reason = "original_unit_conflict_requires_review"
                if row.get("VNO_DCDTL") is not None:
                    line_reason = "original_delivery_link_requires_review"
                line = dict(id=line_id, company_id=company, document_id=identifier, line_number=number,
                            product_id=product_id, store_id=store_id, quantity=row.get("QTY"), rate=row.get("RATE"),
                            discount_percent=discount, net_amount=_number(_line_net(row.get("QTY"), row.get("RATE"), row.get("DIS_PER"))),
                            historical_returned_quantity="0", snapshot_json=_json(line_snapshot),
                            source_table=detail, source_key=row["VNO"], source_json=_json(row),
                            return_eligible=int(line_reason is None), exclusion_reason=line_reason)
                lines[line_id] = line
                line_index[detail, row["VNO"]] = line_id
                inputs["lines"].append({key: line[key] for key in ("product_id", "store_id", "quantity", "rate", "discount_percent")})
            document["input_json"], document["snapshot_json"] = _json(inputs), _json(source_snapshot)
            if not rows:
                document.update(return_eligible=0, exclusion_reason="original_has_no_lines")

    # Actual source references are the only accepted prior-return links. A
    # company/family containing an unlinked return cannot safely assume that
    # every original invoice still has its entire return capacity available.
    uncertain = set()
    for table, detail, type_code, family, origin_table, link_field in (
            ("PMST", "PDTL", "10", "sale", "SDTL", "VNO_SDTL"),
            ("SMST", "SDTL", "9", "purchase", "PDTL", "VNO_PDTL")):
        for header in headers[table]:
            if header.get("TRN_TYPE") != type_code:
                continue
            company = ref("CONFIG", header["CMP_ID"])
            returned = [row for row in details[detail] if row["TRNID"] == header["TRNID"]]
            linked = []
            invalid = not returned
            for row in returned:
                key = line_index.get((origin_table, row.get(link_field)))
                original = lines.get(key)
                if (original is None or original["company_id"] != company or not precision(row.get("QTY"), positive=True)
                        or original["product_id"] != ref("PUR_ITEM", row["ICODE"])):
                    invalid = True
                    break
                linked.append((original, Decimal(row["QTY"])))
            original_docs = {line["document_id"] for line, _ in linked}
            party = party_entries.get((header["CMP_ID"], table, header["TRNID"]), [])
            if invalid or len(original_docs) != 1 or len(party) != 1:
                uncertain.add((company, family))
                continue
            original_doc = documents[next(iter(original_docs))]
            total = party[0].credit if family == "sale" else party[0].debit
            if (total < 0 or party[0].account_code is None or original_doc["account_id"] != ref("CHART", party[0].account_code)):
                uncertain.add((company, family))
                continue
            for line, quantity in linked:
                line["historical_returned_quantity"] = _number(_exact_add(Decimal(line["historical_returned_quantity"]), quantity))
            original_doc["historical_returned_amount"] = _number(_exact_add(Decimal(original_doc["historical_returned_amount"]), total))
    for row in details["BRDTL"]:
        line_id = line_index.get(("SDTL", row.get("VNO_SDTL")))
        if line_id is not None:
            document = documents[lines[line_id]["document_id"]]
            document.update(return_eligible=0, exclusion_reason="original_has_linked_breakage_return_requiring_review")
    for document in documents.values():
        if (document["company_id"], document["kind"].removeprefix("cash_")) in uncertain:
            document.update(return_eligible=0, exclusion_reason="unlinked_historical_returns_require_review")
        if document["total_amount"] is not None and Decimal(document["historical_returned_amount"]) > Decimal(document["total_amount"]):
            document.update(return_eligible=0, exclusion_reason="historical_return_amount_exceeds_original")
    for line in lines.values():
        document = documents[line["document_id"]]
        if not document["return_eligible"]:
            line.update(return_eligible=0, exclusion_reason=document["exclusion_reason"])
        elif line["quantity"] is not None and Decimal(line["historical_returned_quantity"]) > Decimal(line["quantity"]):
            line.update(return_eligible=0, exclusion_reason="historical_return_quantity_exceeds_original")
    return list(documents.values()), list(lines.values())


def _import(source, target, source_hash):
    masters, headers, details, cutoff = _read_source(source)
    records, mappings, memberships, numbers, issues, normalized, ref = _prepare_rows(masters, headers, details, source_hash)
    account_rows, stock_rows, rate_rows, ledger_entries = [], [], [], []
    company_totals = {}
    ledger, stock = LegacyLedger(source), LegacyStock(source)
    for company in masters["CONFIG"]:
        ledger_entries.extend(ledger.entries(company["CMP_ID"], date_to=cutoff))
    cash_headers = {(row["CMP_ID"], "sale_party" if table == "SMST" else "purchase_party", row["TRNID"])
                    for table in ("SMST", "PMST") for row in headers[table]
                    if row.get("S_TYPE" if table == "SMST" else "P_TYPE") == "C"}
    configured_cash = {ref("CHART", row["CHCODE_CASH"]) for row in masters["CONFIG"]}
    proven_cash = {ref("CHART", row.account_code) for row in ledger_entries
                   if (row.company_id, row.source_arm, row.transaction_id) in cash_headers and row.account_code is not None}
    for identifier in proven_cash:
        records["accounts"][identifier]["kind"] = "cash"
    candidate_sources = {}
    for row in headers["OPEN_STOCK"]:
        if row.get("TRNID") is not None and row.get("ICODE") is not None:
            candidate_sources.setdefault((row["CMP_ID"], row["ICODE"], "primary", Decimal(row["TRNID"])), []).append(
                dict(table="OPEN_STOCK", source_field="OPEN_RATE", source_date=row["OPEN_DATE"], source_id=row["TRNID"]))
    purchase_headers = {row["TRNID"]: row for row in headers["PMST"]}
    for row in details["PDTL"]:
        header = purchase_headers[row["TRNID"]]
        if row.get("VNO") is not None and header.get("TRN_TYPE") in {"7", "10"}:
            rate_source = "primary" if header["TRN_TYPE"] == "7" else "fallback"
            candidate_sources.setdefault((header["CMP_ID"], row["ICODE"], rate_source, Decimal(row["VNO"])), []).append(
                dict(table="PDTL", source_field="RATE", source_date=header["TRNDATE"], source_id=row["VNO"]))
    for company in masters["CONFIG"]:
        company_id = ref("CONFIG", company["CMP_ID"])
        upper = _date_bound(cutoff, end=True)
        with session(source) as connection:
            connection.execute("BEGIN")
            candidates = stock._rates(connection, company["CMP_ID"], upper, None)
        for product in masters["PUR_ITEM"]:
            code = product["ICODE"]
            rate = candidates.at(code, upper)  # Includes the legacy ambiguity check.
            kind, state = "missing", None
            for kind_candidate, group in (("primary", candidates.primary), ("fallback", candidates.fallback)):
                timeline = group.get(code)
                state = timeline.at(upper) if timeline else None
                if state is not None:
                    kind = kind_candidate
                    break
            provenance = {} if state is None else next(
                row for row in candidate_sources[company["CMP_ID"], code, kind, state.identifier] if row["source_date"] <= upper)
            rate_rows.append(dict(company_id=company_id, product_id=ref("PUR_ITEM", code), rate=_number(rate),
                                  rate_source=kind, source_id=None if state is None else _number(state.identifier), source_json=_json(provenance)))
        total = Decimal(0)
        for balance in ledger.balances(company["CMP_ID"], date_to=cutoff):
            account_id = ref("CHART", balance.account_code)
            total = _exact_add(total, balance.closing)
            account_rows.append(dict(company_id=company_id, account_id=account_id, balance=_number(balance.closing),
                                     labels_json=_json({"account": records["accounts"][account_id], "company": records["companies"][company_id]})))
        company_totals[company_id] = _number(total)
        for pool in ("regular", "breakage"):
            for balance in stock.balances(company["CMP_ID"], date_to=cutoff, stream=pool):
                product_id, store_id = ref("PUR_ITEM", balance.product_code), ref("STORE_LOC", balance.store_id)
                unit_id = records["products"][product_id]["small_unit_id"]
                stock_rows.append(dict(company_id=company_id, store_id=store_id, product_id=product_id, pool=pool,
                                       quantity=_number(balance.closing_quantity), historical_value=_number(balance.closing_value),
                                       base_unit_id=unit_id, labels_json=_json({"product": records["products"][product_id],
                                                                              "store": records["stores"][store_id], "unit": records["units"][unit_id]})))
    trade_documents, trade_lines = _trade_rows(masters, headers, details, records, mappings, ref, source_hash, ledger_entries)
    manifest = dict(format=1, source_sha256=source_hash, cutoff_date=cutoff,
                    counts={table: len(rows) for table, rows in records.items()},
                    account_balance_rows=len(account_rows), stock_balance_rows=len(stock_rows),
                    purchase_rate_rows=len(rate_rows),
                    trade_document_rows=len(trade_documents), trade_line_rows=len(trade_lines),
                    source_document_rows=len(numbers), company_account_net=company_totals,
                    cash_role_evidence={identifier: dict(configuration=identifier in configured_cash,
                                                          invoice_party_arm=identifier in proven_cash)
                                        for identifier in sorted(configured_cash | proven_cash)},
                    normalized_fields=normalized, product_issues=issues,
                    anomalies=dict(negative_stock_groups=sum(Decimal(row["quantity"]) < 0 for row in stock_rows),
                                   unit_mismatch_rows=sum(issue["unit_mismatch_rows"] for issue in issues.values()),
                                   unit_mismatch_products=len(issues),
                                   unknown_sale_rates=sum(not row["sale_rate_known"] for row in records["products"].values()),
                                   unknown_purchase_rates=sum(not row["purchase_rate_known"] for row in records["products"].values()),
                                   missing_purchase_cost_rates=sum(row["rate_source"] == "missing" for row in rate_rows),
                                   unknown_account_postability=sum(row.get("ALLOWED") is None for row in masters["CHART"]),
                                   inactive_observed_store_memberships=sum(not active for active in memberships.values()),
                                   unbalanced_companies=sum(value != "0" for value in company_totals.values())),
                    policies=["Original source documents and historical calculation rules remain unchanged.",
                              "The closing date is the latest business date in the recovered archive; new postings must follow it.",
                              "Account balances and stock quantities are carried separately; no inventory valuation journal is added.",
                              "Negative stock and accumulated historical stock values are preserved exactly.",
                              "New inventory cost continues the original highest-ID eligible purchase/opening-rate method, with sales-return fallback.",
                              "Current original item units define carried quantities; stored unit conflicts are listed for review.",
                              "Unknown master rates remain marked unknown and require explicit entry or master review.",
                              "Cash roles follow company configuration and proven original cash-invoice postings; other account roles and classification are not inferred.",
                              "Accounts with missing original posting permission remain non-postable until administrator review.",
                              "Only declared company/store memberships are active; additional historical memberships remain inactive."])
    manifest["anomalies"].update(ineligible_return_documents=sum(not row["return_eligible"] for row in trade_documents),
                                 ineligible_return_lines=sum(not row["return_eligible"] for row in trade_lines))
    if _source_digest(source) != source_hash:
        raise BaselineError("The original archive changed while its starting balances were being prepared.")
    with native_transaction(target) as connection:
        for table in ("cities", "accounts", "companies", "units", "product_groups", "products", "stores"):
            _insert(connection, table, records[table].values())
        _insert(connection, "company_stores", [dict(company_id=company, store_id=store, is_active=active)
                                                for (company, store), active in memberships.items()])
        _insert(connection, "baseline_mappings", mappings)
        _insert(connection, "baseline_account_balances", account_rows)
        _insert(connection, "baseline_stock_balances", stock_rows)
        _insert(connection, "baseline_document_numbers", numbers)
        _insert(connection, "baseline_purchase_rates", rate_rows)
        _insert(connection, "baseline_trade_documents", trade_documents)
        _insert(connection, "baseline_trade_lines", trade_lines)
        manifest["immutable_sha256"] = _immutable_digest(connection)
        _insert(connection, "baseline_imports", [dict(singleton=1, source_sha256=source_hash, cutoff_date=cutoff, manifest_json=_json(manifest))])
        _insert(connection, "audit_events", [dict(id=str(uuid4()), action="baseline.imported", entity_type="baseline",
                                                   details_json=_json(dict(source_sha256=source_hash, cutoff_date=cutoff,
                                                                          account_balance_rows=len(account_rows), stock_balance_rows=len(stock_rows))))])
    return cutoff, manifest


def ensure_baseline(source, destination, *, upgrade=False):
    """Create one fully seeded live file or reuse its verified matching baseline.

    No existing file is overwritten or adopted. The default does not migrate;
    the application's explicit startup opt-in permits the guarded login upgrade.
    Construction
    and reconciliation finish in a private temporary directory before atomic
    publication, so interruption cannot leave an empty official live database.
    """
    source = Path(source).resolve()
    destination = Path(destination)
    destination = destination.parent.resolve() / destination.name
    source_hash = _source_digest(source)
    if os.path.lexists(destination):
        info = prepare_native_login(destination, source_sha256=source_hash) if upgrade else baseline_info(destination)
        if info is None or info.source_sha256 != source_hash:
            raise BaselineError("This live database does not belong to the selected original baseline.")
        return info
    new_destination(destination)
    with tempfile.TemporaryDirectory(prefix=".bifma-baseline-", dir=destination.parent) as temporary:
        target = Path(temporary) / "prepared.sqlite3"
        initialize_database(target)
        cutoff, manifest = _import(source, target, source_hash)
        baseline_info(target)  # Full SQLite integrity and immutable-content check.
        if _source_digest(source) != source_hash:
            raise BaselineError("The original archive changed before its starting balances could be published.")
        try:
            publish_new_file(target, destination)
        except FileExistsError:
            # Another starter won publication. Reuse only the same verified data.
            info = prepare_native_login(destination, source_sha256=source_hash) if upgrade else baseline_info(destination)
            if info is None or info.source_sha256 != source_hash:
                raise BaselineError("Another database appeared at the live database path.") from None
            return info
    return BaselineResult(destination, True, source_hash, cutoff, manifest)
