// Copyright (c) 2024, and contributors
// For license information, please see license.txt

frappe.query_reports["Purchase Order Pivot Summary"] = {
    "filters": [
        {
            "fieldname": "from_date",
            "label": __("From Date"),
            "fieldtype": "Date",
            "default": frappe.datetime.add_months(frappe.datetime.get_today(), -12)
        },
        {
            "fieldname": "to_date",
            "label": __("To Date"),
            "fieldtype": "Date",
            "default": frappe.datetime.get_today()
        },
        {
            "fieldname": "project",
            "label": __("Project"),
            "fieldtype": "Link",
            "options": "Project"
        },
        {
            "fieldname": "budget_allocation",
            "label": __("Budget Allocation"),
            "fieldtype": "Link",
            "options": "Budget"
        },
        {
            "fieldname": "purchase_order",
            "label": __("Purchase Order"),
            "fieldtype": "Link",
            "options": "Purchase Order"
        }
    ]
};
