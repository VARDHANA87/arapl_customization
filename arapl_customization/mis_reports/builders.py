"""
Arapl MIS — Purchase Order Pivot dashboard.

Mirrors the Univision MIS pattern:
  * SQL-driven data (same semantics as the "Purchase Order Pivot Summary v3"
    Script Report — project is resolved from PO Item first, PO header second)
  * Jinja HTML dashboard rendered by a whitelisted endpoint
  * embedded in the /app/arapl-mis-dashboard desk page via iframe

The standard report itself is left untouched.

Endpoints:
  /api/method/arapl_customization.mis_reports.builders.view_po_pivot_summary

Deploy (page doc): see deploy.py / the bench-console snippet used at rollout.
"""

import frappe
from frappe import _
from frappe.utils import (
	add_months,
	flt,
	fmt_money,
	formatdate,
	getdate,
	now_datetime,
	nowdate,
)

from arapl_customization.arapl_customization.report.purchase_order_pivot_summary_v3.purchase_order_pivot_summary_v3 import (
	get_budget_left,
	row_key,
)

# Landing range when the dashboard is opened without any filter.
DEFAULT_RANGE_MONTHS = 1
DEFAULT_RANGE_LABEL = "Last 1 month"

# Dashboard endpoint (also used by the in-page filter links).
VIEW_URL = "/api/method/arapl_customization.mis_reports.builders.view_po_pivot_summary"


# Roles allowed to open the dashboard.
ALLOWED_ROLES = {
	"System Manager",
	"Purchase Manager",
	"Purchase User",
	"Accounts Manager",
	"Accounts User",
	"Manufacturing Manager",
	"Stock Manager",
}

# Project lives on the PO Item in this dataset; fall back to the PO header
# (SQL below uses COALESCE(NULLIF(poi.project, ''), po.project) inline).


# ---------------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------------
def _check_access():
	user = frappe.session.user or ""
	if user == "Administrator":
		return
	if not (set(frappe.get_roles(user)) & ALLOWED_ROLES):
		frappe.throw(
			_(
				"You don't have access to the MIS dashboard. "
				"Ask your administrator for the Purchase / Accounts role."
			),
			frappe.PermissionError,
		)


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
def get_pivot_rows(filters):
	"""Return the pivot rows (one per Purchase Order / department / expense account /
	budget allocation / project)."""
	project = filters.get("project")
	budget_allocation = (filters.get("budget_allocation") or "").strip()
	clean_alloc = budget_allocation.split("/")[0].strip()

	# Static query: an optional filter is skipped when its value is NULL.
	values = {
		"from_date": filters.get("from_date") or None,
		"to_date": filters.get("to_date") or None,
		"project": f"%{project}%" if project else None,
		"budget_allocation": clean_alloc or None,
		"budget_allocation_raw": budget_allocation,
		"budget_allocation_like": f"%{clean_alloc}%",
		"purchase_order": filters.get("purchase_order") or None,
		"currency": filters.get("currency") or None,
	}

	return frappe.db.sql(
		"""
        SELECT
            po.name AS `Purchase Orders`,
            po.transaction_date AS `Transaction Dates`,
            po.currency AS `Currency`,
            po.conversion_rate AS `Conversion Rate`,
            poi.budget_allocation AS `Budget Allocation`,
            poi.department AS `Department`,
            poi.expense_account AS `Expense Account`,
            COALESCE(NULLIF(poi.project, ''), po.project) AS `Project Code`,
            /* Convert Allocation to PO Currency if not INR */
            ROUND(
                COALESCE(MAX(ba.budget_amount), 0) / IF(po.currency != 'INR' AND po.conversion_rate > 0, po.conversion_rate, 1),
                2
            ) AS `Amount of Allocation`,
            ROUND(SUM(poi.amount), 2) AS `Expense Amount`,
            /* Company currency (INR): Budget is held in it, PO base_amount is converted with the PO's rate */
            ROUND(COALESCE(MAX(ba.budget_amount), 0), 2) AS `Amount of Allocation (INR)`,
            ROUND(SUM(poi.base_amount), 2) AS `Expense Amount (INR)`,
            ROUND(
                (COALESCE(MAX(ba.budget_amount), 0) / IF(po.currency != 'INR' AND po.conversion_rate > 0, po.conversion_rate, 1)) - SUM(poi.amount),
                2
            ) AS `Balance Amount`
        FROM
            `tabPurchase Order Item` poi
        JOIN
            `tabPurchase Order` po ON poi.parent = po.name
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
            poi.department,
            poi.expense_account,
            poi.budget_allocation,
            COALESCE(NULLIF(poi.project, ''), po.project)
        ORDER BY
            po.transaction_date ASC,
            po.name ASC
        """,
		values,
		as_dict=True,
	)


