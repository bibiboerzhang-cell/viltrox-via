export type CatPose = "front" | "side" | "back";
export type CatAction =
  | "idle"
  | "talking"
  | "running"
  | "shooting"
  | "filming"
  | "thinking"
  | "carting"
  | "flash"
  | "celebrating"
  | "play";

export interface ViaScenePreset {
  id: string;
  title: string;
  text: string;
  action: CatAction;
  pose: CatPose;
  propLabel: string;
  accentLabel: string;
  moodLabel: string;
  prompts: string[];
  keywords: string[];
}

export interface ViaMessageLike {
  title?: string;
  text?: string;
  quickActions?: string[];
  behaviorMode?: string;
  sceneHint?: string;
}

export const VIA_SCENE_PLAYBOOK: ViaScenePreset[] = [
  {
    id: "chapman-luna-30-300",
    title: "Via is rolling the cine cart",
    text: "I am padding beside the Chapman dolly with an ARRI body parked up top, checking whether the LUNA 30-300mm should stay dressed for the next move.",
    action: "carting",
    pose: "front",
    propLabel: "Chapman + LUNA 30-300",
    accentLabel: "Cine cart",
    moodLabel: "calm focus",
    prompts: ["Build a Chapman + LUNA 30-300 rig", "What does the 30-300 feel like?", "Which Viltrox cine lens goes first?"],
    keywords: ["chapman", "dolly", "arri", "luna", "30-300", "cine", "cinema", "cart"],
  },
  {
    id: "chapman-luna-42-420",
    title: "Via is guarding the long zoom",
    text: "I am watching the long end of the cart, tail wrapped around the wheels, while the LUNA 42-420mm waits for the big telephoto setup.",
    action: "filming",
    pose: "front",
    propLabel: "LUNA 42-420",
    accentLabel: "Long zoom",
    moodLabel: "big glass energy",
    prompts: ["Show me the 42-420 setup", "Why use LUNA 42-420?", "What body fits the long zoom?"],
    keywords: ["42-420", "420", "long zoom", "telephoto", "luna 42", "cine gear"],
  },
  {
    id: "mini-lf-epic",
    title: "Via is checking playback",
    text: "I am peeking over the monitor while the Mini LF and EPIC pairing settles into a cleaner, wider, more cinematic rhythm.",
    action: "filming",
    pose: "front",
    propLabel: "Mini LF + EPIC",
    accentLabel: "Anamorphic lane",
    moodLabel: "cinematic",
    prompts: ["Tell me about EPIC focal lengths", "Build a Mini LF + EPIC look", "What is the EPIC personality?"],
    keywords: ["epic", "mini lf", "anamorphic", "pl", "t2.0", "cine lens"],
  },
  {
    id: "sony-z1-z2",
    title: "Via is firing test frames",
    text: "I have a Sony body in paw, the Z1 and Z2 flashes sparking nearby, and I am snapping quick portraits to judge the punch and falloff.",
    action: "flash",
    pose: "front",
    propLabel: "Sony + Z1 / Z2",
    accentLabel: "Flash test",
    moodLabel: "sparkly",
    prompts: ["Show me a Sony + Z1 kit", "What can Z1 and Z2 do?", "Which Viltrox lens suits flash portraits?"],
    keywords: ["sony", "z1", "z2", "flash", "strobe", "portrait", "lighting"],
  },
  {
    id: "sony-air",
    title: "Via is packing the travel kit",
    text: "I am matching a Sony body with the lightest Air primes so the bag stays tiny but the shot list still feels ambitious.",
    action: "shooting",
    pose: "front",
    propLabel: "Sony Air kit",
    accentLabel: "Travel light",
    moodLabel: "nimble",
    prompts: ["Build me a Sony Air kit", "Which Air lens is cheapest?", "What works for student shooting?"],
    keywords: ["sony e", "fe", "air", "travel", "student", "budget", "20mm", "40mm", "50mm"],
  },
  {
    id: "fujifilm-air",
    title: "Via is roaming with Fujifilm",
    text: "I am trotting with a Fujifilm body and the smaller Air lenses, looking for that light-on-feet street and travel balance.",
    action: "play",
    pose: "front",
    propLabel: "Fujifilm + Air",
    accentLabel: "Street walk",
    moodLabel: "curious",
    prompts: ["Which Viltrox lens fits Fujifilm?", "Build a Fujifilm street kit", "What is the lightest Fuji setup?"],
    keywords: ["fujifilm", "fuji", "xf", "x mount", "street", "travel", "56mm f1.7", "35mm f1.7"],
  },
  {
    id: "lab-portrait",
    title: "Via is polishing the hero frame",
    text: "I am leaning into the monitor with the LAB glass on deck, checking for richer separation, bolder detail, and that polished premium finish.",
    action: "thinking",
    pose: "front",
    propLabel: "LAB portrait",
    accentLabel: "Premium prime",
    moodLabel: "precise",
    prompts: ["Show me the LAB line", "Is LAB worth it?", "Which LAB lens fits portraits?"],
    keywords: ["lab", "premium", "hero", "flagship", "portrait", "135", "135mm"],
  },
  {
    id: "pro-prime",
    title: "Via is lining up the pro prime",
    text: "I am checking the PRO prime against the scene marks, making sure the look stays serious, fast, and clean without losing agility.",
    action: "shooting",
    pose: "front",
    propLabel: "PRO prime",
    accentLabel: "Fast glass",
    moodLabel: "confident",
    prompts: ["Tell me about the PRO series", "What is the PRO look?", "Which PRO lens fits Sony?"],
    keywords: ["pro", "f1.4", "50mm", "85mm", "professional", "fast prime"],
  },
  {
    id: "evo-roaming",
    title: "Via is scouting the set",
    text: "I am pacing the room with the EVO lens, looking for a smoother everyday setup that still feels sleek enough for polished creator work.",
    action: "running",
    pose: "front",
    propLabel: "EVO walkaround",
    accentLabel: "Everyday rig",
    moodLabel: "easygoing",
    prompts: ["What is the EVO series like?", "Give me an EVO setup", "Compare EVO to Air or PRO"],
    keywords: ["evo", "walkaround", "daily", "compact", "85mm f2.0"],
  },
  {
    id: "via-play-bench",
    title: "Via is playing with the gear",
    text: "I am batting a lens cap across the floor, hopping onto the cart rail, and pretending I run the whole stage while the camera team laughs.",
    action: "celebrating",
    pose: "front",
    propLabel: "Set play",
    accentLabel: "Mascot mode",
    moodLabel: "mischievous",
    prompts: ["Show me the fun side of Via", "What can Via help with?", "Give me a playful product intro"],
    keywords: ["fun", "play", "cute", "cat", "mascot", "haha", "哈哈", "宠物"],
  },
];

