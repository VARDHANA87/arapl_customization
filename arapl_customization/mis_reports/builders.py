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
            ROUND(
                (COALESCE(MAX(ba.budget_amount), 0) / IF(po.currency != 'INR' AND po.conversion_rate > 0, po.conversion_rate, 1)) - SUM(poi.amount),
                2
            ) AS `Balance Amount`
        FROM
            `tabPurchase Order Item` poi
        JOIN
            `tabPurchase Order` po ON poi.parent = po.name
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
            poi.department,
            poi.expense_account,
            poi.budget_allocation,
            COALESCE(NULLIF(poi.project, ''), po.project)
        ORDER BY
            po.transaction_date DESC,
            po.name DESC
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

	curr_totals = {
		"INR": {"alloc": 0.0, "expense": 0.0, "balance": 0.0, "count": 0},
		"USD": {"alloc": 0.0, "expense": 0.0, "balance": 0.0, "count": 0},
	}

	for r in rows:
		row_curr = (r.get("Currency") or "INR").upper()
		if row_curr not in curr_totals:
			curr_totals[row_curr] = {"alloc": 0.0, "expense": 0.0, "balance": 0.0, "count": 0}

		alloc = flt(r.get("Amount of Allocation"))
		exp = flt(r.get("Expense Amount"))
		bal = flt(r.get("Balance Amount"))

		r["Allocation Fmt"] = _fmt_money(alloc, row_curr)
		r["Expense Fmt"] = _fmt_money(exp, row_curr)
		r["Balance Fmt"] = _fmt_money(bal, row_curr)

		curr_totals[row_curr]["alloc"] += alloc
		curr_totals[row_curr]["expense"] += exp
		curr_totals[row_curr]["balance"] += bal
		curr_totals[row_curr]["count"] += 1

	po_set = {r.get("Purchase Orders") for r in rows if r.get("Purchase Orders")}
	project_set = {r.get("Project Code") for r in rows if r.get("Project Code")}
	dept_set = {r.get("Department") for r in rows if r.get("Department")}

	primary_curr = currency or (
		"USD" if curr_totals["USD"]["count"] and not curr_totals["INR"]["count"] else "INR"
	)
	active_summary = curr_totals.get(primary_curr, {"alloc": 0.0, "expense": 0.0, "balance": 0.0})

	total_expense = active_summary["expense"]
	total_alloc = active_summary["alloc"]
	total_balance = active_summary["balance"]

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
		"curr_totals": curr_totals,
		"selected_currency": currency or "",
		"primary_currency": primary_curr,
		"total_expense": total_expense,
		"total_alloc": total_alloc,
		"total_balance": total_balance,
		"total_expense_fmt": _fmt_money(total_expense, primary_curr),
		"total_alloc_fmt": _fmt_money(total_alloc, primary_curr),
		"total_balance_fmt": _fmt_money(total_balance, primary_curr),
		"inr_balance_fmt": _fmt_money(curr_totals["INR"]["balance"], "INR"),
		"usd_balance_fmt": _fmt_money(curr_totals["USD"]["balance"], "USD"),
		"inr_count": curr_totals["INR"]["count"],
		"usd_count": curr_totals["USD"]["count"],
		"variance": variance,
		"variance_fmt": _fmt_money(variance, primary_curr),
		"variance_over": variance > 0,
		"utilisation": utilisation,
		"utilisation_fmt": (f"{utilisation:,.1f}%" if utilisation is not None else "—"),
		"by_project": _group(rows, "Project Code", limit=10),
		"by_department": _group(rows, "Department", limit=12),
		"by_account": _group(rows, "Expense Account", limit=8),
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
