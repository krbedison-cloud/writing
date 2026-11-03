"""Short, bounded observations for the profile view and its history evidence."""
import re

BRIEF_POLICY = ('只总结要点，不写长报告、套话、重复的免责声明或Markdown。'
                'analysis用1–2句概括趋势，最多100字；strengths最多2条，weaknesses最多2条，'
                'action_plan最多3条，每条最多50字、只含一个具体动作，优先级从高到低。'
                '全文控制在350字内。证据不足直说，不编造优势或问题。')

def short_text(value, limit):
    text = re.sub(r'\s+', ' ', value).strip()
    return text if len(text) <= limit else text[:limit-1].rstrip() + '…'

def brief_points(items, count=3, limit=60):
    result = []
    for item in items:
        text = short_text(item, limit)
        if text and text not in result:
            result.append(text)
        if len(result) == count:
            break
    return result

def brief_portrait(result):
    return dict(analysis=short_text(result['analysis'], 120),
                strengths=brief_points(result['strengths'], 2),
                weaknesses=brief_points(result['weaknesses'], 2),
                action_plan=brief_points(result['action_plan'], 3))
