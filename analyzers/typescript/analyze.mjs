import ts from 'typescript';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';

const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve(input.root);
const libraryRoot = path.dirname(ts.getDefaultLibFilePath({}));
const inside = (parent, file) => {
  const relative = path.relative(parent, path.resolve(file));
  return relative === '' || (!relative.startsWith('..') && !path.isAbsolute(relative));
};
const host = ts.createCompilerHost({});
const originalRead = host.readFile.bind(host);
const originalExists = host.fileExists.bind(host);
const allowed = file => inside(root, file) || inside(libraryRoot, file);
host.readFile = file => allowed(file) ? originalRead(file) : undefined;
host.fileExists = file => allowed(file) && originalExists(file);
host.directoryExists = file => allowed(file) && fs.existsSync(file) && fs.statSync(file).isDirectory();
const program = ts.createProgram(input.paths.map(file => path.join(root, file)), {
  target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext, moduleResolution: ts.ModuleResolutionKind.Bundler,
  noEmit: true, skipLibCheck: true, strict: true, types: [], noResolve: false,
}, host);
const checker = program.getTypeChecker();
const mutated = new Set();
for (const source of program.getSourceFiles()) {
  if (!inside(root, source.fileName) || source.isDeclarationFile) continue;
  function bindings(node) {
    if (ts.isBinaryExpression(node) && node.operatorToken.kind >= ts.SyntaxKind.FirstAssignment && node.operatorToken.kind <= ts.SyntaxKind.LastAssignment) {
      mutated.add(checker.getSymbolAtLocation(node.left));
    }
    if ((ts.isPrefixUnaryExpression(node) || ts.isPostfixUnaryExpression(node)) &&
        [ts.SyntaxKind.PlusPlusToken, ts.SyntaxKind.MinusMinusToken].includes(node.operator)) {
      mutated.add(checker.getSymbolAtLocation(node.operand));
    }
    ts.forEachChild(node, bindings);
  }
  bindings(source);
}
const result = {entities: [], scopes: [], calls: [], diagnostics: [], file_hashes: {}, readable_paths: [], tools: {typescript: `compiler-api/${ts.version}; function-variables/2`}};
const ids = new Map();
const calls = [];
const makeId = (...items) => crypto.createHash('sha256').update([input.version, ...items].join(':')).digest('hex').slice(0, 32);
function location(node, source) {
  const begin = node.getStart(source), end = node.getEnd();
  return {
    path: path.relative(root, source.fileName).replaceAll('\\', '/'),
    start_line: source.getLineAndCharacterOfPosition(begin).line + 1,
    end_line: source.getLineAndCharacterOfPosition(end).line + 1,
    start_byte: Buffer.byteLength(source.text.slice(0, begin)), end_byte: Buffer.byteLength(source.text.slice(0, end)),
  };
}
const isFunction = node => ts.isFunctionDeclaration(node) || ts.isMethodDeclaration(node) || ts.isConstructorDeclaration(node)
  || ts.isArrowFunction(node) || ts.isFunctionExpression(node) || ts.isGetAccessorDeclaration(node) || ts.isSetAccessorDeclaration(node);
