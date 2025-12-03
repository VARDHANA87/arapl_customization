/* global get_expense_account */
frappe.ui.form.on("Purchase Order", {});

frappe.ui.form.on("Purchase Order Item", {
	cost_center: function (frm, cdt, cdn) {
		get_expense_account(frm, cdt, cdn);
	},
});
