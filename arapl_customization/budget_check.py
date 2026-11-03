"""Hard budget cap for Purchase Orders and Material Requests.

ERPNext's own budget check (erpnext.accounts.doctype.budget.budget) only adds
up open POs for the *same item code*, and uses the PO amount in the PO's own
currency. So a budget can be over-committed without ever being stopped.

Runs only when Accounts Settings > "Enable PO Budget Validation"
(custom_enable_po_budget_validation) is ticked.

This adds an extra check (it does not replace ERPNext's): on PO submit and on
"Update Items" of a submitted PO, the sum of every PO booked against the same
Budget must not go over 100% of that Budget's amount, in company currency.

What a PO line is checked against
---------------------------------
A submitted Budget (docstatus 1) where all of these match the PO / PO Item:
    Budget.company                    = Purchase Order.company
    Budget.fiscal_year                = fiscal year of Purchase Order.transaction_date
    Budget.applicable_on_purchase_order = 1
    Budget.<budget_against field>     = Purchase Order Item.<same field>
        (budget_against "Cost Center" -> cost_center, "Project" -> project,
         an Accounting Dimension such as "Budget Allocation" -> budget_allocation)
    Budget Account.account            = Purchase Order Item.expense_account

What counts as already used (all in company currency, same company /
fiscal year / expense account / dimension value)
------------------------------------------------
1. Other submitted POs, not Closed:    Purchase Order Item.base_net_amount
2. Other submitted POs that are Closed: only what was actually received or
   billed, whichever is higher:
       received_qty * base_net_rate  vs  billed_amt * Purchase Order.conversion_rate
3. Spend that did not come through any PO, so it is not counted twice:
   - GL Entry on the account from vouchers other than Purchase Receipt /
     Purchase Invoice (e.g. Journal Entries), debit - credit
   - Purchase Receipt Item with no purchase_order: base_net_amount
   - Purchase Invoice Item with no purchase_order and no purchase_receipt:
     base_net_amount

This PO: sum of its lines' base_net_amount for that budget.

used + this PO > Budget Account.budget_amount  -> submit is blocked.

Material Request (type Purchase only)
-------------------------------------
Same matching, but against Budgets with applicable_on_material_request = 1,
using Material Request.transaction_date for the fiscal year. MR amounts have
no currency of their own: Material Request Item rate is company currency.
MR Item.amount is only calculated in the browser (it can be stale when an MR is
made by API / import / copy), so qty * rate is used instead.

    used = everything above (submitted POs + non-PO spend)
         + other submitted Purchase MRs not Stopped/Cancelled, only the part
           not yet ordered: qty * rate * (stock_qty - ordered_qty) / stock_qty
    this MR = sum of its lines' qty * rate

The PO check does not add pending MRs: a PO made from an MR would otherwise
count that MR twice (its ordered_qty is updated only after the PO submits).
"""

from collections import defaultdict

import frappe
from erpnext.accounts.utils import get_fiscal_year
from frappe import _
from frappe.utils import flt, fmt_money

PO_STATUS_CLOSED = "Closed"
MR_STATUS_EXCLUDED = ("Stopped", "Cancelled")
ENABLE_FIELD = "custom_enable_po_budget_validation"

# doctype -> (Budget checkbox, amount field on the item row, label)
DOC_CONFIG = {
	"Purchase Order": ("applicable_on_purchase_order", "base_net_amount", "Purchase Order"),
	# MR Item.amount is only computed in the browser, so qty * rate is used for MRs
	"Material Request": ("applicable_on_material_request", None, "Material Request"),
}


def validate_po_budget(doc, method=None):
	"""Hooked on Purchase Order before_submit and before_update_after_submit."""
	if doc.doctype == "Purchase Order":
		_validate_budget(doc, method)


def validate_mr_budget(doc, method=None):
	"""Hooked on Material Request before_submit and before_update_after_submit."""
	if doc.doctype == "Material Request" and doc.material_request_type == "Purchase":
		_validate_budget(doc, method)


def is_budget_validation_enabled():
	"""Accounts Settings > Enable PO Budget Validation (custom field, in this app's fixtures)."""
	if not frappe.get_meta("Accounts Settings").has_field(ENABLE_FIELD):
		return False
	return bool(frappe.db.get_single_value("Accounts Settings", ENABLE_FIELD))


def validate_mr_rate(doc, method=None):
	"""Hooked on Material Request before_validate.

	Moved from the "Material Request Script" Server Script so that script can be
	disabled: every row's rate must be at least 1 (always on, not tied to the toggle).
	"""
	for item in doc.items:
		if item.rate is not None and flt(item.rate) < 1:
			frappe.throw(
				_(
					"<b>Invalid Rate</b><br><br>Rate must be <b>at least 1</b> for all items in "
					"Material Request.<br><br><b>Item Code:</b> {0}<br><b>Row:</b> {1}<br>"
					"<b>Entered Rate:</b> {2}"
				).format(item.item_code, item.idx, item.rate)
			)


