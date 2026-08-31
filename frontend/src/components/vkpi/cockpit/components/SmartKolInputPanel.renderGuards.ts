// 渲染护栏(M2「治卡」):轮询每拍都会 mint 全新的会话/召回对象,即使后端一个字节都没变。
// 这里只提供两件小工具——「内容指纹」与「按内容比对」——让轮询和 memo 能区分
// 「真的来了新数据」与「只是换了个对象地址」。
//
// 口径与红线:
//   * 比对失败一律 fail-open(判为「变了」),宁可多渲染一次,也绝不吞掉真实更新。
//   * 只做纯比较,不缓存业务数据,不替任何展示层决定该显示什么。

/** 内容指纹:同一份 payload 得同一串;无法序列化(循环引用等)时返回 null = 视为已变化。 */
export function stableFingerprint(value: unknown): string | null {
  try {
    return JSON.stringify(value ?? null);
  } catch {
    return null;
  }
}

/** 按内容比对两个值。任一侧无法序列化即判为不等(fail-open,照常重渲染)。 */
export function sameByContent(a: unknown, b: unknown): boolean {
  if (Object.is(a, b)) return true;
  const left = stableFingerprint(a);
  if (left == null) return false;
  const right = stableFingerprint(b);
  return right != null && left === right;
}
