export interface VkpiFrontendBuildInfo {
  version: string;
  gitSha: string;
  gitShortSha: string;
  gitBranch: string;
  builtAt: string;
}

declare const __VKPI_BUILD_INFO__: VkpiFrontendBuildInfo | undefined;

const fallbackBuildInfo: VkpiFrontendBuildInfo = {
  version: "0.0.0",
  gitSha: "unknown",
  gitShortSha: "unknown",
  gitBranch: "unknown",
  builtAt: "unknown",
};

export const frontendBuildInfo: VkpiFrontendBuildInfo =
  typeof __VKPI_BUILD_INFO__ !== "undefined" ? __VKPI_BUILD_INFO__ : fallbackBuildInfo;

export function shortBuildSha(value?: string): string {
  const raw = String(value || "").trim();
  if (!raw || raw === "unknown") return "unknown";
  return raw.slice(0, 8);
}