export const DEFAULT_VIA_SCENE = VIA_SCENE_PLAYBOOK[0];

function _sceneScore(scene: ViaScenePreset, haystack: string): number {
  return scene.keywords.reduce((score, keyword) => (haystack.includes(keyword) ? score + 1 : score), 0);
}

export function matchViaSceneFromText(text?: string | null): ViaScenePreset | null {
  const haystack = String(text || "").trim().toLowerCase();
  if (!haystack) {
    return null;
  }
  let best: ViaScenePreset | null = null;
  let bestScore = 0;
  for (const scene of VIA_SCENE_PLAYBOOK) {
    const score = _sceneScore(scene, haystack);
    if (score > bestScore) {
      best = scene;
      bestScore = score;
    }
  }
  if (!bestScore) {
    if (haystack.includes("gear")) {
      return VIA_SCENE_PLAYBOOK[6];
    }
    if (haystack.includes("photo") || haystack.includes("摄影")) {
      return VIA_SCENE_PLAYBOOK[3];
    }
    if (haystack.includes("pet") || haystack.includes("猫")) {
      return VIA_SCENE_PLAYBOOK[9];
    }
  }
  return best;
}

export function matchViaSceneFromMessage(message?: ViaMessageLike | null): ViaScenePreset | null {
  if (!message) {
    return null;
  }
  const behaviorMode = String(message.behaviorMode || "").trim().toLowerCase();
  if (behaviorMode === "photography") {
    return VIA_SCENE_PLAYBOOK[3];
  }
  if (behaviorMode === "gear") {
    return VIA_SCENE_PLAYBOOK[6];
  }
  if (behaviorMode === "pet") {
    return VIA_SCENE_PLAYBOOK[9];
  }
  return matchViaSceneFromText(
    [message.title, message.text, message.sceneHint, ...(message.quickActions || [])].filter(Boolean).join(" "),
  );
}

export function buildViaPromptDeck(scene: ViaScenePreset, quickActions: string[] = []): string[] {
  const merged = [...quickActions, ...scene.prompts];
  return merged.filter((item, index) => item && merged.indexOf(item) === index).slice(0, 4);
}
