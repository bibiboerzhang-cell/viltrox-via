// Static dictionary composition only. Never import/evaluate source modules.
import fs from "node:fs";
import path from "node:path";
import ts from "typescript";

function propertyName(name) {
  if (ts.isIdentifier(name) || ts.isStringLiteralLike(name) || ts.isNumericLiteral(name)) return name.text;
  if (ts.isComputedPropertyName(name) && (ts.isStringLiteralLike(name.expression) || ts.isNumericLiteral(name.expression))) return name.expression.text;
  return null;
}

function importedObjects(sourceFile) {
  const bindings = new Map();
  for (const statement of sourceFile.statements) {
    if (!ts.isImportDeclaration(statement) || !ts.isStringLiteralLike(statement.moduleSpecifier)
      || statement.importClause?.isTypeOnly) continue;
    const named = statement.importClause?.namedBindings;
    if (!named || !ts.isNamedImports(named)) continue;
    for (const element of named.elements) {
      if (!element.isTypeOnly) bindings.set(element.name.text, {
        module: statement.moduleSpecifier.text, name: element.propertyName?.text ?? element.name.text,
      });
    }
  }
  return bindings;
}

function readObject(frontendRoot, relativePath, variableName, errors, ancestors = new Set(), imported = false) {
  const identity = `${relativePath}#${variableName}`;
  if (ancestors.has(identity) || ancestors.size >= 16) {
    errors.push(`${identity}: cyclic or excessive dictionary composition`);
    return new Map();
  }
  const chain = new Set(ancestors).add(identity);
  const filePath = path.join(frontendRoot, relativePath);
  let source;
  try { source = fs.readFileSync(filePath, "utf8"); }
  catch { errors.push(`${relativePath}: cannot read dictionary module`); return new Map(); }
  const sourceFile = ts.createSourceFile(filePath, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  if (sourceFile.parseDiagnostics.length) {
    errors.push(`${relativePath}: invalid dictionary module syntax`);
    return new Map();
  }
  const definitions = [];
  for (const statement of sourceFile.statements) {
    if (!ts.isVariableStatement(statement)) continue;
    for (const declaration of statement.declarationList.declarations) {
      if (ts.isIdentifier(declaration.name) && declaration.name.text === variableName) {
        definitions.push({ declaration, exported: statement.modifiers?.some(modifier => modifier.kind === ts.SyntaxKind.ExportKeyword) });
      }
    }
  }
  if (definitions.length !== 1) {
    errors.push(`${relativePath}: expected exactly one ${variableName} definition, found ${definitions.length}`);
    return new Map();
  }
  const { declaration, exported } = definitions[0];
  if (imported && !exported) {
    errors.push(`${relativePath}: imported ${variableName} must be directly exported`);
    return new Map();
  }
  if (!declaration.initializer || !ts.isObjectLiteralExpression(declaration.initializer)) {
    errors.push(`${relativePath}: ${variableName} must be an object literal`);
    return new Map();
  }
  const imports = importedObjects(sourceFile);
  const entries = new Map();
  const add = (key, entry) => {
    if (entries.has(key)) errors.push(`${entry.location}: duplicate ${variableName} key ${JSON.stringify(key)} (first at ${entries.get(key).location})`);
    else entries.set(key, entry);
  };
  for (const property of declaration.initializer.properties) {
    const line = sourceFile.getLineAndCharacterOfPosition(property.getStart(sourceFile)).line + 1;
    const location = `${relativePath}:${line}`;
    if (ts.isSpreadAssignment(property)) {
      const binding = ts.isIdentifier(property.expression) ? imports.get(property.expression.text) : null;
      // Domain dictionaries are same-directory named imports, not arbitrary JS.
      if (!binding || !/^\.\/[A-Za-z0-9_-]+(?:\.ts)?$/.test(binding.module)) {
        errors.push(`${location}: dictionary spread must name a same-directory imported object`);
        continue;
      }
      const modulePath = path.join(path.dirname(relativePath), binding.module.endsWith(".ts") ? binding.module : `${binding.module}.ts`);
      for (const [key, entry] of readObject(frontendRoot, modulePath, binding.name, errors, chain, true)) add(key, entry);
      continue;
    }
    if (!ts.isPropertyAssignment(property)) {
      errors.push(`${location}: ${variableName} entries must be plain property assignments`);
      continue;
    }
    const key = propertyName(property.name);
    if (key === null) { errors.push(`${location}: ${variableName} has a non-literal key`); continue; }
    if (!ts.isStringLiteralLike(property.initializer)) {
      errors.push(`${location}: ${variableName}[${JSON.stringify(key)}] must be a string literal`);
      continue;
    }
    add(key, { value: property.initializer.text, location });
  }
  return entries;
}

export function readDictionary(frontendRoot, relativePath, variableName, allowedEmpty, allowedBoundaryWhitespace, errors) {
  const entries = readObject(frontendRoot, relativePath, variableName, errors);
  for (const [key, { value, location }] of entries) {
    if (!key) errors.push(`${location}: ${variableName} contains an empty key`);
    if (key !== key.trim()) errors.push(`${location}: ${variableName} key has leading/trailing whitespace: ${JSON.stringify(key)}`);
    if (value !== value.trim() && !allowedBoundaryWhitespace.has(key)) errors.push(`${location}: ${variableName}[${JSON.stringify(key)}] has leading/trailing whitespace`);
    if (value.trim() === "" && !allowedEmpty.has(key)) errors.push(`${location}: ${variableName}[${JSON.stringify(key)}] is empty and is not allowlisted`);
  }
  for (const key of allowedEmpty) {
    const entry = entries.get(key);
    if (!entry) errors.push(`${variableName} empty-value allowlist references an unknown key: ${JSON.stringify(key)}`);
    else if (entry.value.trim() !== "") errors.push(`${variableName} empty-value allowlist is stale for non-empty key: ${JSON.stringify(key)}`);
  }
  for (const key of allowedBoundaryWhitespace) {
    const entry = entries.get(key);
    if (!entry) errors.push(`${variableName} boundary-whitespace allowlist references an unknown key: ${JSON.stringify(key)}`);
    else if (entry.value === entry.value.trim()) errors.push(`${variableName} boundary-whitespace allowlist is stale for trimmed key: ${JSON.stringify(key)}`);
  }
  return entries;
}
