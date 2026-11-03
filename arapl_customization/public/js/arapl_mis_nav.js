/*
 * Arapl MIS navigation glue.
 *
 * Adds a "MIS Dashboard" button to the Purchase Order Pivot Summary v3 report
 * and removes it again as soon as another query report is opened (the query
 * report page is a single shared Page instance, so the button has to be
 * cleaned up on route change).
 *
 * Loaded globally from hooks.py -> app_include_js.
 */
frappe.provide("arapl.mis.nav");

(function () {
	var REPORT = "Purchase Order Pivot Summary v3";
	var PAGE = "arapl-mis-dashboard";
	var LABEL = __("MIS Dashboard");

	function current_route() {
		try {
			return frappe.get_route() || [];
		} catch (e) {
			return [];
		}
	}

	function on_target_report() {
		var route = current_route();
		var name = route[1] || "";
		try {
			name = decodeURIComponent(name);
		} catch (e) {
			/* keep as is */
		}
		return route[0] === "query-report" && name === REPORT;
	}

	function report_page() {
		if (frappe.query_report && frappe.query_report.page) {
			return frappe.query_report.page;
		}
		return null;
	}

	function has_button(page) {
		return page.inner_toolbar.find(
			'button[data-label="' + encodeURIComponent(LABEL) + '"]'
		).length > 0;
	}

	function drop_menu_items(page) {
		// dropdown items store the raw label, toolbar buttons the encoded one
		page.menu
			.find(
				'a[data-label="' +
					LABEL +
					'"], a[data-label="' +
					encodeURIComponent(LABEL) +
					'"]'
			)
			.remove();
	}

	function sync() {
		var page = report_page();
		if (!page || !page.inner_toolbar) {
			return;
		}
		if (on_target_report()) {
			if (!has_button(page)) {
				page.add_inner_button(
					LABEL,
					function () {
						frappe.set_route(PAGE, { report: "po-pivot-summary" });
					},
					null,
					"primary"
				);
			}
		} else if (has_button(page)) {
			page.remove_inner_button(LABEL);
			drop_menu_items(page);
		}
	}

	function schedule() {
		// the report view builds asynchronously, so check a few times
		setTimeout(sync, 100);
		setTimeout(sync, 600);
		setTimeout(sync, 1500);
	}

	$(document).on("page-change", schedule);
	try {
		frappe.router.on("change", schedule);
	} catch (e) {
		/* older routers: page-change above still covers it */
	}
	$(schedule);
})();
