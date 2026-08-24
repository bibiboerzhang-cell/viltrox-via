import { describe, it, expect, vi, beforeEach } from "vitest";

// 波 D·C 车道:myKolSkuPlay-api 对 ../http.apiFetch 的入参断言(锁定契约):
//   GET /api/admin/vkpi/my-kol/sku-play-overview(无额外参数,token 透传)
// data-watch 的 POST 客户端唯一真源在 myKolBoard-api.ts(见其测试),此处不复刻。
// 文案助手:未实测 诚实口径(null 绝不当 0)。
const apiFetch = vi.fn();
vi.mock("../http", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../http")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetch(...args) };
});

import { fetchSkuPlayOverview, skuPlayCountText } from "./myKolSkuPlay-api";

beforeEach(() => {
  apiFetch.mockReset();
  apiFetch.mockResolvedValue({});
});

describe("fetchSkuPlayOverview(GET + token)", () => {
  it("路径 = /api/admin/vkpi/my-kol/sku-play-overview,token 透传", async () => {
    await fetchSkuPlayOverview("tok");
    const [path, init, token] = apiFetch.mock.calls[0];
    expect(path).toBe("/api/admin/vkpi/my-kol/sku-play-overview");
    expect(init).toEqual({});
    expect(token).toBe("tok");
  });

  it("契约形状原样透传(不改写字段,null 保留)", async () => {
    const body = {
      contract: "my_kol_sku_play_overview_v1",
      summary: { skus: 1, videos: 2, kols: 2, measured_videos: 1 },
      groups: [
        {
          sku_code: "AF85F14-Z",
          sku_name: "AF 85mm F1.4 Pro",
          videos: 2,
          kols: 2,
          latest_measured_at: "2026-08-22T10:00:00+00:00",
          total_views: 1200,
          delta: { d1: null, d7: 300, d30: null },
          items: [],
        },
      ],
    };
    apiFetch.mockResolvedValueOnce(body);
    const res = await fetchSkuPlayOverview("tok");
    expect(res).toEqual(body);
  });
});

describe("诚实文案助手", () => {
  it("skuPlayCountText:有值千分位,null/undefined = 未实测(不当 0)", () => {
    expect(skuPlayCountText(1234567)).toBe("1,234,567");
    expect(skuPlayCountText(0)).toBe("0");
    expect(skuPlayCountText(null)).toBe("未实测");
    expect(skuPlayCountText(undefined)).toBe("未实测");
  });

});
