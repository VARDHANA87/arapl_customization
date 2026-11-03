# Copyright (c) 2026, and contributors
# For license information, please see license.txt

import frappe
from frappe import _

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
		ROUND(
			(COALESCE(MAX(ba.budget_amount), 0)
				/ IF(po.currency != 'INR' AND po.conversion_rate > 0, po.conversion_rate, 1))
				- SUM(poi.amount),
			2
		) AS `Balance Amount`,
		/* Company currency: Budget is held in company currency, PO base_amount is already converted */
		ROUND(COALESCE(MAX(ba.budget_amount), 0), 2) AS `Amount of Allocation (Company Currency)`,
		ROUND(SUM(poi.base_amount), 2) AS `Expense Amount (Company Currency)`,
		ROUND(COALESCE(MAX(ba.budget_amount), 0) - SUM(poi.base_amount), 2) AS `Balance Amount (Company Currency)`
	FROM
		`tabPurchase Order Item` poi
	JOIN
		`tabPurchase Order` po ON poi.parent = po.name
	JOIN
		`tabCompany` comp ON comp.name = po.company
	LEFT JOIN
		`tabBudget` b ON poi.budget_allocation = b.budget_allocation
	LEFT JOIN
		`tabBudget Account` ba ON ba.parent = b.name AND ba.account = poi.expense_account
	WHERE
		po.docstatus = 1
		AND (%(from_date)s IS NULL OR po.transaction_date >= %(from_date)s)
		AND (%(to_date)s IS NULL OR po.transaction_date <= %(to_date)s)
		AND (%(project)s IS NULL OR COALESCE(NULLIF(poi.project, ''), po.project) LIKE %(project)s)
		AND (
			%(budget_allocation)s IS NULL
			OR poi.budget_allocation = %(budget_allocation)s
			OR b.name = %(budget_allocation_raw)s
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
		po.transaction_date DESC,
		po.name DESC
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
	return frappe.db.sql(QUERY, params, as_dict=True)


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
		_currency("Balance Amount", "currency", 160),
		_currency("Balance Amount (Company Currency)", "company_currency", 180),
	]
