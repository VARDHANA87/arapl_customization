"""Tests for arapl_customization.budget_check (PO / MR 100% budget cap).

Run (dev / test site only, every test is rolled back):
    bench --site <site> set-config allow_tests true
    bench --site <site> run-tests --module arapl_customization.tests.test_budget_check --skip-before-tests

The tests build their own Budget Allocation + Budget (₹1,000) and copy an existing
submitted Purchase Order / Material Request of the company as a template, so the
site needs at least one INR PO, one USD PO and one Purchase MR in COMPANY.
"""

import json
from unittest.mock import patch

import frappe
from frappe.model.document import Document
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, flt, getdate

from arapl_customization import budget_check

COMPANY = "ARAPL RAAS PRIVATE LIMITED"
ACCT = "Purchase Maia V3 - RAAS"
BA = "ZZUT-BA-1"
BA_OTHER = "ZZUT-BA-2"
FY = "26-27"
TXN_DATE = "2026-11-02"
SCHED_DATE = "2026-11-30"
BUDGET = 1000.0

LINK_FIELDS = (
	"material_request",
	"material_request_item",
	"sales_order",
	"sales_order_item",
	"supplier_quotation",
	"supplier_quotation_item",
	"blanket_order",
	"blanket_order_rate",
	"production_plan",
	"production_plan_item",
	"work_order",
)


def _clean_lines(doc, rate, qty, ba, account):
	for it in doc.items:
		it.qty = qty
		it.rate = rate
		it.expense_account = account
		it.budget_allocation = ba
		it.schedule_date = SCHED_DATE
		for f in ("price_list_rate", "discount_percentage", "discount_amount"):
			if it.meta.has_field(f):
				it.set(f, 0)
		if it.meta.has_field("margin_type"):
			it.margin_type = None
		for f in LINK_FIELDS:
			if it.meta.has_field(f):
				it.set(f, None)


