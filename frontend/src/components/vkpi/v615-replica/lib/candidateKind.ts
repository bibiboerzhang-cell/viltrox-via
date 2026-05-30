// @ts-nocheck
// Verbatim from vkpi_v6.15.7_integrated.html


import React from "react";
import { CANDIDATE_KIND_INFO } from "../data/candidateKindInfo";

const e = React.createElement;

export function candidateKindGroup(kind) {
  return CANDIDATE_KIND_INFO[kind]?.group || "existing";
}
