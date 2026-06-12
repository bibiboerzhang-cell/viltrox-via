# F3 全网发现 · 设计稿(2026-06-12,纯纸面零施工;过闸后才动码)

## 1 成本实测方案
- 路径:`llm_gateway._call_google` 现有 google provider 形态 + `generationConfig.tools=[{google_search:{}}]`(grounding 开关,**仅 F3 purpose 传入,不动共享默认**)
- 计价:Gemini grounding = 模型 token 费 + grounding 查询费(牌价 $35/千次 grounded query,以 Apify 案教训:**牌价不作数,实测落档**)
- 实测申请:**1 次真实调用,预算 <$0.5,候你"测"字**;产出=单问实测美元+候选条数+延迟,落本档第 1 节回填栏:`实测:$____ / ____ 候选 / ____ ms(候"测")`

## 2 每问预算上限(沿 106 范式,migration 三段式随 apply 窗)
- `single_call_kol_discovery = $0.50`(硬停,fallback=block)
- `cron:vkpi_kol_discovery = $10.00/月`(软警 0.8)
- 数字为提案,实测后可调;migration 编号随窗分配

## 3 候选质量校验(入口闸=卫生闸)
- 最低粉丝:≥5,000(平台可配)
- 平台白名单:youtube/instagram/tiktok(F1 chips 同集)
- 机构号过滤:名称含 official/brand/shop/store + 粉丝/关注比异常
- **`_looks_like_garbage_handle` 必经**(登记制条款:卫生闸即入口闸)
- 撞库查重:handle/channel_id 对 kol_pool + staging 双查(F4 既有条款)

## 4 暂存区表结构(草案;**与 kol_pool 零外键直连——暂存区绝不污染主池**)
```sql
CREATE TABLE vkpi_kol_discovery_staging (
  id BIGSERIAL PRIMARY KEY,
  discovery_uid TEXT UNIQUE NOT NULL,
  source TEXT NOT NULL,                -- 'gemini_grounding' | 'market_signal'(见第 6 节)
  query_text TEXT, platform TEXT, handle TEXT, display_name TEXT,
  profile_url TEXT, followers BIGINT, one_line_reason TEXT,
  quality_flags_json TEXT,             -- 校验结果留痕(过/拒+规则名)
  status TEXT NOT NULL DEFAULT 'pending',  -- pending → approved → archived(状态机;approved 后走 F4 建档)
  dedup_hit_kol_pool_id BIGINT,        -- 撞库命中记 ID,不建 FK(软引用)
  created_by_staff_id BIGINT, created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW());
```

## 5 写入路径登记表第四行(先登记,码后行)
> 管4 | F4 暂存区→建档 | (建档走管2 `write_kol_profile_basics`,自动承袭其卫生闸) | 闸接入点=staging 入列校验(第 3 节)+ 管2 既有闸双层 | 接入 commit:候施工

## 6 "Intelligence 创作者腿"合并设计(既裁并入)
- grounding 候选与 market_signal 体系发现的创作者**共用同一暂存区+校验+撞库**——`source` 字段区分进水口,**一条管线两个进水口**;Intelligence 侧只需向 staging 投递,审批/建档/卫生全复用

## 7 F1 对话式追问 · 交互草图
```
用户问句("我们有个xxx镜头想找KOL")
  → 意图识别(LLM 单跳:找人意图? y/n + 提取产品/题材)
  → 追问条:平台多选 chips [TK][IG][FB][YT](复用 FilterBar chip 样式)
  → 带约束召回:库内 15(现 smart_kol_search 端点加 platforms 参数)
    + 全网 15(F3,过闸后)
  → 结果区:复用现 50 候选卡 UI(SmartKolInputPanel:884 grid 原样)
    + 琥珀诚实条(P0-B 范式:标注哪条腿启用/未启用)
```
复用点:候选卡 grid、session/泳道回填、历史 chips——**新码仅意图跳+chips 追问条**。

—— 设计稿过闸后才施工;F4 建档尾巴依赖 E5,排期不变。