def _project_options():
	rows = frappe.db.sql(
		"""
        SELECT DISTINCT COALESCE(NULLIF(poi.project, ''), po.project) AS p
        FROM `tabPurchase Order Item` poi
        JOIN `tabPurchase Order` po ON po.name = poi.parent
        WHERE po.docstatus = 1
          AND IFNULL(COALESCE(NULLIF(poi.project, ''), po.project), '') != ''
        ORDER BY p
        """
	)
	return [r[0] for r in rows]


def _group(rows, field, limit=10, value_field="Expense Amount"):
	agg = {}
	for r in rows:
		key = (r.get(field) or "").strip() or "(blank)"
		agg[key] = agg.get(key, 0.0) + flt(r.get(value_field))
	items = sorted(agg.items(), key=lambda kv: kv[1], reverse=True)[:limit]
	total = sum(agg.values()) or 1.0
	maxv = max([v for _, v in items], default=0) or 1.0
	out = []
	for label, value in items:
		out.append(
			{
				"label": label,
				"value": value,
				"value_fmt": fmt_money(value, precision=2),
				"pct": round(value * 100.0 / total, 1),
				"width": max(round(value * 100.0 / maxv, 1), 1.0),
			}
		)
	return out


def _fmt_money(v, currency=None):
	return fmt_money(flt(v), precision=2, currency=currency)


def _default_range():
	"""Rolling window used when the dashboard is opened with no filters."""
	end = getdate(nowdate())
	start = getdate(add_months(end, -DEFAULT_RANGE_MONTHS))
	return str(start), str(end)


def _endpoint_url(**params):
	from urllib.parse import urlencode

	query = urlencode({k: v for k, v in params.items() if v})
	return VIEW_URL + (("?" + query) if query else "")


def _range_label(from_date=None, to_date=None):
	def d(value):
		try:
			return formatdate(value, "dd MMM yyyy")
		except Exception:
			return str(value)

	if from_date and to_date:
		return f"{d(from_date)} → {d(to_date)}"
	if from_date:
		return f"from {d(from_date)}"
	if to_date:
		return f"up to {d(to_date)}"
	return "all dates"


