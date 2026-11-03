"""
Arapl print customization - document status printed as a normal detail line.

Instead of a separate banner above the page, the status is injected *inside*
the print format's own header block, styled exactly like the surrounding
details:

    Purchase Order
    PO NO: PO-26-27-00186-1
    PO DATE: 2026-08-29
    STATUS: Submitted (To Bill)          <-- injected

Placement rules (first match wins)

1. Standard layout (``.print-heading``): appended at the end of the heading
   block, right under "Purchase Receipt / GR-26-27-00277".
2. Custom formats: the document name is located in the rendered HTML, then
   the surrounding header block is walked up until the block no longer holds
   further "PO DATE:" style lines, and the status is appended to the end of
   that block - so it follows the last detail line of the header.
3. No document name in the format: appended right after the heading that
   carries the DocType name.

No colour, no border, no wrapper: the line inherits font family, size,
alignment and weight from the print format itself.

Registered from hooks.py::

    pdf_body_html = "arapl_customization.print_status.pdf_body_html"

Frappe calls this hook once per rendered print (preview, single PDF, merged
PDF) for standard formats, custom formats and the generic layout alike.
No core file is modified. Raw/ESC-POS formats are skipped.
"""

import re

import frappe
from frappe.utils import escape_html

DOCSTATUS = {0: "Draft", 1: "Submitted", 2: "Cancelled"}

VOID_TAGS = {
	"area",
	"base",
	"br",
	"col",
	"embed",
	"hr",
	"img",
	"input",
	"link",
	"meta",
	"param",
	"source",
	"track",
	"wbr",
}
# Tags that can hold a status line as a block of its own.
LINE_TAGS = {
	"div",
	"p",
	"li",
	"td",
	"th",
	"caption",
	"address",
	"center",
	"section",
	"article",
	"header",
	"footer",
	"h1",
	"h2",
	"h3",
	"h4",
	"h5",
	"h6",
}
# Tags we are willing to walk up into while collecting header detail lines.
CONTAINER_TAGS = {
	"div",
	"p",
	"li",
	"td",
	"th",
	"caption",
	"address",
	"center",
	"section",
	"article",
	"header",
	"footer",
	"blockquote",
}
HEADING_TAGS = {"h1", "h2", "h3", "h4"}

MAX_BLOCK_CHARS = 4000
MAX_AFTER_CHARS = 400
MAX_AFTER_TEXT = 60
LABEL_LINE_RE = re.compile(r"[A-Za-z][A-Za-z0-9 ./&()#'-]{1,30}\s*:")
LABEL_ANCHOR_RE = re.compile(
	r"\b(?:Invoice|PO|P\.O\.|SO|GRN|Voucher|Document|Bill|Order|Receipt|Credit Note)"
	r"\s*(?:No\.?|Number|#)",
	re.I,
)
TAG_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9]*)\b([^>]*)>", re.S)
CLASS_RE = re.compile(r"""class\s*=\s*(["'])(.*?)\1""", re.I)
TRAIL_RE = re.compile(r"(\s*<br\s*/?>\s*|\s+)$", re.I)


class _Node:
	__slots__ = (
		"attrs",
		"children",
		"close_end",
		"close_start",
		"open_end",
		"parent",
		"start",
		"tag",
	)

	def __init__(self, tag, attrs, start, open_end, parent):
		self.tag = tag
		self.attrs = attrs
		self.start = start
		self.open_end = open_end
		self.close_start = open_end
		self.close_end = open_end
		self.parent = parent
		self.children = []


# --------------------------------------------------------------------------- #
# status text
# --------------------------------------------------------------------------- #
def _status_text(doc):
	"""Return the text to print, or '' when there is nothing meaningful."""
	meta = getattr(doc, "meta", None)
	docstatus = getattr(doc, "docstatus", None)

	label = None
	if meta is not None and getattr(meta, "is_submittable", 0):
		label = DOCSTATUS.get(docstatus)

	try:
		status = (doc.get("status") or "").strip()
	except Exception:
		status = ""

	if not label:
		# Non-submittable DocType: print its own status field if it has one.
		return status

	if status and status.casefold() != label.casefold():
		return f"{label} ({status})"
	return label


