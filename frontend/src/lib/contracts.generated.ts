/* eslint-disable */
/* This file is generated from shared/contracts.json. Do not edit manually. */

export const ACTOR_TIERS = {
  anonymous: "anonymous",
  authenticated: "authenticated",
  creator: "creator",
  verified_creator: "verified_creator",
  student: "student",
  admin: "admin",
  vip_platinum: "vip_platinum",
  founder_internal: "founder_internal",
} as const;

export type ActorTierKey = (typeof ACTOR_TIERS)[keyof typeof ACTOR_TIERS];

export const ROLE_KEYS = ["admin", "creator", "founder", "internal", "student"] as const;
export type RoleKey = (typeof ROLE_KEYS)[number];

export const SURFACE_KEYS = ["upload", "account", "redeem", "student", "admin", "review", "affiliate", "analysis", "submission"] as const;
export type SurfaceKey = (typeof SURFACE_KEYS)[number];

export const DEPRECATED_CONTRACT_LITERALS = ["verified creator", "VIP/Platinum", "founder/internal"] as const;
