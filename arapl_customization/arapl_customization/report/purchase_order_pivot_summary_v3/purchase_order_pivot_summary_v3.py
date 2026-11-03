# Copyright (c) 2026, and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import flt

# Amounts are shown in the PO currency, and again in the company currency using the
# PO's own exchange rate (conversion_rate), which is shown as its own column.
# The query is static: an optional filter is skipped when its value is NULL, so no SQL
# is built from strings.
QUERY = """
	SELECT
		po.name AS `Purchase Orders`,
		po.name AS `Purchase Order`,
		po.name AS `Orders`,
		po.name AS `purchase_order`,
		po.name AS `purchase_orders`,
		po.transaction_date AS `Transaction Dates`,
		po.transaction_date AS `Transaction Date`,
		po.currency AS `currency`,
		comp.default_currency AS `company_currency`,
		po.conversion_rate AS `Exchange Rate`,
		poi.budget_allocation AS `Budget Allocation`,
		poi.department AS `Department`,
		poi.expense_account AS `Expense Account`,
		COALESCE(NULLIF(poi.project, ''), po.project) AS `Project Code`,
		/* Convert Allocation to PO Currency if not INR */
		ROUND(
			COALESCE(MAX(ba.budget_amount), 0)
				/ IF(po.currency != 'INR' AND po.conversion_rate > 0, po.conversion_rate, 1),
			2
		) AS `Amount of Allocation`,
		ROUND(SUM(poi.amount), 2) AS `Expense Amount`,
		/* Company currency: Budget is held in company currency, PO base_amount is already converted */
		ROUND(COALESCE(MAX(ba.budget_amount), 0), 2) AS `Amount of Allocation (Company Currency)`,
		ROUND(SUM(poi.base_amount), 2) AS `Expense Amount (Company Currency)`,
		/* Balance is always in company currency (POs on one budget line can be in different
		   currencies); replaced by the running balance in _apply_reducing_balance */
		ROUND(COALESCE(MAX(ba.budget_amount), 0) - SUM(poi.base_amount), 2) AS `Balance Amount`
	FROM
		`tabPurchase Order Item` poi
	JOIN
		`tabPurchase Order` po ON poi.parent = po.name
	JOIN
		`tabCompany` comp ON comp.name = po.company
	LEFT JOIN (
		/* One row per budget line from SUBMITTED Budgets only, so a PO line is never
		   counted twice (cancelled/amended Budgets or several Budgets sharing one
		   Budget Allocation used to multiply the PO amounts) */
		SELECT b.budget_allocation, bacc.account, SUM(bacc.budget_amount) AS budget_amount
		FROM `tabBudget` b
		JOIN `tabBudget Account` bacc ON bacc.parent = b.name AND bacc.parenttype = 'Budget'
		WHERE b.docstatus = 1 AND IFNULL(b.budget_allocation, '') != ''
		GROUP BY b.budget_allocation, bacc.account
	) ba ON ba.budget_allocation = poi.budget_allocation AND ba.account = poi.expense_account
	WHERE
		po.docstatus = 1
		AND (%(from_date)s IS NULL OR po.transaction_date >= %(from_date)s)
		AND (%(to_date)s IS NULL OR po.transaction_date <= %(to_date)s)
		AND (%(project)s IS NULL OR COALESCE(NULLIF(poi.project, ''), po.project) LIKE %(project)s)
		AND (
			%(budget_allocation)s IS NULL
			OR poi.budget_allocation = %(budget_allocation)s
			OR poi.budget_allocation IN (
				SELECT budget_allocation FROM `tabBudget` WHERE name = %(budget_allocation_raw)s
			)
			OR poi.budget_allocation LIKE %(budget_allocation_like)s
		)
		AND (%(purchase_order)s IS NULL OR po.name = %(purchase_order)s)
		AND (%(currency)s IS NULL OR po.currency = %(currency)s)
	GROUP BY
		po.name,
		po.transaction_date,
		po.currency,
		po.conversion_rate,
		comp.default_currency,
		poi.department,
		poi.expense_account,
		poi.budget_allocation,
		COALESCE(NULLIF(poi.project, ''), po.project)
	ORDER BY
		po.transaction_date ASC,
		po.name ASC,
		poi.department ASC,
		COALESCE(NULLIF(poi.project, ''), po.project) ASC
"""


def execute(filters=None):
	return get_columns(), get_data(filters or {})


