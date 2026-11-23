"""An issue's body as HTML, without the issue form's boilerplate.

GitHub issue forms write one `### Field` section per question. Sections answered `_No response_`, the
checkbox-only "I have read…" sections and everything from the `<!-- footer -->` heading on say nothing about the
issue, so they are dropped. Raw HTML in the body is escaped, not rendered, except GitHub's `<img>` attachments,
turned into Markdown images first.
"""

import re

from markdown_it import MarkdownIt
from markupsafe import Markup

_md = MarkdownIt("commonmark", {"html": False, "linkify": True}).enable(["table", "strikethrough", "linkify"])
_md.inline.ruler.disable(["linkify"])  # the core rule links bare URLs after `~~` is parsed, so ~~https://old/~~ is struck
_IMG = re.compile(r"<img\b[^>]*?\bsrc=\"(https://[^\"\s]+)\"[^>]*>", re.I)
_ALT = re.compile(r"\balt=\"([^\"]*)\"", re.I)
_SECTION = re.compile(r"^(?=#{1,6} )", re.M)
_FOOTER = re.compile(r"^#{1,6} *<!-- *footer *-->.*", re.M | re.S)
_TASK = re.compile(r"^\s*[-*] \[[ xX]\] ")


def _says_something(section: str) -> bool:
    lines = [line for line in section.splitlines()[1:] if line.strip()] if section.startswith("#") else section.splitlines()
    if not any(line.strip() for line in lines):
        return not section.startswith("#")  # a heading with nothing under it
    if all(line.strip() == "_No response_" for line in lines):
        return False
    return not all(_TASK.match(line) for line in lines)


def clean(body: str | None) -> str:
    body = _FOOTER.sub("", (body or "").replace("\r\n", "\n"))
    body = _IMG.sub(lambda m: f"![{(a.group(1) if (a := _ALT.search(m.group(0))) else '').replace(']', '')}]({m.group(1)})", body)
    return "".join(s for s in _SECTION.split(body) if _says_something(s)).strip()


def render(body: str | None) -> Markup:
    html = _md.render(clean(body))
    # Links from issue authors carry no endorsement; images load lazily.
    html = html.replace("<a href=", '<a rel="nofollow noopener" href=').replace("<img ", '<img loading="lazy" ')
    return Markup(html)
