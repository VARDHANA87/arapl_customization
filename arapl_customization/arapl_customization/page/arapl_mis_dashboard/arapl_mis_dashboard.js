frappe.pages["arapl-mis-dashboard"].on_page_load = function (wrapper) {
	var params = new URLSearchParams(window.location.search);
	var report = (params.get("report") || "po-pivot-summary").toLowerCase();

	var reports = {
		"po-pivot-summary": {
			title: "MIS · Purchase Order Pivot Summary",
			url: "/api/method/arapl_customization.mis_reports.builders.view_po_pivot_summary",
			standard_report: "Purchase Order Pivot Summary v3",
		},
	};

	var def = reports[report] || reports["po-pivot-summary"];

	var page = frappe.ui.make_app_page({
		parent: wrapper,
		title: def.title,
		single_column: true,
	});

	var $iframe = $("<iframe>").attr("src", def.url).attr("title", def.title).css({
		width: "100%",
		height: "calc(100vh - 140px)",
		border: "0",
		background: "#fff",
		display: "block",
	});

	// primary action: reload the dashboard without leaving the page
	page.set_primary_action(
		__("Refresh"),
		function () {
			$iframe.attr("src", def.url + "?_t=" + Date.now());
		},
		"refresh"
	);

	// quick jump back to the standard report in the same tab
	page.add_inner_button(__("Standard Report"), function () {
		frappe.set_route("query-report", def.standard_report);
	});

	page.add_inner_button(__("Purchase Orders"), function () {
		frappe.set_route("List", "Purchase Order");
	});

	page.add_menu_item(
		__("Clear filters"),
		function () {
			$iframe.attr("src", def.url);
		},
		true
	);

	page.add_menu_item(
		__("Open in new tab"),
		function () {
			window.open(def.url, "_blank");
		},
		true
	);

	$(page.body).empty().append($iframe);
};