def _validate_budget(doc, method):
	if not is_budget_validation_enabled():
		return

	applicable_field, amount_field, _label = DOC_CONFIG[doc.doctype]
	accounts = {d.expense_account for d in doc.items if d.expense_account}
	if not accounts:
		return
	fiscal_year = get_fiscal_year(doc.transaction_date, company=doc.company, as_dict=True)
	budgets = _get_budgets(doc.company, fiscal_year.name, applicable_field, accounts)
	if not budgets:
		return

	keys = {}
	this_po = _amount_per_budget(doc, budgets, keys, amount_field)

	# On "Update Items" only stop changes that increase this PO's amount for a
	# budget, so a PO can always be reduced even if the budget is already over.
	is_update = method == "before_update_after_submit"
	before = {}
	if is_update:
		prev = doc.get_doc_before_save()
		before = _amount_per_budget(prev, budgets, {}, amount_field) if prev else {}

	to_check = {k: v for k, v in this_po.items() if not (is_update and v <= before.get(k, 0) + 0.005)}
	if not to_check:
		return

	# Concurrency: lock the Budget Account rows (sorted, so two documents never
	# wait on each other in opposite order). A second submit against the same
	# budget waits here until the first one commits.
	latest_amount = _lock_budgets(sorted({k[0] for k in to_check}))

	# The site DB runs REPEATABLE READ, so this transaction's own snapshot may be
	# older than the lock and miss a PO/MR the other submit just committed. Read
	# the used amounts on a fresh connection, which sees everything committed.
	db = _fresh_db()
	try:
		errors = []
		for key, po_amount in to_check.items():
			b = keys[key]
			used = _get_used_amount(doc, b, fiscal_year, db)
			total = used + po_amount
			budget_amount = flt(latest_amount.get(key, b.budget_amount))
			if total > budget_amount + 0.005:
				errors.append((b, budget_amount, used, po_amount, total))
	finally:
		if db is not frappe.db:
			db.close()

	if errors:
		frappe.throw(_build_message(doc, errors, is_update), title=_("Budget Exceeded"))


def _amount_per_budget(doc, budgets, keys, amount_field):
	"""(budget name, account) -> sum of this document's line amounts under that budget."""
	out = defaultdict(float)
	for item in doc.items:
		if not item.expense_account:
			continue
		for b in budgets:
			field = b.budget_against_field
			if b.account == item.expense_account and item.get(field) and item.get(field) == b.against_value:
				key = (b.name, b.account)
				if amount_field:
					out[key] += flt(item.get(amount_field))
				else:
					out[key] += flt(item.qty) * flt(item.rate)
				keys[key] = b
	return out


def _budget_against_fields():
	"""Fields a Budget can be set against: cost_center, project + accounting dimensions."""
	from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import (
		get_accounting_dimensions,
	)

	fields = ["cost_center", "project", *get_accounting_dimensions()]
	budget_meta = frappe.get_meta("Budget")
	return [f for f in dict.fromkeys(fields) if budget_meta.has_field(f)]


def _get_budgets(company, fiscal_year, applicable_field, accounts):
	"""One query: submitted budgets of this company / year for this document's accounts."""
	fields = _budget_against_fields()
	cols = ", ".join(f"b.`{f}`" for f in fields)
	rows = frappe.db.sql(
		f"""
		select b.name, b.budget_against, ba.account, ba.budget_amount, {cols}
		from `tabBudget` b
		join `tabBudget Account` ba on ba.parent = b.name and ba.parenttype = 'Budget'
		where b.docstatus = 1 and b.company = %(company)s and b.fiscal_year = %(fy)s
			and b.`{applicable_field}` = 1 and ba.budget_amount > 0
			and ba.account in %(accounts)s
		""",
		{"company": company, "fy": fiscal_year, "accounts": tuple(accounts)},
		as_dict=True,
	)
	out = []
	for r in rows:
		r.budget_against_field = frappe.scrub(r.budget_against)
		r.against_value = r.get(r.budget_against_field)
		if r.against_value:
			out.append(r)
	return out


def _lock_budgets(budget_names):
	"""SELECT ... FOR UPDATE the Budget Account rows; returns latest {(budget, account): amount}."""
	rows = frappe.db.sql(
		"""
		select parent, account, budget_amount from `tabBudget Account`
		where parenttype = 'Budget' and parent in %(names)s
		order by parent, account
		for update
		""",
		{"names": tuple(budget_names)},
	)
	return {(r[0], r[1]): flt(r[2]) for r in rows}


def _fresh_db():
	"""A new connection to this site's own database (never another site's).

	Used so sums see rows committed after this transaction's snapshot. In tests,
	where test data is never committed, the current connection is used instead.
	"""
	if frappe.flags.in_test:
		return frappe.db
	from frappe.database import get_db

	conf = frappe.conf
	return get_db(
		host=conf.db_host,
		port=conf.db_port,
		user=conf.db_user or conf.db_name,
		password=conf.db_password,
		cur_db_name=conf.db_name,
		socket=conf.db_socket,
	)


