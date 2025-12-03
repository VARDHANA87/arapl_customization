window.get_expense_account = function (frm, cdt, cdn) {
	let row = locals[cdt][cdn];

	if (!row.cost_center) {
		frappe.model.set_value(cdt, cdn, "expense_account", "");
		return;
	}

	frappe.call({
		method: "frappe.client.get_list",
		args: {
			doctype: "Budget",
			filters: {
				cost_center: row.cost_center,
			},
			fields: ["name"],
			limit_page_length: 1,
		},
		callback: function (r) {
			if (!r.message || !r.message.length) {
				frappe.msgprint(__("No Budget found for this Budget Allocation"));
				return;
			}

			let budget_name = r.message[0].name;

			frappe.call({
				method: "frappe.client.get",
				args: {
					doctype: "Budget",
					name: budget_name,
				},
				callback: function (res) {
					if (!res.message) return;

					let accounts = res.message.accounts || [];

					if (!accounts.length) {
						frappe.msgprint(__("No Accounts found inside this Budget"));
						return;
					}

					frappe.model.set_value(cdt, cdn, "expense_account", accounts[0].account);
					// if (accounts.length === 1) {
					//     frappe.model.set_value(cdt, cdn, 'expense_account', accounts[0].account);
					// }
					// else {
					//     let options = accounts.map(a => a.account);

					//     frappe.prompt([
					//         {
					//             label: "Select Expense Account",
					//             fieldname: "expense_account",
					//             fieldtype: "Select",
					//             options: options.join("\n"),
					//             reqd: 1
					//         }
					//     ], function(values) {
					//         frappe.model.set_value(cdt, cdn, 'expense_account', values.expense_account);
					//     });
					// }
				},
			});
		},
	});
};
