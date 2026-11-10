"""Extract numbered source passages and select the exact evidence for one question."""
import copy
import re
import unicodedata
from .paper import paper_spec
from .annotations import material_signature

NUMBER = r'(?:[0-9０-９]{1,3}|[一二三四五六七八九十百零〇两]{1,6})'
HEADING = re.compile(
    rf'^[ \t]*[【\[]?(?:给定)?(?:材料|资料)[ \t]*[（(]?[ \t]*(?P<number>{NUMBER})'
    r'[ \t]*[）)】\]]?[ \t]*(?:[:：、][ \t]*(?P<title>[^\n]{0,60}))?[ \t]*$', re.M)
ATOM = re.compile(rf'''[\s“”"'「」『』（(【\[]*(?P<number>{NUMBER})[ \t“”"'」』）)】\]]*''')
PREFIX = re.compile(r'(?:给定)?(?:材料|资料)[ \t]*')
CONNECTOR = re.compile(r'[ \t]*(?P<join>以及|及|和|与|、|，|,|至|到|[-—–~～])[ \t]*(?:(?:给定)?(?:资料|材料)[ \t]*)?')
RANGES = {'至','到','-','—','–','~','～'}
ALL = re.compile(r'(?:全部|所有|整份|上述全部|以上全部)(?:的)?(?:给定)?(?:材料|资料)')


def number_value(label):
    label = unicodedata.normalize('NFKC', label)
    if label.isdigit():
        return int(label)
    digits = dict(zip('零〇一二两三四五六七八九', [0,0,1,2,2,3,4,5,6,7,8,9]))
    total, current = 0, 0
    for ch in label:
        if ch in digits:
            current = digits[ch]
        elif ch in ('十','百'):
            total += (current or 1) * (10 if ch == '十' else 100)
            current = 0
        else:
            raise ValueError('unrecognised number')
    return total + current


def split_materials(text):
    matches = list(HEADING.finditer(text))
    blocks, seen = [], set()
    for index, match in enumerate(matches):
        number = number_value(match['number'])
        # Repeated/invalid headings cannot establish safe boundaries.
        if not 1 <= number <= 999 or number in seen:
            return []
        seen.add(number)
        end = matches[index+1].start() if index+1 < len(matches) else len(text)
        blocks.append({'id':number, 'title':f'资料 {number}', 'text':text[match.start():end].strip()})
    return blocks


def referenced_ids(prompt):
    ids = set()
    for prefix in PREFIX.finditer(prompt):
        atom = ATOM.match(prompt, prefix.end())
        if not atom:
            continue
        previous = number_value(atom['number'])
        ids.add(previous)
        end = atom.end()
        while True:
            connector = CONNECTOR.match(prompt, end)
            if not connector:
                break
            atom = ATOM.match(prompt, connector.end())
            if not atom:
                break
            value = number_value(atom['number'])
            if connector['join'] in RANGES:
                if value < previous or value - previous > 100:
                    raise ValueError('题干中的资料范围不明确')
                ids.update(range(previous, value+1))
            else:
                ids.add(value)
            previous, end = value, atom.end()
    return sorted(ids)


