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
    from_date = filters.get("from_date")
    to_date = filters.get("to_date")
    project = filters.get("project")
    budget_allocation = filters.get("budget_allocation")
    purchase_order = filters.get("purchase_order")

    conditions = ["po.docstatus = 1"]

    if from_date:
        conditions.append("po.transaction_date >= %(from_date)s")
    if to_date:
        conditions.append("po.transaction_date <= %(to_date)s")
    if project:
        conditions.append("po.project = %(project)s")
    if budget_allocation:
        conditions.append("poi.budget_allocation = %(budget_allocation)s")
    if purchase_order:
        conditions.append("po.name = %(purchase_order)s")

    where_clause = " AND ".join(conditions)

    query = """
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
            {where_clause}
        GROUP BY
            poi.department,
            poi.expense_account,
            poi.budget_allocation,
            po.project
        ORDER BY
            poi.department,
            poi.expense_account
    """.format(where_clause=where_clause)

    sql_filters = {}
    if from_date:
        sql_filters["from_date"] = from_date
    if to_date:
        sql_filters["to_date"] = to_date
    if project:
        sql_filters["project"] = project
    if budget_allocation:
        sql_filters["budget_allocation"] = budget_allocation
    if purchase_order:
        sql_filters["purchase_order"] = purchase_order

    data = frappe.db.sql(query, sql_filters, as_dict=True)
    return data


def get_columns():
    return [
        {"label": _("Purchase Orders"), "fieldname": "Purchase Orders", "fieldtype": "Data", "width": 200},
        {"label": _("Transaction Dates"), "fieldname": "Transaction Dates", "fieldtype": "Data", "width": 150},
        {"label": _("Budget Allocation"), "fieldname": "Budget Allocation", "fieldtype": "Data", "width": 150},
        {"label": _("Department"), "fieldname": "Department", "fieldtype": "Data", "width": 120},
        {"label": _("Expense Account"), "fieldname": "Expense Account", "fieldtype": "Link", "options": "Account", "width": 200},
        {"label": _("Project Code"), "fieldname": "Project Code", "fieldtype": "Link", "options": "Project", "width": 120},
        {"label": _("Amount of Allocation"), "fieldname": "Amount of Allocation", "fieldtype": "Currency", "width": 150},
        {"label": _("Expense Amount"), "fieldname": "Expense Amount", "fieldtype": "Currency", "width": 150},
    ]
