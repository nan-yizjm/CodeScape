"""受限 Python 递归聚合的源码事实；不是执行器或通用语义证明。"""
from __future__ import annotations

import ast
import re


def recursive_fold_facts(analysis, files):
    facts = {}
    entities = {(item.location.path,item.location.start_line,item.name):item for item in analysis.entities
                if item.visible and item.language == 'python'}
    self_calls = {(call.caller_scope_id,call.target_id,call.location.start_line) for call in analysis.calls
                  if call.resolution == 'confirmed' and call.kind == 'direct'}
    for path, text in files.items():
        if not path.endswith('.py'):
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            entity = entities.get((path,node.lineno,node.name))
            if not entity:
                continue
            body = list(node.body)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
                body.pop(0)
            if len(body) != 5:
                continue
            collection, base, seed, loop, returned = body
            if not (isinstance(collection, ast.Assign) and len(collection.targets) == 1 and isinstance(collection.targets[0], ast.Name)
                    and isinstance(base, ast.If) and not base.orelse and isinstance(base.test, ast.UnaryOp)
                    and isinstance(base.test.op, ast.Not) and isinstance(base.test.operand, ast.Name)
                    and base.test.operand.id == collection.targets[0].id and len(base.body) == 1
                    and isinstance(base.body[0], ast.Return) and isinstance(base.body[0].value, ast.Constant)
                    and type(base.body[0].value.value) is int
                    and isinstance(seed, ast.Assign) and len(seed.targets) == 1 and isinstance(seed.targets[0], ast.Name)
                    and isinstance(seed.value, ast.Constant) and type(seed.value.value) is int
                    and isinstance(loop, ast.For) and not loop.orelse and isinstance(loop.target, ast.Name)
                    and isinstance(loop.iter, ast.Name) and loop.iter.id == collection.targets[0].id
                    and len(loop.body) == 1 and isinstance(loop.body[0], ast.AugAssign)
                    and isinstance(loop.body[0].op, ast.Add) and isinstance(loop.body[0].target, ast.Name)
                    and loop.body[0].target.id == seed.targets[0].id
                    and isinstance(loop.body[0].value, ast.Call) and isinstance(loop.body[0].value.func, ast.Name)
                    and loop.body[0].value.func.id == node.name and len(loop.body[0].value.args) == 1
                    and isinstance(loop.body[0].value.args[0], ast.Name) and loop.body[0].value.args[0].id == loop.target.id
                    and not loop.body[0].value.keywords and isinstance(returned, ast.Return)
                    and isinstance(returned.value, ast.Name) and returned.value.id == seed.targets[0].id):
                continue
            if len({collection.targets[0].id, seed.targets[0].id, loop.target.id}) != 3:
                continue
            # Require real static binding; a parameter/alias with the same name is not self-recursion.
            if (entity.scope_id,entity.id,loop.body[0].lineno) not in self_calls:
                continue
            empty, initial = base.body[0].value.value, seed.value.value
            facts[entity.id] = {'entity_id':entity.id, 'name':entity.name, 'kind':'recursive_fold', 'empty_result':empty,
                'nonempty_seed':initial, 'recurrence':f'无子项时返回 {empty}；有子项时返回 {initial} + 各子项递归结果之和。',
                'interpretation':('在无环有限子项树中计数叶项；非叶项不另计数。' if empty == 1 and initial == 0 else
                                  '在无环有限子项树中计数全部项。' if empty == 1 and initial == 1 else
                                  '只确认递推式，不推断计数用途。'),
                'path':path, 'start_line':node.lineno, 'end_line':node.end_lineno}
    return facts


def validate_fold_wording(architecture, facts):
    # Reject known explicit contradictions; absence of a match is NOT semantic acceptance.
    for fact in facts.values():
        if fact['empty_result'] != 1 or fact['nonempty_seed'] != 0:
            continue
        texts = [note.summary+'。'+note.details for note in architecture.explanations if note.entity_id == fact['entity_id']]
        texts.extend(module.summary for module in architecture.modules if fact['entity_id'] in module.entity_ids
                     and not any(facts.get(identifier,{}).get('nonempty_seed') == 1 for identifier in module.entity_ids))
        overview = architecture.overview
        texts.extend(sentence for text in [overview.introduction,overview.principle,*overview.capabilities,*overview.analysis_notes]
                     for sentence in re.split(r'[。；;\n]',text) if fact['name'] in sentence)
        sentences = [sentence for text in texts for sentence in re.split(r'[。；;\n]',text)]
        for sentence in sentences:
            for match in re.finditer(r'(?:全部|所有|总)\s*(?:后代)?节点(?:数量|数)?|包含当前节点(?:在内)?', sentence):
                prefix = sentence[:match.start()]
                if re.search(r'(?:不是|而非|并非|不|不等于|不能称为|不能认为|不计入|未计入)\s*$',prefix):
                    continue
                raise ValueError('递归聚合说明与源码事实冲突：非叶分支从 0 累加子项，没有另加当前项；不得称为全部节点计数。')
