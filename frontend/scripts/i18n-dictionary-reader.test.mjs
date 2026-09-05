import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { readDictionary } from "./i18n-dictionary-reader.mjs";

function fixture(t, files) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "vkpi-i18n-reader-"));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  for (const [name, source] of Object.entries(files)) fs.writeFileSync(path.join(directory, name), source);
  return directory;
}
function read(root, options = {}) {
  const errors = [];
  const entries = readDictionary(root, "root.ts", "I18N_EN", options.empty ?? new Set(), options.whitespace ?? new Set(), errors);
  return { entries, errors };
}

test("named same-directory module spreads preserve ordered entries and source locations", t => {
  const root = fixture(t, {
    "root.ts": 'import { PART as PLAN } from "./part"; export const I18N_EN = { before: "Before", ...PLAN, after: "After" };',
    "part.ts": 'export const PART = { plan: "Plan", ["evidence"]: "Evidence" };',
  });
  const { entries, errors } = read(root);
  assert.deepEqual(errors, []);
  assert.deepEqual([...entries].map(([key, value]) => [key, value.value]), [["before", "Before"], ["plan", "Plan"], ["evidence", "Evidence"], ["after", "After"]]);
  assert.equal(entries.get("plan").location, "part.ts:1");
});

test("imported duplicates cannot bypass the original duplicate-key guard", t => {
  const root = fixture(t, {
    "root.ts": 'import { PART } from "./part"; export const I18N_EN = { duplicate: "First", ...PART, duplicate: "Last" };',
    "part.ts": 'export const PART = { duplicate: "Imported" };',
  });
  const { entries, errors } = read(root);
  assert.equal(entries.get("duplicate").value, "First");
  assert.equal(errors.filter(error => error.includes("duplicate I18N_EN key")).length, 2);
});

test("imported blank/whitespace entries still fail, with source-accurate diagnostics", t => {
  const root = fixture(t, {
    "root.ts": 'import { PART } from "./part"; export const I18N_EN = { ...PART };',
    "part.ts": 'export const PART = { " key ": " value ", empty: "" };',
  });
  const { errors } = read(root);
  assert.equal(errors.length, 3);
  assert(errors.every(error => error.startsWith("part.ts:1:")));
});

test("allowlists validate the final composition and retain stale-entry checks", t => {
  const root = fixture(t, {
    "root.ts": 'import { PART } from "./part"; export const I18N_EN = { ...PART };',
    "part.ts": 'export const PART = { empty: "", padded: " kept " };',
  });
  assert.deepEqual(read(root, { empty: new Set(["empty"]), whitespace: new Set(["padded"]) }).errors, []);
  assert(read(root, { empty: new Set(["missing"]) }).errors.some(error => error.includes("unknown key")));
  assert(read(root, { empty: new Set(["padded"]) }).errors.some(error => error.includes("stale for non-empty")));
});

test("cycles fail without recursion or executing modules", t => {
  const root = fixture(t, {
    "root.ts": 'import { PART } from "./part"; export const I18N_EN = { ...PART };',
    "part.ts": 'import { I18N_EN } from "./root"; throw new Error("must not execute"); export const PART = { ...I18N_EN };',
  });
  assert(read(root).errors.some(error => error.includes("cyclic")));
});

for (const [name, source, message] of [
  ["dynamic spread", 'export const I18N_EN = { ...process.exit(99) };', "same-directory imported object"],
  ["local variable spread", 'const PART = {}; export const I18N_EN = { ...PART };', "same-directory imported object"],
  ["namespace import", 'import * as PART from "./part"; export const I18N_EN = { ...PART };', "same-directory imported object"],
  ["outside import", 'import { PART } from "../outside"; export const I18N_EN = { ...PART };', "same-directory imported object"],
  ["computed value", 'export const I18N_EN = { key: process.exit(99) };', "must be a string literal"],
  ["computed key", 'export const I18N_EN = { [process.exit(99)]: "Value" };', "non-literal key"],
  ["object factory", 'export const I18N_EN = makeDictionary();', "must be an object literal"],
  ["shorthand", 'const key="Value"; export const I18N_EN = { key };', "plain property assignments"],
]) test(`${name} remains rejected without evaluation`, t => {
  const root = fixture(t, { "root.ts": source });
  assert(read(root).errors.some(error => error.includes(message)));
});

test("missing or non-exported imported definitions fail instead of silently dropping translations", t => {
  const root = fixture(t, {
    "root.ts": 'import { PART } from "./part"; export const I18N_EN = { ...PART };',
    "part.ts": 'const PART = { key: "Value" };',
  });
  assert(read(root).errors.some(error => error.includes("must be directly exported")));
  fs.writeFileSync(path.join(root, "part.ts"), 'export const OTHER = { key: "Value" };');
  assert(read(root).errors.some(error => error.includes("expected exactly one PART")));
});

test("the domain split retains the frozen 1354-entry catalog byte-for-byte and in order", () => {
  const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
  const baseline = JSON.parse(fs.readFileSync(path.join(frontendRoot, "scripts/i18n-contract-baseline.json"), "utf8"));
  const errors = [];
  const entries = readDictionary(frontendRoot, "src/components/vkpi/cockpit/data/i18nEn.ts", "I18N_EN",
    new Set(baseline.allowedEmptyTranslations.I18N_EN), new Set(baseline.allowedBoundaryWhitespaceTranslations.I18N_EN), errors);
  assert.deepEqual(errors, []);
  const ordered = [...entries].map(([key, entry]) => [key, entry.value]);
  assert.equal(ordered.length, 1354);
  // Pre-split source snapshot: intentional translation changes must review this golden value.
  assert.equal(createHash("sha256").update(JSON.stringify(ordered)).digest("hex"), "5afb770052419d94af75c32b404554ed06e039270c875007608270042b35d7e7");
  assert.equal(ordered[1237][0], "营销计划准备检查");
});

test("the canonical i18n command runs reader regressions before checking the catalog", () => {
  const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
  const manifest = JSON.parse(fs.readFileSync(path.join(frontendRoot, "package.json"), "utf8"));
  assert.equal(manifest.scripts["check:i18n"], "node --test scripts/i18n-dictionary-reader.test.mjs && node scripts/check-i18n-contract.mjs");
  const canonical = fs.readFileSync(path.resolve(frontendRoot, "../scripts/verify.sh"), "utf8");
  assert(canonical.includes("npm run check:i18n"));
});