// AST structure preserves ASI, literals, JSX text and semantic line breaks;
// whitespace and ordinary comments are not part of the implementation identity.
function semanticTree(node, source, signatureOnly = false) {
  const children = [];
  ts.forEachChild(node, child => {
    if (!signatureOnly || child !== node.body) children.push(semanticTree(child, source));
  });
  return [node.kind, children.length ? children : node.getText(source),
    (node.jsDoc || []).map(doc => doc.getText(source))];
}
for (const source of program.getSourceFiles()) {
  if (!inside(root, source.fileName) || source.isDeclarationFile) continue;
  const relative = path.relative(root, source.fileName).replaceAll('\\', '/');
  const rootId = makeId(relative, 'module');
  result.scopes.push({id: rootId, kind: 'module', name: relative, location: {
    path: relative, start_line: 1, end_line: source.getLineAndCharacterOfPosition(source.text.length).line + 1,
    start_byte: 0, end_byte: Buffer.byteLength(source.text),
  }});
  function visit(node, scopeId, names, nested) {
    if (isFunction(node) && node.body) {
      const name = node.name?.getText(source) || (ts.isConstructorDeclaration(node) ? 'constructor' : '<anonymous>');
      const entityId = makeId(relative, 'entity', node.pos);
      const childScope = makeId(entityId, 'scope');
      const anonymous = ts.isArrowFunction(node) || (ts.isFunctionExpression(node) && !node.name);
      const hidden = anonymous || nested || ts.isConstructorDeclaration(node);
      const signature = node.getText(source).slice(0, node.body.getStart(source) - node.getStart(source)).trim();
      const entity = {id: entityId, name, qualified_name: [...names, name].join('.'), signature,
        signature_key: JSON.stringify(semanticTree(node, source, true)),
        language: 'typescript', location: location(node, source), scope_id: childScope, visible: !hidden,
        hidden_reason: anonymous ? '匿名函数' : nested ? '嵌套局部函数' : hidden ? '构造函数' : null,
        implementation_hash: crypto.createHash('sha256').update(JSON.stringify(semanticTree(node, source))).digest('hex'),
        unresolved_count: 0};
      ids.set(node, entity);
      result.entities.push(entity);
      result.scopes.push({id: childScope, kind: anonymous ? 'lambda' : 'function', name: entity.qualified_name,
        parent_id: scopeId, location: location(node, source)});
      visit(node.body, childScope, [...names, name], true);
      return;
    }
    if (ts.isClassDeclaration(node)) names = [...names, node.name?.text || '<class>'];
    if (ts.isCallExpression(node)) {
      const call = {id: makeId(relative, 'call', node.pos), caller_scope_id: scopeId,
        expression: node.expression.getText(source), location: location(node, source), resolution: 'unresolved',
        origin: 'static', reason: '目标缺少可确认的具体实现。'};
      const declaration = checker.getResolvedSignature(node)?.declaration;
      calls.push([call, declaration, node]);
      result.calls.push(call);
    }
    ts.forEachChild(node, child => visit(child, scopeId, names, nested));
  }
  visit(source, rootId, [], false);
  result.file_hashes[relative] = crypto.createHash('sha256').update(JSON.stringify(semanticTree(source, source))).digest('hex');
  if (source.statements.length) result.readable_paths.push(relative);
  for (const diagnostic of program.getSyntacticDiagnostics(source)) result.diagnostics.push({
    category: 'parse_failed', path: relative,
    line: source.getLineAndCharacterOfPosition(diagnostic.start || 0).line + 1,
    message: ts.flattenDiagnosticMessageText(diagnostic.messageText, ' '),
  });
}
for (const [call, resolvedDeclaration, node] of calls) {
  let declaration = resolvedDeclaration;
  const propertyCall = ts.isPropertyAccessExpression(node.expression);
  let symbol = checker.getSymbolAtLocation(propertyCall ? node.expression.name : node.expression);
  if (symbol && symbol.flags & ts.SymbolFlags.Alias) symbol = checker.getAliasedSymbol(symbol);
  const implementations = symbol?.declarations?.filter(item => isFunction(item) && item.body) || [];
  if (implementations.length === 1) declaration = implementations[0];
  const target = ids.get(declaration);
  const hasDecorator = declaration && ts.canHaveDecorators(declaration) && (ts.getDecorators(declaration)?.length || 0) > 0;
  const dynamicMember = declaration && ts.isMethodDeclaration(declaration)
    && !declaration.modifiers?.some(modifier => modifier.kind === ts.SyntaxKind.StaticKeyword);
  const directBinding = !mutated.has(symbol) && symbol?.declarations?.some(item => ts.isFunctionDeclaration(item) || ts.isMethodDeclaration(item));
  const functionVariable = symbol?.declarations?.some(item => {
    if (!ts.isVariableDeclaration(item) || !inside(root, item.getSourceFile().fileName)) return false;
    let initializer = item.initializer;
    while (initializer && (ts.isParenthesizedExpression(initializer) || ts.isAsExpression(initializer)
        || ts.isTypeAssertionExpression(initializer) || ts.isSatisfiesExpression(initializer))) initializer = initializer.expression;
    return initializer && (ts.isArrowFunction(initializer) || ts.isFunctionExpression(initializer));
  });
  if (functionVariable || target && !directBinding) {
    call.resolution = 'excluded_indirect'; call.reason = '变量或参数持有的函数不作为直接调用。';
  } else if (target && target.visible && !hasDecorator && !dynamicMember && directBinding &&
      (ts.isFunctionDeclaration(declaration) || propertyCall && ts.isMethodDeclaration(declaration))) {
    call.target_id = target.id; call.target_name = target.qualified_name; call.resolution = 'confirmed';
    call.reason = 'Compiler API 指向唯一函数实现；不是已知动态成员或回调。';
  } else if (target && !target.visible) {
    call.resolution = 'excluded_indirect'; call.reason = '匿名/局部作用域调用按 P0 规则保留，不能提升到外层函数。';
  } else if (declaration && !inside(root, declaration.getSourceFile().fileName)) {
    call.resolution = 'external'; call.reason = '实现不属于当前分析范围。';
  } else if (dynamicMember || hasDecorator) {
    call.reason = '成员动态分派或装饰器语义未确认。';
  }
}
for (const entity of result.entities) entity.unresolved_count = result.calls.filter(call =>
  call.caller_scope_id === entity.scope_id && ['unresolved', 'excluded_indirect'].includes(call.resolution)).length;
process.stdout.write(JSON.stringify(result));
