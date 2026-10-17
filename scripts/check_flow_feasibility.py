"""可选流程图的受限静态原型；只解析字符串，不执行样例代码。"""
from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path

from codescape.runtime import executable

repository = Path(__file__).resolve().parents[1]


def python_candidate(source):
    tree = ast.parse(source)
    if not all(isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) for item in tree.body):
        return None
    functions = {item.name: item for item in tree.body}
    target = functions['run']
    if (isinstance(target, ast.AsyncFunctionDef) or target.args.args or target.decorator_list
            or len(functions) != len(tree.body)):
        return None
    steps = []
    for statement in target.body:
        if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Call):
            return None
        call = statement.value
        if (not isinstance(call.func, ast.Name) or call.func.id not in functions
                or call.func.id == 'run' or call.args or call.keywords):
            return None
        steps.append(call.func.id)
    return steps


def main():
    python = {
        '直线': 'def run():\n first()\n second()\n',
        '分支': 'def run():\n if flag:\n  first()\n else:\n  second()\n',
        '递归': 'def run():\n run()\n',
        '异步': 'async def run():\n await first()\n',
        '动态调用': 'def run(handler):\n handler()\n',
        '嵌套实参': 'def run():\n second(first())\n',
    }
    py_results = {name: python_candidate('def first(): pass\ndef second(): pass\n' + text)
                  for name, text in python.items()}
    typescript = {
        '直线': 'function run(){first();second();}',
        '分支': 'function run(){if(flag)first();else second();}',
        '递归': 'function run(){run();}',
        '异步': 'async function run(){await first();}',
        '动态调用': 'function run(handler){handler();}',
        '嵌套实参': 'function run(){second(first());}',
    }
    program = r"""
import ts from 'typescript';
let input='';for await(const chunk of process.stdin)input+=chunk;
const output={};
for(const [name,body] of Object.entries(JSON.parse(input))){
 const tree=ts.createSourceFile('owned.ts','function first(){} function second(){} '+body,ts.ScriptTarget.Latest,true);
 const functions=tree.statements.filter(ts.isFunctionDeclaration);
 const run=functions.find(f=>f.name?.text==='run');
 let steps=[];
 if(!run||run.parameters.length||run.modifiers?.some(m=>m.kind===ts.SyntaxKind.AsyncKeyword))steps=null;
 else for(const statement of run.body.statements){
  const call=ts.isExpressionStatement(statement)&&statement.expression;
  if(!call||!ts.isCallExpression(call)||!ts.isIdentifier(call.expression)||call.arguments.length||
    call.expression.text==='run'||!functions.some(f=>f.name?.text===call.expression.text)){steps=null;break;}
  steps.push(call.expression.text);
 }
 output[name]=steps;
}
process.stdout.write(JSON.stringify(output));
"""
    completed = subprocess.run([executable('runtime/node/node.exe', 'node'), '--input-type=module', '-e', program],
        input=json.dumps(typescript).encode(), capture_output=True, cwd=repository, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW)
    assert completed.returncode == 0, 'TypeScript 受限原型检查未通过'
    ts_results = json.loads(completed.stdout)
    for results in [py_results, ts_results]:
        assert results['直线'] == ['first', 'second']
        assert all(value is None for name, value in results.items() if name != '直线')
    report = {'日期': '2026-10-17', '结论': '本轮放弃流程图，不提供生产入口',
        'Python静态原型': py_results, 'TypeScript静态原型': ts_results,
        '边界': '只解析应用自有字符串样例；没有运行被分析代码，没有模型请求。',
        '原因': ['仅窄范围直线调用可形成候选顺序，与已有函数调用图重合，学习收益有限。',
                 '分支、递归、异步、动态绑定与嵌套实参需要控制流和运行语义；当前调用证据不能代替真实执行流程。',
                 '按用户允许的可放弃条款，不以猜测顺序交付，也不留下不可用按钮。']}
    destination = repository / 'docs/reports/p1/流程图可行性验证.json'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Python/TypeScript各六类样例验证通过；流程图本轮不纳入。')


if __name__ == '__main__':
    main()
