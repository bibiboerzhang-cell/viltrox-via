// 2026-07-03 退役:此前写死 Kevin Chen/kevin@viltrox.com 假身份,泄漏到头像菜单/个人资料/
// 数据归属 id。现仅作"真身份完全拿不到"时的中性兜底 —— 真身份链:VkpiTab 的 user →
// userEmail/userAuthRole props → useCockpitRuntime → normalizeCurrentUser。
export const CURRENT_USER = {
  id: 0, name: "成员", role: "member", email: "",
  avatar: "V", avatarGradient: "linear-gradient(135deg, #64748b, #475569)",
};
