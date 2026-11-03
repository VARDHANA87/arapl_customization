# Copyright (c) 2024, and contributors
# For license information, please see license.txt

import frappe
from frappe import _


def execute(filters=None):
	if not filters:
		filters = {}

	columns = get_columns()
	data = get_data(filters)

	return columns, data


def get_data(filters):
	# Static query: each optional filter is skipped when its value is NULL, so no SQL is
	# built from strings (keeps semgrep's frappe-sql-format-injection rule happy).
	sql_filters = {
		"from_date": filters.get("from_date") or None,
		"to_date": filters.get("to_date") or None,
		"project": filters.get("project") or None,
		"budget_allocation": filters.get("budget_allocation") or None,
		"purchase_order": filters.get("purchase_order") or None,
	}

	return frappe.db.sql(
		"""
		SELECT
			GROUP_CONCAT(DISTINCT po.name ORDER BY po.name ASC SEPARATOR ', ') AS `Purchase Orders`,
			GROUP_CONCAT(DISTINCT po.transaction_date ORDER BY po.transaction_date ASC SEPARATOR ', ') AS `Transaction Dates`,
			poi.budget_allocation AS `Budget Allocation`,
			poi.department AS `Department`,
			poi.expense_account AS `Expense Account`,
			po.project AS `Project Code`,
			SUM(ba.budget_amount) AS `Amount of Allocation`,
			SUM(poi.amount) AS `Expense Amount`
		FROM
			`tabPurchase Order Item` poi
		JOIN
			`tabPurchase Order` po ON poi.parent = po.name
		LEFT JOIN
			`tabBudget` b ON poi.budget_allocation = b.budget_allocation
		LEFT JOIN
			`tabBudget Account` ba ON ba.parent = b.name
		WHERE
			po.docstatus = 1
			AND (%(from_date)s IS NULL OR po.transaction_date >= %(from_date)s)
			AND (%(to_date)s IS NULL OR po.transaction_date <= %(to_date)s)
			AND (%(project)s IS NULL OR po.project = %(project)s)
			AND (%(budget_allocation)s IS NULL OR poi.budget_allocation = %(budget_allocation)s)
			AND (%(purchase_order)s IS NULL OR po.name = %(purchase_order)s)
		GROUP BY
			poi.department,
			poi.expense_account,
			poi.budget_allocation,
			po.project
		ORDER BY
			poi.department,
			poi.expense_account
		""",
		sql_filters,
		as_dict=True,
	)


def get_columns():
	return [
		{"label": _("Purchase Orders"), "fieldname": "Purchase Orders", "fieldtype": "Data", "width": 200},
		{
			"label": _("Transaction Dates"),
			"fieldname": "Transaction Dates",
			"fieldtype": "Data",
			"width": 150,
		},
		{
			"label": _("Budget Allocation"),
			"fieldname": "Budget Allocation",
			"fieldtype": "Data",
			"width": 150,
		},
		{"label": _("Department"), "fieldname": "Department", "fieldtype": "Data", "width": 120},
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
			"label": _("Amount of Allocation"),
			"fieldname": "Amount of Allocation",
			"fieldtype": "Currency",
			"width": 150,
		},
		{"label": _("Expense Amount"), "fieldname": "Expense Amount", "fieldtype": "Currency", "width": 150},
	]
