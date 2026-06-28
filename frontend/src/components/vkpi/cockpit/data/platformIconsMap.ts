// Verbatim from vkpi_v6.15.7_integrated.html

import { Camera, MessageCircle, PlayCircle, RadioTower } from "lucide-react";

export const PLATFORM_ICONS_MAP = {
  YouTube:   { Icon: PlayCircle,   color: "#ef4444" },
  Instagram: { Icon: Camera, color: "#ec4899" },
  Twitter:   { Icon: MessageCircle,   color: "#06b6d4" },
  default:   { Icon: RadioTower, color: "#94a3b8" },
};
