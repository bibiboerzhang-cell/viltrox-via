/**
 * Fail-closed scanner for raw browser evidence.
 *
 * Only stable category names ever leave this module. Matched values and
 * excerpts are deliberately inaccessible to callers and never appear in an
 * exception, which lets the capture gate reject a leak without duplicating it
 * into stderr or CI logs.
 */

const SECRET_PATTERNS = Object.freeze([
  ["private_key", /-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----/iu],
  ["authorization", /\b(?:authorization\s*[:=]\s*["']?)?(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{12,}/iu],
  ["jwt", /\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b/u],
  ["url_credentials", /\b[a-z][a-z0-9+.-]*:\/\/[^\s/:@]+:[^\s/@]+@/iu],
  [
    "database_dsn",
    /\b(?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|redis|rediss|amqp|amqps):(?:\/\/|%3A%2F%2F)/iu,
  ],
  [
    "provider_key",
    /\b(?:sk-(?:ant-|proj-)?[A-Za-z0-9_-]{12,}|AIza[A-Za-z0-9_-]{20,}|(?:gh[pousr]|github_pat)_[A-Za-z0-9_]{16,}|xox[baprs]-[A-Za-z0-9-]{12,}|apify_api_[A-Za-z0-9_-]{12,}|(?:AKIA|ASIA)[A-Z0-9]{16})\b/u,
  ],
  [
    "signed_url",
    /(?:[?&]|%3F|%26)(?:x-amz-(?:credential|signature)|awsaccesskeyid|signature|sig)\s*(?:=|%3D)[^&#\s"']{8,}/iu,
  ],
  [
    "credential_query",
    /(?:[?&]|%3F|%26)(?:access[_-]?token|refresh[_-]?token|id[_-]?token|api[_-]?key|access[_-]?key(?:[_-]?id)?|client[_-]?secret|secret(?:[_-]?key)?|token)\s*(?:=|%3D)[^&#\s"']{8,}/iu,
  ],
  [
    "credential_assignment",
    /["']?(?:authorization|proxy[_-]?authorization|cookie|set-cookie|password|passwd|client[_-]?secret|secret(?:[_-]?key)?|api[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token|private[_-]?key|aws[_-]?secret[_-]?access[_-]?key|database[_-]?url|redis[_-]?url|dsn)["']?\s*[:=]\s*["']?(?!(?:(?:null|none|true|false|missing|unset|redacted)\b|<redacted>|\*{3,}))[^\s"',}\]]{8,}/iu,
  ],
  [
    "provider_credential_assignment",
    /["']?(?:(?:openai|anthropic|gemini|google|apify|rapidapi|resend|cloudflare|github|gitlab|slack|stripe|aws|azure|gcp|supabase|vercel|sentry|huggingface|replicate|deepseek|groq|mistral|cohere|tavily|serpapi)[_-](?:api[_-]?)?(?:key|token|secret|password)|(?:[a-z][a-z0-9]*[_-]){1,3}api[_-](?:key|token|secret))["']?\s*[:=]\s*["']?(?!(?:(?:null|none|true|false|missing|unset|redacted)\b|<redacted>|\*{3,}))[^\s"',}\]]{8,}/iu,
  ],
]);

function scanTexts(serializedCapture) {
  const text = String(serializedCapture ?? "");
  const texts = [text];
  try {
    const stack = [JSON.parse(text)];
    while (stack.length > 0) {
      const value = stack.pop();
      if (typeof value === "string") texts.push(value);
      else if (Array.isArray(value)) {
        for (const item of value) stack.push(item);
      } else if (value && typeof value === "object") {
        for (const item of Object.values(value)) stack.push(item);
      }
    }
  } catch {
    // The writer supplies JSON.stringify output, but scanning the raw text still
    // fails closed for callers that hand this helper malformed evidence.
  }
  return texts;
}

export function browserCaptureSecretCategories(serializedCapture, knownSecrets = []) {
  const text = String(serializedCapture ?? "");
  const texts = scanTexts(text);
  const categories = new Set();
  for (const secret of knownSecrets) {
    const value = String(secret ?? "");
    if (value && text.includes(value)) categories.add("known_credential");
  }
  for (const [category, pattern] of SECRET_PATTERNS) {
    if (texts.some((candidate) => pattern.test(candidate))) categories.add(category);
  }
  return [...categories].sort();
}

export function assertBrowserCaptureCredentialFree(serializedCapture, knownSecrets = []) {
  if (browserCaptureSecretCategories(serializedCapture, knownSecrets).length > 0) {
    throw new Error("browser_capture_secret_scan_failed");
  }
}
