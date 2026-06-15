# Shopify 接入 · 同事操作流程(约 5–10 分钟)

> 目标:让营销系统自动「生成专属折扣链 → 顾客下单 → 自动算到对应 KOL 头上 → GMV/ROI 实时出数」。
> **下面这部分必须由有 Shopify 后台管理员权限的人做**;做完只要把 4 样东西发回来,其余我们这边接。
> 不需要你手动建折扣码、不需要你写任何代码。

---

## A. 你同事在 Shopify 后台要做的(唯一必须 Shopify 权限的部分)

### 第 1 步 · 开通「自定义应用」开发
1. 登录 Shopify 后台(admin)。
2. 左下角 **Settings(设置)** → **Apps and sales channels(应用和销售渠道)**。
3. 点 **Develop apps(开发应用)**。
4. 如果第一次:点 **Allow custom app development(允许自定义应用开发)** → 确认。

### 第 2 步 · 创建应用 + 配置 API 权限
1. 点 **Create an app(创建应用)**,名字填:`Viltrox Marketing Attribution`。
2. 进应用后点 **Configuration(配置)** 标签 → **Admin API integration** 区 → **Configure(配置)**。
3. 在 **Admin API access scopes(权限)** 里勾选下面这些(搜索框输入关键词找):

   | 权限 scope | 干嘛用的 | 必须? |
   |---|---|---|
   | `read_orders` | 读订单 → 销售归因 | ✅ 必须 |
   | `write_discounts` | **自动创建折扣码**(系统一键生成专属码) | ✅ 必须 |
   | `write_price_rules` | 折扣规则(兼容旧接口,一起勾稳妥) | ✅ 建议 |
   | `read_products` | 读产品 → 生成商品折扣链 | ✅ 必须 |
   | `read_customers` | 读顾客邮箱(可选,做更细归因) | ⬜ 可选 |

4. 点 **Save(保存)**。

### 第 3 步 · 安装应用 + 复制 Access Token
1. 回到应用页,点右上 **Install app(安装应用)** → 确认 Install。
2. 安装后会出现 **Admin API access token**,以 `shpat_` 开头。
   ⚠️ **它只显示这一次**,点 **Reveal token once / 显示令牌** → 复制保存好(丢了要重置)。

### 第 4 步 · 拿到 API secret(webhook 验签用)
1. 还在应用页 → **API credentials(API 凭证)** 标签。
2. 找到 **API secret key**(点显示)→ 复制。
   (这个用来校验"回传的订单确实来自 Shopify",防伪造。)

---

## B. 把这 4 样发回给我们(走飞书即可)

1. **店铺域名**:形如 `xxxx.myshopify.com`(在浏览器地址栏能看到)
2. **Admin API access token**:`shpat_...`(第 3 步复制的)
3. **API secret key**:第 4 步复制的
4. **确认勾了哪些权限**(把第 2 步勾的截个图最稳)

> 🔒 这 4 样等于店铺的钥匙,**走飞书私发**,别发群、别发邮件外链。我们收到后加密存,绝不进日志。

---

## C. 我们这边接(同事不用管,列出来让你心里有数)

- 用 token **自动注册 webhook**(订单一产生就回传)或定时拉单 —— 不用同事手动配。
- **自动生成折扣码**:运营在系统点「生成推广链接」→ 选 KOL + 产品 + 折扣 → 系统调 Shopify 自动建码 + 拼出 `?discount=CODE` 自动应用的专属链。
- **归因 + 出数**:订单回来 → 折扣码匹配到 KOL → Attributed GMV / ROI 当场从"待接入"变真数。

---

## D. 架构(已定)——三个角色,内部站不暴露给 KOL

- **www.viltrox.com** = Shopify 商店(顾客下单、折扣生效、订单来源)。**这是接入的店,token/secret 取自这个店。**
- **go.viltrox.com**(子域名,名字可改:go / vx / track) = KOL 看到的短链 + 跳转 + webhook 接收。DNS CNAME 指到我们的服务器。
- **www.viltroxtest.com** = 内部营销平台(只你们登录看数据/生成链),**绝不出现在任何 KOL 可见链接里**。

```
KOL 转发: go.viltrox.com/xxxx → 跳转 → www.viltrox.com/products/X?discount=CODE&utm=...
        → 顾客在 Shopify 下单(折扣自动减)→ webhook → go.viltrox.com/api/webhooks/shopify
        → HMAC 验签 + 折扣码匹配 → 归因 → 内部看板(viltroxtest.com)出数
```

✅ **webhook 走实时路线**(平台已公网部署,Shopify 直接打回,不用穿透/轮询),且**我们用 token 自动注册**——同事不用手动配 webhook。
✅ **KOL 只看到 viltrox 品牌**(go.viltrox.com + viltrox.com),内部工具域名隐身。

### 同事额外加一条 DNS(2 分钟)
在管理 viltrox.com 的 DNS(Cloudflare/域名商)里加一条:
- 类型 `CNAME`,主机名 `go`(即 `go.viltrox.com`),指向我们给的目标地址(我们提供)。
- (备选:不想配子域名,就直接用 `www.viltrox.com/products/X?discount=CODE` 发链,零配置,但记不到点击数。)

---

## E. 折扣码:你们要不要先约定命名规则?(可选)

系统自动生成的码可以带规则,例如 `KOL名-产品-随机`(`ALEX-AF28-7K9`)。如果你们对折扣力度有要求(比如统一 10% off / 满减),告诉我们默认值,生成时直接套。

---

## 一句话给同事
> 「帮忙在 Shopify 后台建个自定义应用,勾上 read_orders / write_discounts / write_price_rules / read_products,安装后把 **店铺域名 + access token(shpat_开头) + API secret** 走飞书发我,5 分钟搞定,其余我们接。」
