from datetime import timedelta
from fastapi.templating import Jinja2Templates
import markdown as _markdown
import bleach
import models

templates = Jinja2Templates(directory="templates")


def _jst(dt, fmt: str = "%Y/%m/%d %H:%M") -> str:
    if dt is None:
        return "-"
    return (dt + timedelta(hours=9)).strftime(fmt)


_MD_ALLOWED_TAGS = {
    "p", "br", "hr", "span", "div",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "strong", "em", "del", "code", "pre", "blockquote",
    "ul", "ol", "li",
    "a", "img",
    "table", "thead", "tbody", "tr", "th", "td",
}
_MD_ALLOWED_ATTRS = {
    "a": ["href", "title", "rel", "target"],
    "img": ["src", "alt", "title", "width", "height"],
    "*": ["class"],
}


def _render_markdown(text: str) -> str:
    if not text:
        return ""
    html = _markdown.markdown(
        text,
        extensions=["fenced_code", "tables", "nl2br", "sane_lists"],
    )
    return bleach.clean(html, tags=_MD_ALLOWED_TAGS, attributes=_MD_ALLOWED_ATTRS)


templates.env.filters["jst"] = _jst
templates.env.filters["markdown"] = _render_markdown
templates.env.globals["SKILL_LEVELS"] = models.SKILL_LEVELS
templates.env.globals["LEVEL_COLORS"] = models.LEVEL_COLORS
templates.env.globals["SKILL_TIERS"] = models.SKILL_TIERS
templates.env.globals["TIER_COLORS"] = models.TIER_COLORS
templates.env.globals["TIER_ICONS"] = models.TIER_ICONS
templates.env.globals["TIER_DESCRIPTIONS"] = models.TIER_DESCRIPTIONS
templates.env.globals["APPROVAL_STATUS"] = models.APPROVAL_STATUS
templates.env.globals["APPROVAL_STATUS_COLORS"] = models.APPROVAL_STATUS_COLORS
templates.env.globals["TICKET_TYPES"] = models.TICKET_TYPES
templates.env.globals["TICKET_STATUS"] = models.TICKET_STATUS
templates.env.globals["TICKET_STATUS_COLORS"] = models.TICKET_STATUS_COLORS
templates.env.globals["TICKET_PRIORITY"] = models.TICKET_PRIORITY
templates.env.globals["TICKET_PRIORITY_COLORS"] = models.TICKET_PRIORITY_COLORS
templates.env.globals["ANNOUNCEMENT_TYPES"]        = models.ANNOUNCEMENT_TYPES
templates.env.globals["ANNOUNCEMENT_TYPE_COLORS"]  = models.ANNOUNCEMENT_TYPE_COLORS
templates.env.globals["WIKI_EDIT_MODES"]           = models.WIKI_EDIT_MODES
templates.env.globals["WIKI_VISIBILITY"]           = models.WIKI_VISIBILITY
