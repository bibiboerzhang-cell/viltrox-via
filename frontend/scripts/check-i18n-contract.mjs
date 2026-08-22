import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(scriptDir, "..");
const cockpitRoot = path.join(frontendRoot, "src/components/vkpi/cockpit");
const defaultBaselinePath = path.join(scriptDir, "i18n-contract-baseline.json");

function usage() {
  return [
    "Usage: node scripts/check-i18n-contract.mjs [--report] [--baseline <path>]",
    "",
    "Checks the Cockpit translation dictionaries and prevents new Chinese",
    "literal t()/translate() keys from silently falling back in English mode.",
  ].join("\n");
}

function parseArgs(argv) {
  let report = false;
  let baselinePath = defaultBaselinePath;
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--report") {
      report = true;
    } else if (arg === "--baseline") {
      const value = argv[index + 1];
      if (!value) throw new Error("--baseline requires a path");
      baselinePath = path.resolve(process.cwd(), value);
      index += 1;
    } else if (arg === "--help" || arg === "-h") {
      process.stdout.write(`${usage()}\n`);
      process.exit(0);
    } else {
      throw new Error(`unknown argument: ${arg}`);
    }
  }
  return { report, baselinePath };
}

function readJson(filePath) {
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch (error) {
    throw new Error(`cannot read baseline ${filePath}: ${error.message}`);
  }
}

function checkedStringArray(value, label, errors) {
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) {
    errors.push(`${label} must be an array of strings`);
    return [];
  }
  const seen = new Set();
  for (const item of value) {
    if (item !== item.trim()) errors.push(`${label} contains a key with leading/trailing whitespace: ${JSON.stringify(item)}`);
    if (seen.has(item)) errors.push(`${label} contains a duplicate: ${JSON.stringify(item)}`);
    seen.add(item);
  }
  return value;
}

function loadBaseline(filePath, errors) {
  const data = readJson(filePath);
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    errors.push("baseline root must be an object");
    return {
      missingEnglishKeys: new Set(),
      allowedEmptyTranslations: new Map(),
      allowedBoundaryWhitespaceTranslations: new Map(),
    };
  }
  if (data.version !== 1) errors.push(`unsupported baseline version: ${JSON.stringify(data.version)}`);

  const missingEnglishKeys = new Set(
    checkedStringArray(data.missingEnglishKeys, "baseline.missingEnglishKeys", errors),
  );
  const allowedEmptyTranslations = new Map();
  const allowedBoundaryWhitespaceTranslations = new Map();
  const rawAllowed = data.allowedEmptyTranslations;
  if (!rawAllowed || typeof rawAllowed !== "object" || Array.isArray(rawAllowed)) {
    errors.push("baseline.allowedEmptyTranslations must be an object");
  } else {
    for (const dictionaryName of ["I18N_ZH", "I18N_EN"]) {
      allowedEmptyTranslations.set(
        dictionaryName,
        new Set(checkedStringArray(
          rawAllowed[dictionaryName],
          `baseline.allowedEmptyTranslations.${dictionaryName}`,
          errors,
        )),
      );
    }
  }
  const rawBoundaryWhitespace = data.allowedBoundaryWhitespaceTranslations;
  if (
    !rawBoundaryWhitespace
    || typeof rawBoundaryWhitespace !== "object"
    || Array.isArray(rawBoundaryWhitespace)
  ) {
    errors.push("baseline.allowedBoundaryWhitespaceTranslations must be an object");
  } else {
    for (const dictionaryName of ["I18N_ZH", "I18N_EN"]) {
      allowedBoundaryWhitespaceTranslations.set(
        dictionaryName,
        new Set(checkedStringArray(
          rawBoundaryWhitespace[dictionaryName],
          `baseline.allowedBoundaryWhitespaceTranslations.${dictionaryName}`,
          errors,
        )),
      );
    }
  }
  return {
    missingEnglishKeys,
    allowedEmptyTranslations,
    allowedBoundaryWhitespaceTranslations,
  };
}

function sourceKind(filePath) {
  return filePath.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
}

function literalPropertyName(name, sourceFile) {
  if (ts.isIdentifier(name) || ts.isStringLiteralLike(name) || ts.isNumericLiteral(name)) {
    return name.text;
  }
  if (
    ts.isComputedPropertyName(name)
    && (ts.isStringLiteralLike(name.expression) || ts.isNumericLiteral(name.expression))
  ) {
    return name.expression.text;
  }
  return null;
}

