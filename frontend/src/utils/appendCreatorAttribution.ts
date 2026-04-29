export function appendCreatorAttribution(targetUrl: string, creatorCode: string): string {
  const cleanTarget = String(targetUrl || "").trim();
  const cleanCode = String(creatorCode || "").trim();
  if (!cleanTarget || !cleanCode) return cleanTarget;

  const hashIndex = cleanTarget.indexOf("#");
  const hash = hashIndex >= 0 ? cleanTarget.slice(hashIndex) : "";
  const base = hashIndex >= 0 ? cleanTarget.slice(0, hashIndex) : cleanTarget;
  const separator = base.includes("?") ? "&" : "?";
  return `${base}${separator}creator_id=${encodeURIComponent(cleanCode)}${hash}`;
}