def get_data(filters):
	project = filters.get("project")
	budget_allocation = (filters.get("budget_allocation") or "").strip()
	clean_alloc = budget_allocation.split("/")[0].strip()

	params = {
		"from_date": filters.get("from_date") or None,
		"to_date": filters.get("to_date") or None,
		"project": f"%{project}%" if project else None,
		"budget_allocation": clean_alloc or None,
		"budget_allocation_raw": budget_allocation,
		"budget_allocation_like": f"%{clean_alloc}%",
		"purchase_order": filters.get("purchase_order") or None,
		"currency": filters.get("currency") or None,
	}
	rows = frappe.db.sql(QUERY, params, as_dict=True)
	_apply_reducing_balance(rows)
	return rows


def row_key(r):
	"""One row per PO / department / expense account / budget allocation / project (the GROUP BY).

	Also used by the MIS dashboard (mis_reports/builders.py), whose rows group the same way."""
	return (
		r["Purchase Orders"],
		r["Department"],
		r["Expense Account"],
		r["Budget Allocation"],
		r["Project Code"],
	)


def get_budget_left():
	"""{row_key: budget left in company currency (INR) after that row}.

	A budget line is Budget Allocation + Expense Account. POs are taken oldest first, and
	each row's figure is the allocation minus that PO and every earlier PO on the same line.
	POs on one line can be in different currencies, so only their company-currency amounts
	are added up. Always covers all submitted POs, whatever filters the caller applies, so a
	filtered view (one month, one PO, one currency) still shows the true remaining budget.
	Rows whose line has no allocation are left out.
	"""
	all_params = dict.fromkeys(
		(
			"from_date",
			"to_date",
			"project",
			"budget_allocation",
			"budget_allocation_raw",
			"budget_allocation_like",
			"purchase_order",
			"currency",
		)
	)
	all_rows = frappe.db.sql(QUERY, all_params, as_dict=True)
	all_rows.sort(
		key=lambda r: (
			r["Transaction Dates"],
			r["Purchase Orders"],
			r["Department"] or "",
			r["Project Code"] or "",
		)
	)

	spent = {}
	budget_left = {}
	for r in all_rows:
		alloc = flt(r["Amount of Allocation (Company Currency)"])
		if not alloc:
			continue
		line = (r["Budget Allocation"], r["Expense Account"])
		spent[line] = spent.get(line, 0) + flt(r["Expense Amount (Company Currency)"])
		budget_left[row_key(r)] = flt(alloc - spent[line], 2)
	return budget_left


def _apply_reducing_balance(rows):
	"""Balance Amount = budget left on the row's budget line after this PO (INR).

	Rows with no allocation keep allocation minus their own expense."""
	if not rows:
		return
	budget_left = get_budget_left()
	for r in rows:
		bal = budget_left.get(row_key(r))
		if bal is not None:
			r["Balance Amount"] = bal


def _currency(label, options, width):
	return {
		"label": _(label),
		"fieldname": label,
		"fieldtype": "Currency",
		"options": options,
		"width": width,
	}


def get_columns():
	return [
		{
			"label": _("Purchase Orders"),
			"fieldname": "Purchase Orders",
			"fieldtype": "Link",
			"options": "Purchase Order",
			"width": 180,
		},
		{
			"label": _("Transaction Dates"),
			"fieldname": "Transaction Dates",
			"fieldtype": "Date",
			"width": 120,
		},
		{
			"label": _("Budget Allocation"),
			"fieldname": "Budget Allocation",
			"fieldtype": "Data",
			"width": 150,
		},
		{"label": _("Department"), "fieldname": "Department", "fieldtype": "Data", "width": 140},
		{
			"label": _("Expense Account"),
			"fieldname": "Expense Account",
			"fieldtype": "Link",
			"options": "Account",
			"width": 200,
		},
		{
			"label": _("Project Code"),
			"fieldname": "Project Code",
			"fieldtype": "Link",
			"options": "Project",
			"width": 120,
		},
		{
			"label": _("Exchange Rate"),
			"fieldname": "Exchange Rate",
			"fieldtype": "Float",
			"precision": 4,
			"width": 110,
		},
		_currency("Amount of Allocation", "currency", 160),
		_currency("Amount of Allocation (Company Currency)", "company_currency", 180),
		_currency("Expense Amount", "currency", 160),
		_currency("Expense Amount (Company Currency)", "company_currency", 180),
		_currency("Balance Amount", "company_currency", 170),
	]
