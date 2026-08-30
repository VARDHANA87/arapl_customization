# Copyright (c) 2024, Repo Structure and Contributors
# License: MIT. See LICENSE

import os

import click
import frappe
from frappe import _
from frappe.commands import pass_context
from frappe.exceptions import SiteNotSpecifiedError


@click.command("8848-export-fixtures")
@click.option("--app", default=None, help="Export fixtures of a specific app")
@pass_context
def export_fixtures(context, app=None):
	"Export fixtures"

	for site in context.sites:
		try:
			frappe.init(site=site)
			frappe.connect()
			__export_fixtures(app=app)
		finally:
			frappe.destroy()
	if not context.sites:
		raise SiteNotSpecifiedError


def __export_fixtures(app=None):
	"""Export fixtures as JSON to `[app]/fixtures`"""
	if app:
		apps = [app]
	else:
		apps = frappe.get_installed_apps()
	for app in apps:
		for fixture in frappe.get_hooks("custom_fixtures", app_name=app):  # Custom Fixtures
			filters = None
			or_filters = None
			if isinstance(fixture, dict):
				filters = fixture.get("filters")
				or_filters = fixture.get("or_filters")
				fixture = fixture.get("doctype") or fixture.get("dt")
			print(f"Exporting {fixture} app {app} filters {(filters if filters else or_filters)}")
			if not os.path.exists(frappe.get_app_path(app, "fixtures")):
				os.mkdir(frappe.get_app_path(app, "fixtures"))

			__export_json(
				fixture,
				frappe.get_app_path(app, "fixtures", frappe.scrub(fixture) + ".json"),
				filters=filters,
				or_filters=or_filters,
				order_by="idx asc, creation asc",
			)


def __export_json(doctype, path, filters=None, or_filters=None, name=None, order_by="creation asc"):
	def post_process(out):
		# Note on Tree DocTypes:
		# The tree structure is maintained in the database via the fields "lft"
		# and "rgt". They are automatically set and kept up-to-date. Importing
		# them would destroy any existing tree structure. For this reason they
		# are not exported as well.
		del_keys = ("modified_by", "creation", "owner", "idx", "lft", "rgt")
		for doc in out:
			# Custom Fixtures: Remove keys that are not needed in the fixture
			for key in list(doc.keys()):
				if not doc.get(key):
					del doc[key]  # Remove keys with null, "" or 0 values

			for key in del_keys:
				if key in doc:
					del doc[key]

			for v in doc.values():
				if isinstance(v, list):
					for child in v:
						# Custom Fixtures: Remove keys that are not needed in the fixture from child
						for key in list(child.keys()):
							if not child.get(key):
								del child[key]  # Remove keys with null, "" or 0 values from child

						for key in (
							*del_keys,
							"docstatus",
							"doctype",
							"modified",
							"name",
						):
							if key in child:
								del child[key]

	out = []
	if name:
		out.append(frappe.get_doc(doctype, name).as_dict())
	elif frappe.db.get_value("DocType", doctype, "issingle"):
		out.append(frappe.get_doc(doctype).as_dict())
	else:
		for doc in frappe.get_all(
			doctype,
			fields=["name"],
			filters=filters,
			or_filters=or_filters,
			limit_page_length=0,
			order_by=order_by,
		):
			out.append(frappe.get_doc(doctype, doc.name).as_dict())
	post_process(out)

	# Custom Fixtures: Custom Fields are exported to separate files based on their doctype (dt)
	if doctype == "Custom Field":
		dt_map = {}
		for row in out:
			dt_map.setdefault(row.dt, [])
			dt_map[row.dt].append(row)

		for dt, rows in dt_map.items():
			dt_path = path.replace("custom_field.json", f"{frappe.scrub(dt)}.json")
			dirname = os.path.dirname(dt_path)
			if not os.path.exists(dirname):
				dt_path = os.path.join("..", dt_path)

			with open(dt_path, "w") as outfile:
				outfile.write(frappe.as_json(rows, ensure_ascii=False))
	else:
		dirname = os.path.dirname(path)
		if not os.path.exists(dirname):
			path = os.path.join("..", path)

		with open(path, "w") as outfile:
			outfile.write(frappe.as_json(out, ensure_ascii=False))