def _status_line(doc):
	text = _status_text(doc)
	if not text:
		return ""
	return (
		"<div><strong>STATUS:</strong> "
		f'<span style="font-weight: normal;">{escape_html(text)}</span></div>'
	)


# --------------------------------------------------------------------------- #
# tiny HTML scanner
# --------------------------------------------------------------------------- #
def _parse(html):
	root = _Node("#root", "", 0, 0, None)
	root.close_start = root.close_end = len(html)
	stack = [root]

	for m in TAG_RE.finditer(html):
		closing, tag, attrs = m.group(1), m.group(2).lower(), m.group(3)
		self_closing = attrs.rstrip().endswith("/")

		if tag in VOID_TAGS or (not closing and self_closing):
			continue

		if closing:
			for i in range(len(stack) - 1, 0, -1):
				if stack[i].tag == tag:
					while len(stack) > i:
						node = stack.pop()
						node.close_start = m.start()
						node.close_end = m.end()
					break
		else:
			parent = stack[-1]
			node = _Node(tag, attrs, m.start(), m.end(), parent)
			parent.children.append(node)
			stack.append(node)

	for node in stack[1:]:  # never closed
		node.close_start = node.close_end = len(html)
	return root


def _walk(node):
	for child in node.children:
		yield child
		yield from _walk(child)


def _inner(html, node):
	return html[node.open_end : node.close_start]


def _plain_text(fragment):
	return re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", fragment or "")).strip()


def _has_label_line(fragment):
	return bool(LABEL_LINE_RE.search(_plain_text(fragment)))


def _deepest_at(node, index):
	found = node
	for child in node.children:
		if child.start <= index < max(child.close_end, child.open_end):
			found = _deepest_at(child, index)
			break
	return found


def _has_class(node, name):
	m = CLASS_RE.search(node.attrs or "")
	return bool(m and name in m.group(2).split())


def _find_first_with_class(root, name):
	for node in _walk(root):
		if _has_class(node, name):
			return node
	return None


def _trim_before(html, pos):
	"""Move pos back over trailing whitespace / <br> so the line sits inside."""
	for _ in range(20):
		m = TRAIL_RE.search(html[:pos])
		if not m or m.start() == pos:
			break
		pos = m.start()
	return pos


def _insert_at_end(html, node, fragment):
	pos = _trim_before(html, node.close_start)
	return html[:pos] + fragment + html[pos:]


# --------------------------------------------------------------------------- #
# placement
# --------------------------------------------------------------------------- #
def _name_index(html, doc):
	"""Index of the first rendered occurrence of doc.name outside style/script."""
	name = getattr(doc, "name", None)
	if not name:
		return None

	limit = min(len(html), 30000)
	head = html[:limit]
	needles = [escape_html(name)]
	if name not in needles:
		needles.append(name)

	starts = []
	for needle in needles:
		start = head.find(needle)
		while start != -1:
			starts.append(start)
			start = head.find(needle, start + 1)
	if not starts:
		return None

	executed = _parse(head)
	skip = {"script", "style", "title", "head", "meta", "option"}
	for index in sorted(starts):
		if _deepest_at(executed, index).tag not in skip:
			return index
	return None


def _climb(html, node):
	"""Walk up while the parent block still holds further detail lines.

	A parent qualifies when the content that follows the anchor is still short
	header material (the PO DATE line, the invoice date, ...) rather than the
	document body. That way the status ends up after the *last* detail line of
	the header block instead of in the middle of it.
	"""
	while True:
		parent = node.parent
		if parent is None or parent.tag not in CONTAINER_TAGS:
			break

		inner = _inner(html, parent)
		if len(inner) > MAX_BLOCK_CHARS or "<table" in inner.lower():
			break

		after = _plain_text(html[node.close_end : parent.close_start])
		if not after or len(after) > MAX_AFTER_CHARS:
			break
		# Short trailing text (a date, a value) or a full "LABEL: value" line
		# keeps us inside the header block; anything longer is body content.
		if len(after) > MAX_AFTER_TEXT and not _has_label_line(after):
			break

		node = parent
	return node


