"""Custom Guardrails action: claim-level grounding of the bot message against the dispatch record."""
from typing import Optional

from nemoguardrails.actions import action
from nemoguardrails.actions.actions import ActionResult

from er_agent.guard import check


@action(is_system_action=True)
async def check_patient_grounding(context: Optional[dict] = None):
    context = context or {}
    record = context.get("record", "")
    bot_message = context.get("bot_message", "")
    grounded, unsupported = check(record, bot_message)
    return ActionResult(return_value=grounded, context_updates={"unsupported_claims": unsupported})