function readDictionary(relativePath, variableName, allowedEmpty, allowedBoundaryWhitespace, errors) {
  const filePath = path.join(frontendRoot, relativePath);
  const source = fs.readFileSync(filePath, "utf8");
  const sourceFile = ts.createSourceFile(
    filePath,
    source,
    ts.ScriptTarget.Latest,
    true,
    sourceKind(filePath),
  );
  const definitions = [];

  function visit(node) {
    if (
      ts.isVariableDeclaration(node)
      && ts.isIdentifier(node.name)
      && node.name.text === variableName
    ) {
      definitions.push(node);
    }
    ts.forEachChild(node, visit);
  }
  visit(sourceFile);

  if (definitions.length !== 1) {
    errors.push(`${relativePath}: expected exactly one ${variableName} definition, found ${definitions.length}`);
    return new Map();
  }

  const initializer = definitions[0].initializer;
  if (!initializer || !ts.isObjectLiteralExpression(initializer)) {
    errors.push(`${relativePath}: ${variableName} must be an object literal`);
    return new Map();
  }

  const entries = new Map();
  for (const property of initializer.properties) {
    const position = sourceFile.getLineAndCharacterOfPosition(property.getStart(sourceFile));
    const location = `${relativePath}:${position.line + 1}`;
    if (!ts.isPropertyAssignment(property)) {
      errors.push(`${location}: ${variableName} entries must be plain property assignments`);
      continue;
    }
    const key = literalPropertyName(property.name, sourceFile);
    if (key === null) {
      errors.push(`${location}: ${variableName} has a non-literal key`);
      continue;
    }
    if (!ts.isStringLiteralLike(property.initializer)) {
      errors.push(`${location}: ${variableName}[${JSON.stringify(key)}] must be a string literal`);
      continue;
    }
    const value = property.initializer.text;
    if (!key) errors.push(`${location}: ${variableName} contains an empty key`);
    if (key !== key.trim()) {
      errors.push(`${location}: ${variableName} key has leading/trailing whitespace: ${JSON.stringify(key)}`);
    }
    if (value !== value.trim() && !allowedBoundaryWhitespace.has(key)) {
      errors.push(`${location}: ${variableName}[${JSON.stringify(key)}] has leading/trailing whitespace`);
    }
    if (value.trim() === "" && !allowedEmpty.has(key)) {
      errors.push(`${location}: ${variableName}[${JSON.stringify(key)}] is empty and is not allowlisted`);
    }
    if (entries.has(key)) {
      errors.push(
        `${location}: duplicate ${variableName} key ${JSON.stringify(key)} (first at ${entries.get(key).location})`,
      );
      continue;
    }
    entries.set(key, { value, location });
  }

  for (const key of allowedEmpty) {
    const entry = entries.get(key);
    if (!entry) {
      errors.push(`${variableName} empty-value allowlist references an unknown key: ${JSON.stringify(key)}`);
    } else if (entry.value.trim() !== "") {
      errors.push(`${variableName} empty-value allowlist is stale for non-empty key: ${JSON.stringify(key)}`);
    }
  }
  for (const key of allowedBoundaryWhitespace) {
    const entry = entries.get(key);
    if (!entry) {
      errors.push(`${variableName} boundary-whitespace allowlist references an unknown key: ${JSON.stringify(key)}`);
    } else if (entry.value === entry.value.trim()) {
      errors.push(`${variableName} boundary-whitespace allowlist is stale for trimmed key: ${JSON.stringify(key)}`);
    }
  }
  return entries;
}

function productionSourceFiles(rootPath) {
  const output = [];
  function walk(currentPath) {
    for (const entry of fs.readdirSync(currentPath, { withFileTypes: true })) {
      const filePath = path.join(currentPath, entry.name);
      if (entry.isDirectory()) {
        if (entry.name !== "__tests__") walk(filePath);
        continue;
      }
      if (!entry.isFile() || !/\.tsx?$/.test(entry.name)) continue;
      if (/\.(?:test|spec|stories)\.[cm]?[jt]sx?$/.test(entry.name)) continue;
      output.push(filePath);
    }
  }
  walk(rootPath);
  return output.sort();
}

function collectTranslatedChineseKeys(files) {
  const occurrences = new Map();
  let literalCalls = 0;
  for (const filePath of files) {
    const source = fs.readFileSync(filePath, "utf8");
    const sourceFile = ts.createSourceFile(
      filePath,
      source,
      ts.ScriptTarget.Latest,
      true,
      sourceKind(filePath),
    );
    function visit(node) {
      if (
        ts.isCallExpression(node)
        && ts.isIdentifier(node.expression)
        && (node.expression.text === "t" || node.expression.text === "translate")
        && node.arguments.length > 0
        && ts.isStringLiteralLike(node.arguments[0])
      ) {
        literalCalls += 1;
        const key = node.arguments[0].text;
        if (/\p{Script=Han}/u.test(key)) {
          const position = sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile));
          const location = `${path.relative(frontendRoot, filePath).split(path.sep).join("/")}:${position.line + 1}`;
          const rows = occurrences.get(key) ?? [];
          rows.push(location);
          occurrences.set(key, rows);
        }
      }
      ts.forEachChild(node, visit);
    }
    visit(sourceFile);
  }
  return { occurrences, literalCalls };
}

