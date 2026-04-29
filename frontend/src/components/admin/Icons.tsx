/**
 * V-OS Admin — Icons
 * Centralised Lucide imports. Use via <Icons.layout /> etc.
 * Keep strokeWidth consistent (2) for visual harmony.
 */
import {
  LayoutGrid,
  Gavel,
  Users,
  Package,
  BarChart3,
  GraduationCap,
  Sparkles,
  Terminal,
  Activity,
  Search,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  ExternalLink,
  Calendar,
  ArrowUp,
  ArrowDown,
  ArrowUpDown,
  X,
  Check,
  Plus,
  Minus,
  Filter,
  MoreHorizontal,
  Menu,
  AlertCircle,
  Download,
  Mail,
  Ban,
  TrendingUp,
  UserCheck,
  Eye,
  Pencil,
  type LucideIcon,
} from "lucide-react";

export const Icons = {
  // Sidebar nav
  layout: LayoutGrid,
  ops: Gavel,
  users: Users,
  products: Package,
  analytics: BarChart3,
  student: GraduationCap,
  via: Sparkles,
  command: Terminal,
  runtime: Activity,

  // Topbar
  search: Search,
  externalLink: ExternalLink,

  // Common
  caret: ChevronDown,
  caretRight: ChevronRight,
  caretUp: ChevronUp,
  calendar: Calendar,
  arrowUp: ArrowUp,
  arrowDown: ArrowDown,
  sort: ArrowUpDown,
  close: X,
  check: Check,
  plus: Plus,
  minus: Minus,
  filter: Filter,
  more: MoreHorizontal,
  menu: Menu,
  alert: AlertCircle,
  download: Download,
  mail: Mail,
  ban: Ban,
  trending: TrendingUp,
  userCheck: UserCheck,
  eye: Eye,
  edit: Pencil,
} as const;

export type IconName = keyof typeof Icons;
export type { LucideIcon };
