/* global get_expense_account */
frappe.ui.form.on("Purchase Order", {});

frappe.ui.form.on("Purchase Order Item", {
	budget_allocation: function (frm, cdt, cdn) {
		get_expense_account(frm, cdt, cdn);
	},
});