class TestBudgetCheck(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.inr_po = frappe.db.get_value(
			"Purchase Order", {"company": COMPANY, "docstatus": 1, "currency": "INR"}, "name"
		)
		cls.usd_po = frappe.db.get_value(
			"Purchase Order", {"company": COMPANY, "docstatus": 1, "currency": "USD"}, "name"
		)
		cls.purchase_mr = frappe.db.get_value(
			"Material Request",
			{"company": COMPANY, "docstatus": 1, "material_request_type": "Purchase"},
			"name",
		)
		cls.bank_account = frappe.db.get_value(
			"Account", {"company": COMPANY, "account_type": "Bank", "is_group": 0}, "name"
		)
		cls.other_account = frappe.db.get_value(
			"Account",
			{"company": COMPANY, "root_type": "Expense", "is_group": 0, "name": ("!=", ACCT)},
			"name",
		)
		if not (cls.inr_po and cls.usd_po and cls.purchase_mr and cls.other_account):
			raise cls.skipTest(cls, "site has no template PO / MR / account for COMPANY")

	def setUp(self):
		# Workflows on PO / MR are not what is under test.
		self._wf = patch.object(Document, "validate_workflow", lambda self: None)
		self._wf.start()
		self._wf2 = patch("erpnext.controllers.accounts_controller.get_workflow_name", return_value=None)
		self._wf2.start()
		frappe.set_user("Administrator")
		frappe.db.set_single_value("Accounts Settings", budget_check.ENABLE_FIELD, 1)
		for ba in (BA, BA_OTHER):
			self._make_budget_allocation(ba)
		self.budget = self._make_budget(BA, BUDGET)

	def tearDown(self):
		self._wf.stop()
		self._wf2.stop()
		frappe.set_user("Administrator")
		frappe.db.rollback()
		frappe.local.message_log = []

	# ---------- helpers ----------

	def _make_budget_allocation(self, name):
		if not frappe.db.exists("Budget Allocation", name):
			doc = frappe.get_doc({"doctype": "Budget Allocation", "month": f"unit test {name}"})
			doc.insert(set_name=name, ignore_permissions=True)
			doc.submit()

	def _make_budget(self, ba, amount, company=COMPANY, account=ACCT, fy=FY):
		b = frappe.get_doc(
			{
				"doctype": "Budget",
				"company": company,
				"fiscal_year": fy,
				"budget_against": "Budget Allocation",
				"budget_allocation": ba,
				"applicable_on_material_request": 1,
				"applicable_on_purchase_order": 1,
				"applicable_on_booking_actual_expenses": 1,
				# ERPNext's own (per-item) check only warns, so our check is what blocks
				"action_if_annual_budget_exceeded_on_mr": "Warn",
				"action_if_accumulated_monthly_budget_exceeded_on_mr": "Warn",
				"action_if_annual_budget_exceeded_on_po": "Warn",
				"action_if_accumulated_monthly_budget_exceeded_on_po": "Warn",
				"action_if_annual_budget_exceeded": "Warn",
				"action_if_accumulated_monthly_budget_exceeded": "Warn",
				"accounts": [{"account": account, "budget_amount": amount}],
			}
		)
		b.insert(ignore_permissions=True)
		b.submit()
		return b

	def _copy(self, doctype, name):
		doc = frappe.copy_doc(frappe.get_doc(doctype, name), ignore_no_copy=False)
		doc.items = doc.items[:1]
		doc.docstatus = 0
		for d in doc.get_all_children():
			d.docstatus = 0
		doc.status = "Draft"
		return doc

	def make_po(self, rate, qty=1, ba=BA, account=ACCT, usd=False, date=TXN_DATE, submit=True):
		po = self._copy("Purchase Order", self.usd_po if usd else self.inr_po)
		po.transaction_date = date
		po.schedule_date = SCHED_DATE if getdate(date) <= getdate(SCHED_DATE) else add_days(date, 10)
		po.ignore_pricing_rule = 1
		po.buying_price_list = None
		po.discount_amount = 0
		po.additional_discount_percentage = 0
		po.taxes_and_charges = None
		po.taxes = []
		po.payment_schedule = []
		if po.meta.has_field("workflow_state"):
			po.workflow_state = None
		_clean_lines(po, rate, qty, ba, account)
		for it in po.items:
			it.schedule_date = po.schedule_date
		po.insert(ignore_permissions=True)
		if submit:
			po.submit()
		return po

	def make_mr(self, rate, qty=1, ba=BA, mr_type="Purchase", submit=True):
		mr = self._copy("Material Request", self.purchase_mr)
		mr.transaction_date = TXN_DATE
		mr.schedule_date = SCHED_DATE
		mr.material_request_type = mr_type
		if mr.meta.has_field("workflow_state"):
			mr.workflow_state = None
		_clean_lines(mr, rate, qty, ba, ACCT)
		mr.insert(ignore_permissions=True)
		if submit:
			mr.submit()
		return mr

	def assert_blocked(self, fn, *args, **kwargs):
		with self.assertRaises(frappe.ValidationError) as cm:
			fn(*args, **kwargs)
		self.assertIn("over 100%", str(cm.exception))
		frappe.local.message_log = []
		return str(cm.exception)

	# ---------- Purchase Order: business rules ----------

	def test_po_within_budget_passes(self):
		po = self.make_po(400)
		self.assertEqual(po.docstatus, 1)

	def test_po_exactly_100_percent_passes(self):
		self.make_po(600)
		po = self.make_po(400)
		self.assertEqual(po.docstatus, 1)

	def test_po_over_100_percent_blocked_with_amounts(self):
		self.make_po(600)
		po = self.make_po(500, submit=False)
		msg = self.assert_blocked(po.submit)
		for text in ("1,000.00", "600.00", "500.00", "1,100.00", "110.0%", "100.00", ACCT, BA):
			self.assertIn(text, msg)

	def test_toggle_off_skips_check(self):
		frappe.db.set_single_value("Accounts Settings", budget_check.ENABLE_FIELD, 0)
		self.make_po(600)
		po = self.make_po(500)
		self.assertEqual(po.docstatus, 1)

	def test_cancelled_po_not_counted(self):
		po = self.make_po(900)
		po.cancel()
		self.assertEqual(self.make_po(900).docstatus, 1)

	def test_closed_po_counts_only_received_or_billed(self):
		po = self.make_po(900)
		po.update_status("Closed")
		# nothing received / billed -> a closed PO frees its budget
		self.assertEqual(self.make_po(900).docstatus, 1)

	def test_closed_po_received_part_still_counted(self):
		po = self.make_po(450, qty=2)  # 900
		frappe.db.set_value("Purchase Order Item", po.items[0].name, "received_qty", 1)  # 450 received
		po.reload()
		po.update_status("Closed")
		self.assertEqual(self.make_po(550).docstatus, 1)  # 450 + 550 = 1000
		po2 = self.make_po(1, submit=False)
		self.assert_blocked(po2.submit)

	def test_foreign_currency_converted_to_company_currency(self):
		usd = self.make_po(5, usd=True)  # USD 5
		inr_value = flt(usd.items[0].base_net_amount)
		self.assertGreater(inr_value, 5)  # converted at the PO's exchange rate
		remaining = BUDGET - inr_value
		if remaining > 1:
			self.assertEqual(self.make_po(flt(remaining, 2) - 0.01).docstatus, 1)
		po = self.make_po(1, submit=False)
		self.assert_blocked(po.submit)

	def test_other_dimension_value_not_counted(self):
		self._make_budget(BA_OTHER, 5000)
		self.make_po(900, ba=BA_OTHER)
		self.assertEqual(self.make_po(900).docstatus, 1)

	def test_other_account_not_counted(self):
		self.make_po(900, account=self.other_account)
		self.assertEqual(self.make_po(900).docstatus, 1)

	def test_other_fiscal_year_not_counted(self):
		self.make_po(900, date="2026-03-15")  # FY 25-26, no budget there
		self.assertEqual(self.make_po(900).docstatus, 1)

	def test_journal_entry_spend_counted(self):
		je = frappe.get_doc(
			{
				"doctype": "Journal Entry",
				"company": COMPANY,
				"posting_date": TXN_DATE,
				"accounts": [
					{
						"account": ACCT,
						"debit_in_account_currency": 600,
						"budget_allocation": BA,
						"cost_center": frappe.get_cached_value("Company", COMPANY, "cost_center"),
					},
					{
						"account": self.bank_account,
						"credit_in_account_currency": 600,
						"cost_center": frappe.get_cached_value("Company", COMPANY, "cost_center"),
					},
				],
			}
		)
		je.insert(ignore_permissions=True)
		je.submit()
		self.assertEqual(self.make_po(400).docstatus, 1)
		po = self.make_po(1, submit=False)
		self.assert_blocked(po.submit)

	def test_po_on_allocation_without_budget_unaffected(self):
		self.make_po(1000)
		po = self.make_po(5000, ba=BA_OTHER)  # BA_OTHER has no Budget in this test
		self.assertEqual(po.docstatus, 1)

	def test_update_items_increase_blocked_decrease_allowed(self):
		from erpnext.controllers.accounts_controller import update_child_qty_rate

		po = self.make_po(900)
		it = po.items[0]

		def trans(qty, rate=None):
			return json.dumps(
				[
					{
						"docname": it.name,
						"item_code": it.item_code,
						"qty": qty,
						"rate": rate or it.rate,
						"conversion_factor": it.conversion_factor,
						"schedule_date": str(it.schedule_date),
						"uom": it.uom,
					}
				]
			)

		msg = self.assert_blocked(update_child_qty_rate, "Purchase Order", trans(2), po.name)
		self.assertIn("Update Items is blocked", msg)
		frappe.db.rollback()  # update_child_qty_rate may have partly written child rows

		po = self.make_po(1000)  # budget now exactly full
		it = po.items[0]
		update_child_qty_rate("Purchase Order", trans(1, 500), po.name)  # reduce rate -> allowed
		self.assertEqual(flt(frappe.db.get_value("Purchase Order Item", it.name, "rate")), 500)

	# ---------- Material Request ----------

	def test_mr_within_budget_passes(self):
		self.assertEqual(self.make_mr(1000).docstatus, 1)

	def test_mr_counts_pos_and_pending_mrs(self):
		self.make_po(500)
		self.make_mr(400)
		mr = self.make_mr(200, submit=False)
		msg = self.assert_blocked(mr.submit)
		self.assertIn("900.00", msg)  # 500 PO + 400 pending MR

	def test_mr_ordered_part_not_counted_twice(self):
		mr = self.make_mr(100, qty=8)  # 800
		frappe.db.set_value("Material Request Item", mr.items[0].name, "ordered_qty", 6)  # 600 ordered
		self.make_po(600)  # the PO for those 6
		# used = 600 PO + 200 still pending = 800 -> 200 left
		self.assertEqual(self.make_mr(200).docstatus, 1)

	def test_stopped_mr_not_counted(self):
		mr = self.make_mr(900)
		mr.update_status("Stopped")
		self.assertEqual(self.make_mr(900).docstatus, 1)

	def test_non_purchase_mr_not_checked(self):
		self.make_po(1000)
		mr = self.make_mr(5000, mr_type="Material Transfer")
		self.assertEqual(mr.docstatus, 1)

	def test_po_check_ignores_pending_mrs(self):
		# A PO made from an MR must not count that MR again.
		self.make_mr(900)
		self.assertEqual(self.make_po(900).docstatus, 1)

	def test_mr_uses_qty_times_rate_not_stale_amount(self):
		mr = self.make_mr(400, qty=2, submit=False)
		mr.items[0].amount = 4  # stale amount, as left by a copy / API insert
		mr.items[0].db_update()
		mr.submit()  # 800, fits
		mr2 = self.make_mr(300, submit=False)
		msg = self.assert_blocked(mr2.submit)  # 800 + 300 > 1000
		self.assertIn("800.00", msg)

	def test_mr_rate_below_one_blocked(self):
		with self.assertRaises(frappe.ValidationError) as cm:
			self.make_mr(0.5, submit=False)
		self.assertIn("at least 1", str(cm.exception))

	# ---------- isolation ----------

	def test_other_company_isolated(self):
		self.make_po(900)
		other_company = frappe.db.get_value("Company", {"name": ("!=", COMPANY)}, "name")
		fy = frappe._dict(name=FY, year_start_date=getdate("2026-04-01"), year_end_date=getdate("2027-03-31"))
		budget = frappe._dict(
			name="x", budget_against_field="budget_allocation", against_value=BA, account=ACCT
		)
		doc = frappe._dict(doctype="Purchase Order", company=other_company, name="new-po")
		self.assertEqual(budget_check._get_used_amount(doc, budget, fy), 0)
		self.assertEqual(
			budget_check._get_budgets(other_company, FY, "applicable_on_purchase_order", {ACCT}), []
		)

	def test_fresh_connection_is_same_site_database(self):
		frappe.flags.in_test = False  # frappe.flags is a thread-local proxy; patch() can't wrap it
		try:
			db = budget_check._fresh_db()
		finally:
			frappe.flags.in_test = True
		try:
			self.assertIsNot(db, frappe.db)
			self.assertEqual(db.sql("select database()")[0][0], frappe.conf.db_name)
			self.assertEqual(db.sql("select database()")[0][0], frappe.db.sql("select database()")[0][0])
		finally:
			db.close()

	# ---------- users & roles ----------

	def test_restricted_user_still_sees_full_usage(self):
		"""Budget usage must include POs the submitting user cannot read."""
		first = self.make_po(900)
		email = "zz-budget-test-buyer@example.com"
		if not frappe.db.exists("User", email):
			user = frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": "ZZ Buyer",
					"send_welcome_email": 0,
					"roles": [{"role": "Purchase User"}],
				}
			)
			user.insert(ignore_permissions=True)
		# restrict the user to another Budget Allocation value only
		frappe.get_doc(
			{"doctype": "User Permission", "user": email, "allow": "Budget Allocation", "for_value": BA_OTHER}
		).insert(ignore_permissions=True)
		po = self.make_po(200, submit=False)
		frappe.set_user(email)
		# the user cannot open the ₹900 PO, but it must still count against the budget
		self.assertFalse(frappe.has_permission("Purchase Order", "read", doc=first.name))
		msg = self.assert_blocked(budget_check.validate_po_budget, po, "before_submit")
		self.assertIn("900.00", msg)
