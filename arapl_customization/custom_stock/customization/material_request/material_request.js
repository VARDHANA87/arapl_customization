/* global get_expense_account */
frappe.ui.form.on("Material Request", {});

frappe.ui.form.on("Material Request Item", {
	cost_center: function (frm, cdt, cdn) {
		get_expense_account(frm, cdt, cdn);
	},
});
