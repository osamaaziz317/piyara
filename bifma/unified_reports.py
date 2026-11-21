"""Verified original activity and new postings through the same report controls."""

from __future__ import annotations

from decimal import Context, Decimal, localcontext

from bifma.database import session
from bifma.document_register import _TRADES
from bifma.legacy_stock import LegacyStock
from bifma.native_schema import native_transaction
from bifma.native_reports import (
    NativeReports, _add_value, _dates, _finish_values, _row_limit, _stock_store,
    _value_totals, _limits, _trade_filters, _trade_predicate, _trade_scope, _trade_totals,
)


class UnifiedReports(NativeReports):
    """Use original stock flows for earlier periods, without replaying balances.

    Trade detail merges original invoice/return rows and new posted activity.
    Financial and item ledgers retain the documented closing-snapshot boundary.
    Archive verification runs before a managed native transaction;
    the original read occurs inside that authorized snapshot and remains
    read-only. Exports use these same query methods and the usual final grant
    check before publication.
    """

    def __init__(self, path, archive_path=None, *, security=None, register=None):
        super().__init__(path, security=security)
        if register is None:
            from bifma.document_register import DocumentRegister
            register = DocumentRegister(path, archive_path, security=self.security)
        self.register = register

    def _historical_period(self, date_from=None):
        baseline = self.register.baseline
        return baseline is not None and (date_from is None or date_from <= baseline.cutoff_date)

    def _verify_period(self, date_from, date_to):
        _dates(date_from, date_to)
        if self._historical_period(date_from):
            self.register.verify_archive()

    def stock(self, actor, company_id, *, date_from=None, date_to=None, store_id=None, pool="regular", search=""):
        self._verify_period(date_from, date_to)
        return super().stock(actor, company_id, date_from=date_from, date_to=date_to,
                             store_id=store_id, pool=pool, search=search)

    def stock_valuation(self, actor, company_id, *, date_from=None, date_to=None, store_id=None, pool="regular", search=""):
        self._verify_period(date_from, date_to)
        return super().stock_valuation(actor, company_id, date_from=date_from, date_to=date_to,
                                       store_id=store_id, pool=pool, search=search)

    def export_snapshot(self, actor, company_id, report, **filters):
        stock_report = report in {"stock_quantities", "stock_valuation"}
        trade_report = report in {"sales_detail", "purchase_detail"}
        if stock_report:
            self._verify_period(filters.get("date_from"), filters.get("date_to"))
        if trade_report:
            self.register.verify_archive()
        snapshot = super().export_snapshot(actor, company_id, report, **filters)
        if stock_report and self._historical_period(filters.get("date_from")):
            snapshot["historical_activity"] = True
        if trade_report and snapshot["result"].get("original_trade_activity"):
            snapshot["original_trade_activity"] = True
            self.register.assert_archive_unchanged()
        return snapshot

    def verify_export_archive(self, snapshot):
        """Recheck the original file after rendering, outside native sessions."""
        if snapshot.get("original_trade_activity"):
            self.register.verify_archive()

    def assert_export_archive_unchanged(self, snapshot):
        if snapshot.get("original_trade_activity"):
            self.register.assert_archive_unchanged()

    def trade_selectors(self, actor, company_id, family, kind="all", *, payment_mode="account"):
        kinds, modules = _trade_scope(family, kind, payment_mode)
        self.register.verify_archive()
        with native_transaction(self.path) as connection:
            for module in modules:
                self.security.require(actor, company_id, module, "read", connection=connection)
            result = NativeReports._query_trade_selectors(connection, company_id, kinds)
            source_company = self.register.source_company(connection, company_id)
            if source_company is not None:
                with session(self.register.archive_path) as original:
                    original.execute("BEGIN")
                    headers = self._original_trade_headers(original, source_company, kinds)
                    self._merge_original_selectors(connection, original, headers, result)
            self.register.assert_archive_unchanged()
            return result

    def trade_detail(self, actor, company_id, family, *, kind="all", payment_mode="account", date_from=None,
                     date_to=None, account_id=None, product_id=None, store_id=None, search="", offset=0, limit=100):
        _, modules = _trade_filters(family, kind, date_from, date_to, account_id, product_id, store_id, search, payment_mode)
        _limits(limit, offset)
        self.register.verify_archive()
        with native_transaction(self.path) as connection, localcontext(Context(prec=1000)):
            for module in modules:
                self.security.require(actor, company_id, module, "read", connection=connection)
            return self._query_trade_detail(connection, company_id, family, kind=kind, payment_mode=payment_mode,
                date_from=date_from, date_to=date_to, account_id=account_id, product_id=product_id,
                store_id=store_id, search=search, offset=offset, limit=limit)

    def _original_trade_headers(self, original, company, kinds):
        headers = [row for kind in kinds for row in
                   self.register._original_rows(original, company, kind, "", None, None)]
        return sorted(headers, key=lambda row: (row["business_date"] or "", row["source_table"], row["id"]))

    @staticmethod
    def _original_trade_lines(original, kind, headers):
        """Bound each query without dropping headers that have no saved lines."""
        table = _TRADES[kind][1]
        for start in range(0, len(headers), 100):
            batch = headers[start:start + 100]
            lines = {row["id"]: [] for row in batch}
            marks = ",".join("?" for _ in batch)
            for row in original.execute(f"SELECT * FROM {table} WHERE TRNID IN ({marks}) "
                                        "ORDER BY CAST(LINE_NUM AS INTEGER),VNO", list(lines)):
                lines[row["TRNID"]].append(dict(row))
            for header in batch:
                yield header, lines[header["id"]]

    @staticmethod
    def _trade_mappings(connection):
        return {(row["source_table"], row["source_key"]): row["native_id"] for row in connection.execute(
            "SELECT source_table,source_key,native_id FROM baseline_mappings "
            "WHERE source_table IN ('CHART','PUR_ITEM','STORE_LOC','UOM')")}

    def _merge_original_selectors(self, connection, original, headers, result):
        mappings = self._trade_mappings(connection)
        selected = {"accounts": set(), "products": set(), "stores": set()}
        for header in headers:
            selected["accounts"].add(mappings.get(("CHART", header["account_code"])))
        for kind in dict.fromkeys(row["kind"] for row in headers):
            for _, lines in self._original_trade_lines(original, kind, [h for h in headers if h["kind"] == kind]):
                for line in lines:
                    selected["products"].add(mappings.get(("PUR_ITEM", line["ICODE"])))
                    selected["stores"].add(mappings.get(("STORE_LOC", line["STORE_ID"])))
        for table, identifiers in selected.items():
            by_id = {row["id"]: row for row in result[table]}
            for row in connection.execute(f"SELECT id,code,name,name_urdu,is_active FROM {table}"):
                if row["id"] in identifiers:
                    by_id[row["id"]] = dict(row)
            result[table] = sorted(by_id.values(), key=lambda row: (row["code"], row["id"]))

    @staticmethod
    def _add_trade_value(totals, effect, value):
        for key in (effect, "net"):
            totals[key] = None if value is None or totals[key] is None else totals[key] + value

    def _original_trade_detail(self, connection, original, company, headers, *, date_from, date_to,
                               account_id, product_id, store_id, search, offset, limit, max_rows):
        mappings = self._trade_mappings(connection)
        masters = {table: {row[key]: dict(row) for row in original.execute(f"SELECT * FROM {table}")}
                   for table, key in (("CHART", "CHCODE"), ("PUR_ITEM", "ICODE"), ("STORE_LOC", "STORE_ID"), ("UOM", "UOM_ID"))}
        totals, quantities, page = _trade_totals(), {}, []
        document_totals = _trade_totals() if product_id is None and store_id is None else None
        count = documents = missing_amounts = missing_quantities = unknown_documents = issues_count = empty_documents = 0
        term = search.strip().casefold()
        headers = [row for row in headers if
                   (date_from is None or (row["business_date"] or "") >= date_from)
                   and (date_to is None or row["business_date"] is not None and row["business_date"] <= date_to)
                   and (account_id is None or mappings.get(("CHART", row["account_code"])) == account_id)
                   and (not term or term in (row["document_number"] or "").casefold())]
        by_header = {}
        for kind in dict.fromkeys(row["kind"] for row in headers):
            scoped = [row for row in headers if row["kind"] == kind]
            for start in range(0, len(scoped), 100):
                self.register._original_amounts(original, company, kind, scoped[start:start + 100])
            by_header.update({(kind, header["id"]): lines for header, lines in
                              self._original_trade_lines(original, kind, scoped)})

        def labels(table, raw, prefix, code_key, name_key, urdu_key):
            master = masters[table].get(raw)
            identifier = mappings.get((table, raw))
            if master is not None and identifier is None:
                raise ValueError("An original trade reference no longer matches the working database.")
            return identifier, {prefix + "_code": (master[code_key] if master else raw) or "",
                prefix + "_name": (master[name_key] if master else None) or "Unknown",
                prefix + "_name_urdu": master.get(urdu_key) if master else None}

        def number(value):
            if value is None:
                return None
            result = Decimal(value)
            if not result.is_finite():
                raise ValueError("An original trade contains a non-finite amount.")
            return result

        for header in headers:
            kind = header["kind"]
            lines = by_header[kind, header["id"]]
            selected_lines = [line for line in lines if
                (product_id is None or mappings.get(("PUR_ITEM", line["ICODE"])) == product_id)
                and (store_id is None or mappings.get(("STORE_LOC", line["STORE_ID"])) == store_id)]
            if not selected_lines and (product_id is not None or store_id is not None):
                continue
            documents += 1
            empty_documents += not lines
            effect = "return" if kind.endswith("_return") else "invoice"
            sign = -1 if effect == "return" else 1
            amount = number(header["total_amount"])
            unknown_documents += amount is None
            if document_totals is not None:
                self._add_trade_value(document_totals, effect, amount * sign if amount is not None else None)
            account, account_labels = labels("CHART", header["account_code"], "account", "CHCODE", "CHDESC", "CHDESC_URDU")
            if account is None and header["account"]:
                account_labels["account_name"] = header["account"]
            for line in selected_lines:
                count += 1
                _row_limit(count, max_rows)
                product, product_labels = labels("PUR_ITEM", line["ICODE"], "product", "ICODE", "INAME", "INAME_URDU")
                store, store_labels = labels("STORE_LOC", line["STORE_ID"], "store", "STORE_ID", "STORE_DESC", "STORE_DESC_URDU")
                unit, unit_labels = labels("UOM", line["UOM_ID_SMALL"], "unit", "UOM_ID", "UOM_DESC", "UOM_DESC_URDU")
                quantity, rate = number(line["QTY"]), number(line["RATE"])
                percentage = number(line["DIS_PER"]) if line["DIS_PER"] is not None else Decimal(0)
                net = quantity * rate * (1 - percentage / 100) if quantity is not None and rate is not None else None
                signed_quantity = quantity * sign if quantity is not None else None
                signed_amount = net * sign if net is not None else None
                issues = []
                for value, title in ((account, "Party reference unavailable"), (product, "Item reference unavailable"),
                                     (store, "Store reference unavailable"), (unit, "Recorded unit unavailable"),
                                     (quantity, "Quantity unavailable"), (rate, "Rate unavailable")):
                    if value is None:
                        issues.append(title)
                item = masters["PUR_ITEM"].get(line["ICODE"])
                if item is not None and line["UOM_ID_SMALL"] is not None and line["UOM_ID_SMALL"] != item["UOM_ID_SMALL"]:
                    issues.append("Recorded unit differs from original item master")
                if quantity is not None and quantity < 0:
                    issues.append("Negative original quantity")
                if rate is not None and rate < 0:
                    issues.append("Negative original rate")
                if percentage < 0 or percentage > 100:
                    issues.append("Original discount outside 0–100%")
                if header["original_payment_type"] not in {"C", "R"}:
                    issues.append("Original payment type unclassified")
                if amount is None:
                    issues.append("Recorded party amount unavailable")
                missing_amounts += net is None
                missing_quantities += quantity is None
                issues_count += bool(issues)
                self._add_trade_value(totals, effect, signed_amount)
                # Unknown unit references retain their raw identity; they must
                # not merge with a later known unit or another unknown item.
                key = (product or ("original_item", line["ICODE"]), unit or ("original_unit", line["UOM_ID_SMALL"]))
                if key not in quantities:
                    quantities[key] = {"product_id": product, "unit_id": unit, "source_product_code": line["ICODE"],
                        "source_unit_code": line["UOM_ID_SMALL"], **product_labels, **unit_labels, **_trade_totals()}
                self._add_trade_value(quantities[key], effect, signed_quantity)
                if offset <= count - 1 < offset + limit:
                    page.append({"document_id": None, "document_line_id": None, "source": "original",
                        "source_table": header["source_table"], "source_document_id": header["id"],
                        "source_line_table": _TRADES[kind][1], "source_line_id": line["VNO"],
                        "source_sha256": self.register.baseline.source_sha256,
                        "document_number": header["document_number"], "business_date": header["business_date"],
                        "kind": kind, "source_kind": kind, "payment_mode": "cash" if kind.startswith("cash_") else "account",
                        "posting_sequence": None, "posted_at": None, "account_id": account, "product_id": product,
                        "store_id": store, "unit_id": unit, "line_number": line["LINE_NUM"],
                        "reverses_document_id": None, "reverses_document_number": None,
                        **account_labels, **product_labels, **store_labels, **unit_labels,
                        "sign": sign, "quantity": quantity, "signed_quantity": signed_quantity, "rate": rate,
                        "discount_percent": percentage, "net_amount": net, "signed_net_amount": signed_amount,
                        "issues": issues, "amount_known": net is not None, "quantity_known": quantity is not None,
                        "unit_known": unit is not None, "recorded_party_amount": amount})
        return {"rows": page, "total": count, "document_count": documents, "totals": totals,
                "document_totals": document_totals, "quantity_totals": quantities,
                "missing_amount_count": missing_amounts, "missing_quantity_count": missing_quantities,
                "unknown_document_amount_count": unknown_documents, "original_issue_count": issues_count,
                "original_empty_document_count": empty_documents}

    def _query_trade_detail(self, connection, company_id, family, *, kind="all", payment_mode="account", date_from=None,
                            date_to=None, account_id=None, product_id=None, store_id=None, search="", offset=0, limit=100, max_rows=None):
        options = dict(kind=kind, payment_mode=payment_mode, date_from=date_from, date_to=date_to,
                       account_id=account_id, product_id=product_id, store_id=store_id, search=search)
        source_company = self.register.source_company(connection, company_id)
        if source_company is None:
            return NativeReports._query_trade_detail(connection, company_id, family, offset=offset, limit=limit, max_rows=max_rows, **options)
        kinds, _ = _trade_scope(family, kind, payment_mode)
        native_selectors = NativeReports._query_trade_selectors(connection, company_id, kinds)
        selectors = {key: list(rows) for key, rows in native_selectors.items()}
        with session(self.register.archive_path) as original:
            original.execute("BEGIN")
            headers = self._original_trade_headers(original, source_company, kinds)
            self._merge_original_selectors(connection, original, headers, selectors)
            selected = {}
            native_possible = True
            for field, identifier, table in (("account", account_id, "accounts"), ("product", product_id, "products"), ("store", store_id, "stores")):
                selected[field] = next((row for row in selectors[table] if row["id"] == identifier), None) if identifier else None
                if identifier is not None and selected[field] is None:
                    raise ValueError(f"Select a {field} with original or posted {family} history in this company, kind and payment mode.")
                if identifier and not any(row["id"] == identifier for row in native_selectors[table]):
                    native_possible = False
            saved = self._original_trade_detail(connection, original, source_company, headers,
                **{key: value for key, value in options.items() if key not in {"kind", "payment_mode"}},
                offset=offset, limit=limit, max_rows=max_rows)
        # Cutover establishes a strict chronological boundary. This allows the
        # combined page to span sources without loading every native detail row.
        cutoff = self.register.baseline.cutoff_date
        predicate, parameters = _trade_predicate(company_id, kinds)
        if connection.execute("SELECT 1 FROM documents d LEFT JOIN documents original ON original.id=d.reverses_document_id "
                              "AND original.company_id=d.company_id WHERE " + predicate + " AND d.business_date<=? LIMIT 1",
                              [*parameters, cutoff]).fetchone() is not None:
            raise ValueError("A new trade overlaps the preserved original reporting period.")
        native = (NativeReports._query_trade_detail(connection, company_id, family,
                  offset=max(0, offset - saved["total"]), limit=limit - len(saved["rows"]), max_rows=max_rows, **options)
                  if native_possible else {"rows": [], "total": 0, "document_count": 0, "totals": _trade_totals(),
                     "document_totals": _trade_totals() if product_id is None and store_id is None else None, "quantity_totals": []})
        total = saved["total"] + native["total"]
        _row_limit(total, max_rows)
        for key in ("totals", "document_totals"):
            if saved[key] is not None:
                for effect in saved[key]:
                    value = native[key][effect]
                    saved[key][effect] = None if saved[key][effect] is None or value is None else saved[key][effect] + value
        quantities = saved["quantity_totals"]
        for row in native["quantity_totals"]:
            key = row["product_id"], row["unit_id"]
            if key not in quantities:
                quantities[key] = dict(row)
            else:
                for effect in ("invoice", "return", "reversal", "net"):
                    value = quantities[key][effect]
                    quantities[key][effect] = None if value is None else value + row[effect]
        self.register.assert_archive_unchanged()
        return {"family": family, **options, **selected, **saved,
            "rows": saved["rows"] + [{**row, "source": "current", "issues": [], "amount_known": True,
                                       "quantity_known": True, "unit_known": True} for row in native["rows"]],
            "total": total, "document_count": saved["document_count"] + native["document_count"],
            "original_document_count": saved["document_count"], "current_document_count": native["document_count"],
            "offset": offset, "limit": limit, "original_trade_activity": True,
            "quantity_totals": sorted(quantities.values(), key=lambda row: (row["product_code"], row["unit_code"], row["product_id"] or "", row["unit_id"] or ""))}

    @staticmethod
    def _source_key(connection, table, identifier):
        row = connection.execute("SELECT source_key FROM baseline_mappings WHERE source_table=? AND native_id=?",
                                 (table, identifier)).fetchone()
        return row[0] if row is not None else None

    def _query_stock(self, connection, company_id, date_from=None, date_to=None, store_id=None,
                     pool="regular", search="", *, max_rows=None):
        if not self._historical_period(date_from):
            return NativeReports._query_stock(connection, company_id, date_from, date_to, store_id, pool, search, max_rows=max_rows)
        if store_id is not None:
            _stock_store(connection, company_id, store_id)
        source_company = self.register.source_company(connection, company_id)
        baseline = self.register.baseline
        source_store = self._source_key(connection, "STORE_LOC", store_id) if store_id is not None else None
        upper = min(date_to, baseline.cutoff_date) if date_to is not None else baseline.cutoff_date
        original = []
        if source_company is not None and (store_id is None or source_store is not None):
            if self.register.archive_path is None:
                raise ValueError("The original archive is required to report stock activity in this date range.")
            original = LegacyStock(self.register.archive_path).balances(
                source_company, date_from=date_from, date_to=upper, store_id=source_store, stream=pool)
            self.register.assert_archive_unchanged()
        products = {row["id"]: dict(row) for row in connection.execute("SELECT id,code,name,name_urdu FROM products")}
        stores = {row["id"]: dict(row) for row in connection.execute("SELECT id,code,name,name_urdu FROM stores")}
        mappings = {(row["source_table"], row["source_key"]): row["native_id"] for row in connection.execute(
            "SELECT source_table,source_key,native_id FROM baseline_mappings WHERE source_table IN ('PUR_ITEM','STORE_LOC')")}
        balances = {}
        term = search.strip().casefold()

        def item(product_id, location):
            product, store = products[product_id], stores[location]
            if term and term not in (product["code"] + " " + product["name"]).casefold():
                return None
            key = product_id, location
            if key not in balances:
                _row_limit(len(balances) + 1, max_rows)
                balances[key] = dict(product_id=product_id, store_id=location, code=product["code"], name=product["name"],
                                     name_urdu=product["name_urdu"], store=store["name"], store_code=store["code"],
                                     store_name_urdu=store["name_urdu"], opening=Decimal(0), **{"in": Decimal(0), "out": Decimal(0)},
                                     **_value_totals())
            return balances[key]

        with localcontext(Context(prec=1000)):
            for balance in original:
                key = ("PUR_ITEM", balance.product_code)
                location = ("STORE_LOC", balance.store_id)
                if key not in mappings or location not in mappings:
                    raise ValueError("An original stock reference no longer matches the working database.")
                row = item(mappings[key], mappings[location])
                if row is None:
                    continue
                row["opening"] = balance.opening_quantity
                row["in"], row["out"] = balance.quantity_in, balance.quantity_out
                row["value_opening"] = balance.opening_value
                row["value_in"], row["value_out"] = balance.value_in, balance.value_out
            if connection.execute("SELECT 1 FROM posted_stock_movements WHERE company_id=? AND pool IS NULL "
                                  "AND (? IS NULL OR store_id=?) AND (? IS NULL OR business_date<=?) LIMIT 1",
                                  (company_id, store_id, store_id, date_to, date_to)).fetchone() is not None:
                raise ValueError("Some posted movements have no verified stock pool. Reconcile them before reporting stock quantities.")
            movements = connection.execute(
                "SELECT m.*,CASE WHEN v.movement_id IS NULL AND m.valuation_basis='opening_explicit' "
                "THEN m.value ELSE v.value END AS saved_value FROM posted_stock_movements m "
                "LEFT JOIN stock_valuation_snapshots v ON v.movement_id=m.id "
                "WHERE m.company_id=? AND m.pool=? AND (? IS NULL OR m.store_id=?) "
                "AND (? IS NULL OR m.business_date<=?) ORDER BY m.business_date,m.document_id,m.id",
                (company_id, pool, store_id, store_id, date_to, date_to))
            for movement in movements:
                if movement["business_date"] <= baseline.cutoff_date:
                    raise ValueError("A new stock movement overlaps the preserved original reporting period.")
                row = item(movement["product_id"], movement["store_id"])
                if row is None:
                    continue
                before = bool(date_from and movement["business_date"] < date_from)
                direction, quantity = movement["direction"], Decimal(movement["quantity"])
                if before:
                    row["opening"] += quantity if direction == "in" else -quantity
                else:
                    row[direction] += quantity
                value = Decimal(movement["saved_value"]) if movement["saved_value"] is not None else None
                _add_value(row, value, direction, before)
            for row in balances.values():
                row["closing"] = row["opening"] + row["in"] - row["out"]
                _finish_values(row)
        self.register.assert_archive_unchanged()
        return sorted(balances.values(), key=lambda row: (row["code"], row["store_code"], row["product_id"], row["store_id"]))

    def _query_stock_valuation(self, connection, company_id, date_from=None, date_to=None,
                               store_id=None, pool="regular", search="", *, max_rows=None):
        rows = self._query_stock(connection, company_id, date_from, date_to, store_id, pool, search, max_rows=max_rows)
        with localcontext(Context(prec=1000)):
            totals = {"missing_valuation_count": sum(row["missing_valuation_count"] for row in rows)}
            for key in ("value_opening", "value_in", "value_out", "value_closing"):
                totals[key] = sum((row[key] for row in rows), Decimal(0)) if all(row[key] is not None for row in rows) else None
        source = connection.execute("SELECT source_sha256,cutoff_date FROM baseline_imports").fetchone()
        return dict(rows=rows, totals=totals, baseline=dict(source) if source else None,
                    historical_activity=self._historical_period(date_from),
                    store=_stock_store(connection, company_id, store_id) if store_id is not None else None,
                    pool=pool, date_from=date_from, date_to=date_to)