def _ctx_po_pivot_summary(
	from_date=None,
	to_date=None,
	project=None,
	budget_allocation=None,
	purchase_order=None,
	currency=None,
	is_default_range=False,
):
	filters = {
		"from_date": from_date or None,
		"to_date": to_date or None,
		"project": project or None,
		"budget_allocation": budget_allocation or None,
		"purchase_order": purchase_order or None,
		"currency": currency or None,
	}

	rows = get_pivot_rows(filters)

	# Budget left after each PO, in company currency (same figure as Balance Amount in the
	# Purchase Order Pivot Summary v3 report).
	budget_left = get_budget_left()
	company_currency = (
		frappe.get_cached_value("Company", frappe.defaults.get_user_default("Company"), "default_currency")
		or "INR"
	)

	# Cards, bars and footer are in company currency (INR): POs can be in INR, USD or GBP,
	# and only their INR amounts can be added up. A budget line's allocation is repeated on
	# every PO row of that line, so it is counted once.
	total_alloc = 0.0
	total_expense = 0.0
	alloc_seen = set()
	currency_count = {}

	for r in rows:
		row_curr = (r.get("Currency") or company_currency).upper()
		currency_count[row_curr] = currency_count.get(row_curr, 0) + 1

		r["Allocation Fmt"] = _fmt_money(r.get("Amount of Allocation"), row_curr)
		r["Expense Fmt"] = _fmt_money(r.get("Expense Amount"), row_curr)
		r["Balance Fmt"] = _fmt_money(r.get("Balance Amount"), row_curr)

		left = budget_left.get(row_key(r))
		r["Budget Left"] = left
		r["Budget Left Fmt"] = _fmt_money(left, company_currency) if left is not None else ""

		line = (r.get("Budget Allocation"), r.get("Expense Account"))
		if line not in alloc_seen:
			alloc_seen.add(line)
			total_alloc += flt(r.get("Amount of Allocation (INR)"))
		total_expense += flt(r.get("Expense Amount (INR)"))

	total_balance = total_alloc - total_expense
	primary_curr = company_currency

	po_set = {r.get("Purchase Orders") for r in rows if r.get("Purchase Orders")}
	project_set = {r.get("Project Code") for r in rows if r.get("Project Code")}
	dept_set = {r.get("Department") for r in rows if r.get("Department")}

	utilisation = None
	if total_alloc:
		utilisation = round(total_expense * 100.0 / total_alloc, 1)

	variance = total_expense - total_alloc

	return {
		"rows": rows,
		"row_count": len(rows),
		"po_count": len(po_set),
		"project_count": len(project_set),
		"department_count": len(dept_set),
		"currency_mix": " · ".join(f"{c} {n}" for c, n in sorted(currency_count.items())),
		"selected_currency": currency or "",
		"primary_currency": primary_curr,
		"total_expense": total_expense,
		"total_alloc": total_alloc,
		"total_balance": total_balance,
		"total_expense_fmt": _fmt_money(total_expense, primary_curr),
		"total_alloc_fmt": _fmt_money(total_alloc, primary_curr),
		"total_balance_fmt": _fmt_money(total_balance, primary_curr),
		"company_currency": company_currency,
		"variance": variance,
		"variance_fmt": _fmt_money(variance, primary_curr),
		"variance_over": variance > 0,
		"utilisation": utilisation,
		"utilisation_fmt": (f"{utilisation:,.1f}%" if utilisation is not None else "—"),
		"by_project": _group(rows, "Project Code", limit=10, value_field="Expense Amount (INR)"),
		"by_department": _group(rows, "Department", limit=12, value_field="Expense Amount (INR)"),
		"by_account": _group(rows, "Expense Account", limit=8, value_field="Expense Amount (INR)"),
		"projects": _project_options(),
		"filter_from_date": from_date or "",
		"filter_to_date": to_date or "",
		"filter_project": project or "",
		"filter_budget_allocation": budget_allocation or "",
		"filter_purchase_order": purchase_order or "",
		"filter_currency": currency or "",
		"range_label": (
			f"{DEFAULT_RANGE_LABEL.lower()} · {_range_label(from_date, to_date)}"
			if is_default_range
			else _range_label(from_date, to_date)
		),
		"is_default_range": is_default_range,
		"default_range_label": DEFAULT_RANGE_LABEL,
		"base_url": VIEW_URL,
		"all_dates_url": _endpoint_url(
			all_dates=1,
			project=project,
			purchase_order=purchase_order,
			budget_allocation=budget_allocation,
			currency=currency,
		),
		"last_month_url": _endpoint_url(
			from_date=_default_range()[0],
			to_date=_default_range()[1],
			project=project,
			purchase_order=purchase_order,
			budget_allocation=budget_allocation,
			currency=currency,
		),
		"generated_at": now_datetime().strftime("%d %b %Y, %H:%M"),
		"report_url": "/app/query-report/Purchase%20Order%20Pivot%20Summary%20v3",
	}


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------
def _html_response(html_body):
	from werkzeug.wrappers import Response

	return Response(html_body, mimetype="text/html; charset=utf-8")


# Loaded through Frappe's Jinja loader (app-relative path), not open().
PO_PIVOT_TEMPLATE = "arapl_customization/mis_reports/templates/po_pivot_summary.html"


@frappe.whitelist(allow_guest=False)
def view_po_pivot_summary(
	from_date: str | None = None,
	to_date: str | None = None,
	project: str | None = None,
	budget_allocation: str | None = None,
	purchase_order: str | None = None,
	currency: str | None = None,
	all_dates: str | int | None = None,
):
	_check_access()

	from_date = from_date or None
	to_date = to_date or None
	project = project or None
	budget_allocation = budget_allocation or None
	purchase_order = purchase_order or None
	currency = currency or None

	bare_landing = not any((from_date, to_date, project, budget_allocation, purchase_order, currency))
	is_default_range = bool(bare_landing and not all_dates)
	if is_default_range:
		from_date, to_date = _default_range()

	ctx = _ctx_po_pivot_summary(
		from_date=from_date,
		to_date=to_date,
		project=project,
		budget_allocation=budget_allocation,
		purchase_order=purchase_order,
		currency=currency,
		is_default_range=is_default_range,
	)
	ctx["is_browser_view"] = True
	ctx["all_dates_flag"] = bool(all_dates)
	ctx["default_from"], ctx["default_to"] = _default_range()
	html = frappe.get_template(PO_PIVOT_TEMPLATE).render(ctx)
	return _html_response(html)
