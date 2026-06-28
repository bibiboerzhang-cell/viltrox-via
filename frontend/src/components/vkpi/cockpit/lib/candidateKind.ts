// Verbatim from vkpi_v6.15.7_integrated.html


import { CANDIDATE_KIND_INFO } from "../data/candidateKindInfo";

export function candidateKindGroup(kind: any) {
  return (CANDIDATE_KIND_INFO as any)[kind]?.group || "existing";
}
