# 电商归因接入 · 完整操作手册（点击级）

> 一句话目标：营销系统自动「给 KOL 生成专属折扣链 → 顾客下单 → 自动算到对应 KOL 头上 → GMV/ROI 实时出数」。
> 三方分工：**同事**(有 Shopify 后台权限)拿 3 把钥匙 · **你**(管 DNS)加 1 条记录 · **我们**接管所有代码与配置。

**文件位置**：仓库根目录 `SHOPIFY_ONBOARDING.md`（即本文件，可直接把这份发给同事）。

---

## 0. 先确认一件事：钱走 Shopify 还是 GoDaddy？

我在你的 DNS 里看到 `pay.viltroxtest.com → paylinks.commerce.godaddy…`（GoDaddy 收款链）。你又说 Shopify 店是 `www.viltrox.com`。**两套收款流程不一样**，先对一下：

- **走 Shopify（viltrox.com）** → 照本手册「方案 A」做（推荐，折扣码/订单回传最成熟）。
- **走 GoDaddy Payments（pay.viltroxtest.com）** → 跳到文末「方案 B」，我接 GoDaddy Commerce 的订单回传（折扣码自动生成能力受限）。
- **两个都用** → 两套都接，归因合并。

下面 1–4 步是 **方案 A（Shopify）**。

---

## 1. 同事做：Shopify 自定义应用（约 8 分钟，点击级）

### 1.1 进入「开发应用」
1. 登录 Shopify 后台（`www.viltrox.com/admin` 或 `xxx.myshopify.com/admin`）。
2. 左下角点 **Settings（设置）**。
3. 左侧菜单点 **Apps and sales channels（应用和销售渠道）**。
4. 点页面上方 **Develop apps（开发应用）**。
5. 首次会要点 **Allow custom app development（允许自定义应用开发）** → 弹窗再点一次确认。

### 1.2 创建应用 + 配置权限
1. 点 **Create an app（创建应用）**。
2. App name 填：`Viltrox Marketing Attribution`，Create app。
3. 进应用后点 **Configuration（配置）** 标签。
4. 在 **Admin API integration** 区点 **Configure**。
5. 在 **Admin API access scopes** 搜索框逐个搜、逐个勾：

   | 勾这个 scope | 作用 |
   |---|---|
   | `read_orders` | 读订单 → 销售归因（**必须**） |
   | `write_discounts` | 自动创建折扣码（**必须**，省去手动建码） |
   | `write_price_rules` | 折扣规则兼容（建议一起勾） |
   | `read_products` | 读产品 → 生成商品折扣链（**必须**） |
   | `read_customers` | 读顾客邮箱做更细归因（可选） |

6. 拉到底点 **Save（保存）**。

### 1.3 安装应用 + 复制 Access Token（最关键）
1. 回应用页右上点 **Install app（安装应用）** → 弹窗点 **Install**。
2. 安装后出现 **Admin API access token**（`shpat_` 开头）。
3. ⚠️ **它只显示一次**：点 **Reveal token once（显示一次）** → 立刻复制保存。丢了只能 Uninstall 重装重生成。

### 1.4 复制 API secret key（webhook 验签用）
1. 在应用页点 **API credentials（API 凭证）** 标签。
2. 找 **API secret key** → 点显示 → 复制。

### 1.5 把这 3 样发回给我们（走飞书私发，别发群）
1. **Shopify 店的 admin 域名**：形如 `xxxx.myshopify.com`（地址栏 `/admin` 前那段）。
2. **Admin API access token**：`shpat_…`（1.3 复制的）。
3. **API secret key**：（1.4 复制的）。

> 🔒 这 3 样 = 店铺钥匙，飞书私发；我们加密存，绝不进日志。

---

## 2. 你做：加 1 条 DNS（约 2 分钟）—— 短链子域名 `go.viltrox.com`

KOL 转发的短链要用 **viltrox 品牌**、又**不暴露内部站 viltroxtest.com**，所以在 **viltrox.com 的 DNS** 里加一条子域名，指到我们的服务器（`5.78.200.75`，就是 viltroxtest.com 现在指的那台）。

### 在「管理 viltrox.com 域名的 DNS」处加：
| 字段 | 值 |
|---|---|
| Type（类型） | `A` |
| Name（主机名） | `go`（即 `go.viltrox.com`，名字想换成 track/vx 都行，告诉我即可） |
| Content（指向） | `5.78.200.75` |
| Proxy（代理） | 若 viltrox.com 也在 Cloudflare → **Proxied（橙云）**；不在 → **DNS only**，TLS 我们用 Let's Encrypt 配 |
| TTL | Auto |

- viltrox.com 若在**同一个 Cloudflare 账号** → 切到 viltrox.com 这个 zone，**Add record** 照上表填即可（和你截图里 viltroxtest.com 的加法一样）。
- viltrox.com 若 DNS 在**别处**（GoDaddy/Shopify 托管）→ 去那边加同样一条 A 记录。
- **为什么不用 go.viltroxtest.com**：会把"viltroxtest"（你不想外人看的内部站）暴露给所有 KOL。用 viltrox.com 子域名，KOL 只看到品牌。

> 加完告诉我们子域名最终名（默认 `go.viltrox.com`），我们配好服务器接这个 Host + 自动签 HTTPS。

---

## 3. 我们接管（你和同事都不用管）

- 用 token **自动注册 Shopify webhook**（指到 `go.viltrox.com`）→ 订单实时回传。同事**不用手动配 webhook**。
- 运营在系统点「生成推广链接」→ 选 **来源(项目/活动·可选) + KOL + 产品 + 折扣** → 系统调 Shopify API **自动建折扣码** + 拼出 `go.viltrox.com/xxx` 短链。
- 折扣码匹配 → 归因到 KOL/项目/活动 → 「数据追踪」看板出 点击/订单/GMV/ROI。

---

## 4. 接好后怎么验证（你自己 5 分钟测一遍）

1. 在系统「Shopify 中心 → 生成推广链接」选个 KOL + 产品 + 折扣 → 生成。
2. 复制出来的 `go.viltrox.com/xxx` 短链，浏览器打开 → 应自动跳到 `www.viltrox.com/products/…?discount=…` 且折扣已应用。
3. 用测试地址走一笔小额真单（或 Shopify 后台建个测试订单带这个折扣码）。
4. 回系统「数据追踪」看板 → 这个 KOL 应在几秒内出现 1 单 + GMV。
5. 全链通 = 上线。失败任一步告诉我，我按日志定位。

---

## 方案 B：如果钱走 GoDaddy Payments（不是 Shopify）

若 `pay.viltroxtest.com` 那个 GoDaddy 收款链才是真实下单入口：
- 折扣码自动生成 + 订单 webhook 改走 **GoDaddy Commerce API**（能力比 Shopify 弱，可能需手动建优惠码、归因靠 UTM + 订单 email 匹配）。
- 你需要提供：GoDaddy Commerce 的 API key / store id / webhook 配置入口。
- 告诉我「走 B」，我换这套接。

---

## 附：发给同事的一句话
> 「Shopify 后台 → 设置 → 应用和销售渠道 → 开发应用 → 创建应用，勾 `read_orders` / `write_discounts` / `write_price_rules` / `read_products`，安装后把 **店铺 .myshopify.com 域名 + token(shpat_开头) + API secret** 走飞书发我。8 分钟搞定,其余我们接。」
