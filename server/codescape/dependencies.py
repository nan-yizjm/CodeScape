from .call_judgments import reconcile_judgments


def validate_primary_dependencies(architecture, analysis, allowed_ids=None):
    confirmed = {call.id for call in reconcile_judgments(analysis, architecture).calls if call.resolution == "confirmed"}
    if allowed_ids is not None:
        confirmed &= allowed_ids
    for dependency in architecture.primary_dependencies:
        if (not dependency.reason.strip() or len(dependency.call_ids) != len(set(dependency.call_ids))
                or not set(dependency.call_ids) <= confirmed):
            raise ValueError("主要依赖必须引用当前上下文中已确认的真实调用，并说明核心协作理由。")