function printDetailedReport({
  baselinePath,
  dictionaries,
  sourceFileCount,
  literalCalls,
  chineseKeyCount,
  missingKeys,
  baselineDebt,
  resolvedKeys,
  occurrences,
}) {
  process.stdout.write(`baseline: ${path.relative(frontendRoot, baselinePath)}\n`);
  process.stdout.write(
    `dictionaries: I18N_ZH=${dictionaries.I18N_ZH.size}, I18N_EN=${dictionaries.I18N_EN.size}\n`,
  );
  process.stdout.write(
    `scan: ${sourceFileCount} production files, ${literalCalls} literal t()/translate() calls, ${chineseKeyCount} unique Chinese keys\n`,
  );
  process.stdout.write(
    `English translation debt: current=${missingKeys.length}, baseline=${baselineDebt.size}, resolved=${resolvedKeys.length}\n`,
  );
  if (missingKeys.length > 0) {
    process.stdout.write("current missing English keys:\n");
    for (const key of missingKeys) {
      const locations = occurrences.get(key) ?? [];
      process.stdout.write(`  - ${JSON.stringify(key)} (${locations.join(", ")})\n`);
    }
  }
  if (resolvedKeys.length > 0) {
    process.stdout.write("resolved baseline keys (remove them from the baseline to lock in the reduction):\n");
    for (const key of resolvedKeys) process.stdout.write(`  - ${JSON.stringify(key)}\n`);
  }
}

function main() {
  const cli = parseArgs(process.argv.slice(2));
  const errors = [];
  const baseline = loadBaseline(cli.baselinePath, errors);
  const dictionaries = {
    I18N_ZH: readDictionary(
      "src/components/vkpi/cockpit/data/i18nZh.ts",
      "I18N_ZH",
      baseline.allowedEmptyTranslations.get("I18N_ZH") ?? new Set(),
      baseline.allowedBoundaryWhitespaceTranslations.get("I18N_ZH") ?? new Set(),
      errors,
    ),
    I18N_EN: readDictionary(
      "src/components/vkpi/cockpit/data/i18nEn.ts",
      "I18N_EN",
      baseline.allowedEmptyTranslations.get("I18N_EN") ?? new Set(),
      baseline.allowedBoundaryWhitespaceTranslations.get("I18N_EN") ?? new Set(),
      errors,
    ),
  };

  const files = productionSourceFiles(cockpitRoot);
  const { occurrences, literalCalls } = collectTranslatedChineseKeys(files);
  const missingKeys = [...occurrences.keys()]
    .filter((key) => !dictionaries.I18N_EN.has(key))
    .sort((left, right) => left.localeCompare(right, "zh-CN"));
  const newMissingKeys = missingKeys.filter((key) => !baseline.missingEnglishKeys.has(key));
  const resolvedKeys = [...baseline.missingEnglishKeys]
    .filter((key) => !missingKeys.includes(key))
    .sort((left, right) => left.localeCompare(right, "zh-CN"));

  for (const key of newMissingKeys) {
    errors.push(
      `new Chinese translation key lacks I18N_EN: ${JSON.stringify(key)} (${(occurrences.get(key) ?? []).join(", ")})`,
    );
  }

  if (cli.report) {
    printDetailedReport({
      baselinePath: cli.baselinePath,
      dictionaries,
      sourceFileCount: files.length,
      literalCalls,
      chineseKeyCount: occurrences.size,
      missingKeys,
      baselineDebt: baseline.missingEnglishKeys,
      resolvedKeys,
      occurrences,
    });
  }

  if (errors.length > 0) {
    process.stderr.write(`i18n contract FAIL (${errors.length} issue${errors.length === 1 ? "" : "s"}):\n`);
    for (const error of errors) process.stderr.write(`  - ${error}\n`);
    process.exitCode = 1;
    return;
  }

  process.stdout.write(
    `i18n contract PASS: ${files.length} files, ${dictionaries.I18N_ZH.size}/${dictionaries.I18N_EN.size} zh/en entries, `
      + `${missingKeys.length}/${baseline.missingEnglishKeys.size} baseline English gaps, 0 new\n`,
  );
}

try {
  main();
} catch (error) {
  process.stderr.write(`i18n contract FAIL: ${error instanceof Error ? error.message : String(error)}\n`);
  process.exitCode = 1;
}
