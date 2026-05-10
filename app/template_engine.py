from datetime import timedelta
from fastapi.templating import Jinja2Templates
import models

templates = Jinja2Templates(directory="templates")


def _jst(dt, fmt: str = "%Y/%m/%d %H:%M") -> str:
    if dt is None:
        return "-"
    return (dt + timedelta(hours=9)).strftime(fmt)


templates.env.filters["jst"] = _jst
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