def resolve_materials(text, question, blocks=None):
    blocks = split_materials(text) if blocks is None else blocks
    mode = question.get('material_mode', 'auto')
    requested, matched, review = [], [], False
    selected = text.strip()
    if mode == 'custom':
        selected = question.get('custom_materials', '').strip()
        note, scope = '使用人工校对的本题材料', 'custom'
    elif mode == 'all':
        note, scope = '已指定使用全部材料', 'all'
        matched = [b['id'] for b in blocks]
    else:
        try:
            requested = list(dict.fromkeys(question.get('material_ids', []))) if mode == 'selected' else referenced_ids(question['prompt'])
        except ValueError:
            requested = []
            note, scope, review = '资料范围不明确，暂用全部材料，请校对', 'fallback', True
        else:
            if mode == 'auto' and ALL.search(question['prompt']):
                note, scope = '题目要求结合全部给定材料', 'all'
                matched = [b['id'] for b in blocks]
            elif requested and blocks:
                by_id = {b['id']:b for b in blocks}
                missing = sorted(set(requested) - by_id.keys())
                if missing:
                    note, scope, review = f'资料 {"、".join(map(str,missing))} 未定位，暂用全部材料，请校对', 'fallback', True
                else:
                    matched = [b['id'] for b in blocks if b['id'] in requested]
                    selected = '\n\n'.join(by_id[id]['text'] for id in matched)
                    note, scope = '仅使用资料 ' + '、'.join(map(str,matched)), 'selected'
            elif requested:
                note, scope, review = '未能可靠拆分资料段落，暂用全部材料，请校对', 'fallback', True
            else:
                note, scope = '题干未限定资料编号，使用全部材料', 'all'
                matched = [b['id'] for b in blocks]
                review = True
    requires_choice = mode not in ('all','custom') and review and len(blocks) > 1
    if requires_choice:
        note = '题干未能明确材料范围，请选择本题使用的材料编号范围或全部材料'
    count, total = len(selected), len(text.strip())
    return selected, dict(mode=scope, requested_ids=requested, matched_ids=matched, note=note,
                         needs_review=review, requires_choice=requires_choice, character_count=count, total_character_count=total,
                         saved_percent=max(0,round((1-count/total)*100)) if total else 0)


def prepare_paper(paper):
    p = copy.deepcopy(paper)
    blocks = split_materials(p['materials'])
    p['material_blocks'] = blocks
    p['materials_version'] = 1
    p['requires_material_selection'] = False
    for q in p['questions']:
        q.setdefault('material_mode', 'auto')
        q.setdefault('material_ids', [])
        q.setdefault('custom_materials', '')
        q['materials'], q['material_scope'] = resolve_materials(p['materials'], q, blocks)
        p['requires_material_selection'] |= q['material_scope']['requires_choice']
        q['paper_spec'] = paper_spec(q)
        q['material_signature'] = material_signature(q['materials'])
    from .reference_policy import is_essay
    for q in p['questions']:
        q.pop('reference_material_priority',None)
        if is_essay(q):q['reference_material_priority']=essay_material_priority(p['questions'],q,blocks)
    return p


def scoped_material_ids(question, blocks):
    scope=question.get('material_scope',{})
    if scope.get('requires_choice') or scope.get('mode')=='fallback':return None
    if scope.get('mode') in ('selected','all'):
        return scope.get('matched_ids') if blocks else None
    if scope.get('mode')=='custom':
        selected=split_materials(question.get('materials',''))
        original={b['id']:b['text'] for b in blocks}
        if selected and all(original.get(b['id'])==b['text'] for b in selected):
            return [b['id'] for b in selected]
    return None


def essay_material_priority(questions, question, blocks):
    from .reference_policy import is_essay
    priority=dict(version=1,available=False,preferred_ids=[],secondary_ids=[],unresolved_prior_questions=[])
    current=scoped_material_ids(question,blocks)
    if current is None:return priority
    used=set()
    for prior in questions:
        if prior['number']>=question['number'] or is_essay(prior):continue
        ids=scoped_material_ids(prior,blocks)
        if ids is None:priority['unresolved_prior_questions'].append(prior['number'])
        else:used.update(ids)
    priority.update(available=True,preferred_ids=[i for i in current if i not in used],secondary_ids=[i for i in current if i in used])
    return priority


def model_question(snapshot):
    # Never send a whole paper, unrelated questions, or derived editor fields to a model.
    if snapshot.get('material_scope'):
        materials = snapshot['materials']
    else:
        materials, _ = resolve_materials(snapshot.get('materials',''), snapshot)
    return {k:snapshot[k] for k in ('number','kind','prompt','word_min','word_max') if k in snapshot} | {'materials':materials, 'paper_spec':snapshot.get('paper_spec') or paper_spec(snapshot)}