def _heading_for_doctype(html, root, doc):
	meta = getattr(doc, "meta", None)
	names = set()
	if meta is not None:
		names.add((getattr(meta, "name", "") or "").strip().casefold())
	names.add((getattr(doc, "doctype", "") or "").strip().casefold())
	names.discard("")

	for node in _walk(root):
		if node.tag not in HEADING_TAGS:
			continue
		text = _plain_text(_inner(html, node)).casefold()
		if not text:
			continue
		if any(name and (name in text or text in name) for name in names):
			return node
	return None


def _label_index(html):
	"""Index of a 'INVOICE NO' / 'PO No.' style header label in visible text."""
	limit = min(len(html), 8000)
	head = html[:limit]
	style_end = head.lower().rfind("</style>")
	if style_end == -1:
		style_end = head.lower().rfind("</script>")
	if style_end == -1:
		style_end = 0

	for match in LABEL_ANCHOR_RE.finditer(head):
		if match.start() < style_end:
			continue
		# ignore matches inside a tag, e.g. class="...order..."
		if html.rfind("<", 0, match.start()) > html.rfind(">", 0, match.start()):
			continue
		return match.start()
	return None


def _place_at(html, root, index, line):
	"""Insert the status line next to the header detail found at index."""
	node = _deepest_at(root, index)
	block = node
	while block is not None and block.tag not in LINE_TAGS:
		block = block.parent
	if block is None or block.tag == "#root":
		return None

	block = _climb(html, block)
	if block.tag in HEADING_TAGS:
		# Never inherit the heading's font: sit right below it.
		return html[: block.close_end] + line + html[block.close_end :]

	fragment = ("<br>" + line) if block.tag in ("td", "th") else line
	return _insert_at_end(html, block, fragment)


def _inject(html, doc):
	line = _status_line(doc)
	if not line:
		return html

	root = _parse(html)

	# 1. Standard layout: append inside the print header.
	heading = _find_first_with_class(root, "print-heading")
	if heading is not None and _plain_text(_inner(html, heading)):
		return _insert_at_end(html, heading, line)

	# 2. Custom formats: anchor on the document name printed in the header.
	index = _name_index(html, doc)
	if index is not None:
		placed = _place_at(html, root, index, line)
		if placed:
			return placed

	# 3. Format does not print the document name: sit under the DocType heading.
	heading = _heading_for_doctype(html, root, doc)
	if heading is not None:
		return html[: heading.close_end] + line + html[heading.close_end :]

	# 4. Last resort: anchor on an "INVOICE NO" / "PO No." label.
	index = _label_index(html)
	if index is not None:
		placed = _place_at(html, root, index, line)
		if placed:
			return placed

	return html


# --------------------------------------------------------------------------- #
# hook
# --------------------------------------------------------------------------- #
def pdf_body_html(template=None, args=None, print_format=None, **kwargs):
	html = template.render(args) if hasattr(template, "render") else (template or "")

	# Thermal / ESC-POS formats are command streams, not HTML.
	if print_format is not None and getattr(print_format, "raw_printing", 0):
		return html

	# Hide watermark in PDF download
	if frappe.form_dict.get("cmd") == "frappe.utils.print_format.download_pdf" or getattr(
		frappe.flags, "in_download_pdf", False
	):
		html = "<style>.status-watermark { display: none !important; }</style>" + html

	# Suppress watermark when downloading or generating PDF
	if frappe.form_dict.get("cmd") == "frappe.utils.print_format.download_pdf" or getattr(
		frappe.flags, "in_download_pdf", False
	):
		html = "<style>.status-watermark { display: none !important; }</style>" + html

	doc = (args or {}).get("doc")
	if doc is None:
		return html

	try:
		return _inject(html, doc)
	except Exception:
		# A print must never fail because of this customization.
		frappe.log_error(frappe.get_traceback(), "Arapl print status")
		return html