def _get_used_amount(doc, b, fiscal_year, db=None):
	db = db or frappe.db
	field = b.budget_against_field
	params = {
		"company": doc.company,
		"account": b.account,
		"value": b.against_value,
		"from": fiscal_year.year_start_date,
		"to": fiscal_year.year_end_date,
		"this_doc": doc.name,
		"closed": PO_STATUS_CLOSED,
		"mr_excluded": MR_STATUS_EXCLUDED,
	}

	po = db.sql(
		f"""
		select coalesce(sum(case when p.status != %(closed)s then c.base_net_amount
			else greatest(c.received_qty * c.base_net_rate, c.billed_amt * p.conversion_rate) end), 0)
		from `tabPurchase Order Item` c
		join `tabPurchase Order` p on p.name = c.parent
		where p.docstatus = 1 and p.company = %(company)s and p.name != %(this_doc)s
			and p.transaction_date between %(from)s and %(to)s
			and c.expense_account = %(account)s and c.`{field}` = %(value)s
		""",
		params,
	)[0][0]

	gl_other = db.sql(
		f"""
		select coalesce(sum(debit - credit), 0) from `tabGL Entry`
		where is_cancelled = 0 and company = %(company)s and account = %(account)s
			and `{field}` = %(value)s and posting_date between %(from)s and %(to)s
			and voucher_type not in ('Purchase Receipt', 'Purchase Invoice')
		""",
		params,
	)[0][0]

	pr_no_po = db.sql(
		f"""
		select coalesce(sum(c.base_net_amount), 0)
		from `tabPurchase Receipt Item` c join `tabPurchase Receipt` p on p.name = c.parent
		where p.docstatus = 1 and p.company = %(company)s
			and p.posting_date between %(from)s and %(to)s
			and c.expense_account = %(account)s and c.`{field}` = %(value)s
			and ifnull(c.purchase_order, '') = ''
		""",
		params,
	)[0][0]

	pi_no_po = db.sql(
		f"""
		select coalesce(sum(c.base_net_amount), 0)
		from `tabPurchase Invoice Item` c join `tabPurchase Invoice` p on p.name = c.parent
		where p.docstatus = 1 and p.company = %(company)s
			and p.posting_date between %(from)s and %(to)s
			and c.expense_account = %(account)s and c.`{field}` = %(value)s
			and ifnull(c.purchase_order, '') = '' and ifnull(c.purchase_receipt, '') = ''
		""",
		params,
	)[0][0]

	mr_pending = 0
	if doc.doctype == "Material Request":
		mr_pending = db.sql(
			f"""
			select coalesce(sum(c.qty * c.rate * greatest(c.stock_qty - c.ordered_qty, 0) / c.stock_qty), 0)
			from `tabMaterial Request Item` c join `tabMaterial Request` p on p.name = c.parent
			where p.docstatus = 1 and p.company = %(company)s and p.name != %(this_doc)s
				and p.material_request_type = 'Purchase' and p.status not in %(mr_excluded)s
				and p.transaction_date between %(from)s and %(to)s
				and c.stock_qty > 0
				and c.expense_account = %(account)s and c.`{field}` = %(value)s
			""",
			params,
		)[0][0]

	return flt(po) + flt(gl_other) + flt(pr_no_po) + flt(pi_no_po) + flt(mr_pending)


def _build_message(doc, errors, is_update=False):
	currency = frappe.get_cached_value("Company", doc.company, "default_currency")

	def m(v):
		return fmt_money(v, currency=currency)

	action = _("Update Items") if is_update else _("Submit")
	label = _(doc.doctype)
	parts = [_("This {0} would take the budget over 100%. {1} is blocked.").format(label, action)]
	booked_label = (
		_("Already booked (POs + pending MRs + non-PO spend)")
		if doc.doctype == "Material Request"
		else _("Already booked (other POs + non-PO spend)")
	)
	for b, budget_amount, used, po_amount, total in errors:
		pct = (total / budget_amount * 100) if budget_amount else 0
		available = max(budget_amount - used, 0)
		parts.append(
			"<br><br><b>{}</b> &mdash; {}: <b>{}</b>, {}: <b>{}</b>"
			"<table class='table table-bordered' style='margin-top:6px'>"
			"<tr><td>{}</td><td class='text-right'>{}</td></tr>"
			"<tr><td>{}</td><td class='text-right'>{}</td></tr>"
			"<tr><td>{}</td><td class='text-right'>{}</td></tr>"
			"<tr><td>{}</td><td class='text-right'>{}</td></tr>"
			"<tr><td><b>{}</b></td><td class='text-right'><b>{} ({:.1f}%)</b></td></tr>"
			"<tr><td><b>{}</b></td><td class='text-right'><b>{}</b></td></tr>"
			"</table>".format(
				frappe.utils.get_link_to_form("Budget", b.name),
				_(b.budget_against),
				frappe.bold(b.against_value),
				_("Account"),
				frappe.bold(b.account),
				_("Budget"),
				m(budget_amount),
				booked_label,
				m(used),
				_("Available"),
				m(available),
				_("This {0}").format(label),
				m(po_amount),
				_("Total after this {0}").format(label),
				m(total),
				pct,
				_("Over by"),
				m(total - budget_amount),
			)
		)
	return "".join(parts)
