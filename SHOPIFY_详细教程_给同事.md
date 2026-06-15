# Shopify 接入 · 手把手详细教程（给同事）

> 你只要照着点，**全程约 8 分钟**。最后把 **3 样东西**（店铺域名、token、secret）发回即可，其余技术的事我们都接。
> Shopify 后台一般是英文，下面每个按钮我都标了「英文名（中文意思）」，照着找就行。
> 🔴 全程最重要的一步是 **第 6 步复制 token**——它只显示一次，一定当场复制！

---

## 准备
- 用**能登录 Shopify 后台、且是店铺 Owner / 管理员**的账号。
- 用电脑浏览器（手机也行但电脑更方便复制）。
- 准备一个记事本/备忘录，等下要粘贴 2 段密钥。

---

## 第 1 步：登录 Shopify 后台
1. 打开浏览器，访问 `www.viltrox.com/admin`（或你们的 `xxxx.myshopify.com/admin`）。
2. 登录后，你会看到左边一列菜单（Home、Orders、Products…）。

✅ **顺手记下店铺域名**：浏览器地址栏里 `/admin` 前面那一串就是（形如 `viltrox-xx.myshopify.com`）。这是要发回的**第 1 样**。

---

## 第 2 步：进入「设置」
1. 看左下角，有个齿轮图标 **Settings（设置）**，点它。
2. 进入设置页，左边会出现一列设置菜单。

---

## 第 3 步：进入「开发应用」
1. 在设置左侧菜单里找 **Apps and sales channels（应用和销售渠道）**，点它。
2. 在打开的页面**右上角**找一个按钮 **Develop apps（开发应用）**，点它。
   - 如果找不到这个按钮：说明还没开权限。页面上会有一句 **Allow custom app development（允许自定义应用开发）**，点它 → 弹窗里再点 **Allow custom app development** 确认一次。然后 Develop apps 按钮就出现了。

---

## 第 4 步：创建应用
1. 点 **Create an app（创建应用）**。
2. 弹窗里 **App name（应用名字）** 填：`Viltrox Marketing Attribution`（随便起也行，自己认得就好）。
3. 点 **Create app（创建）**。

---

## 第 5 步：勾选权限（最关键的配置）
1. 创建后进入应用页，点上方 **Configuration（配置）** 标签。
2. 找到 **Admin API integration** 这一块，点里面的 **Configure（配置）**。
3. 会出现一个很长的权限列表 + 一个搜索框。**用搜索框逐个搜下面 4 个，搜到就打勾**：

   | 在搜索框输入 | 勾选这一项 | （它是干嘛的，不用记） |
   |---|---|---|
   | `read_orders` | ☑ read_orders | 读订单 |
   | `write_discounts` | ☑ write_discounts | 自动建折扣码 |
   | `write_price_rules` | ☑ write_price_rules | 折扣规则 |
   | `read_products` | ☑ read_products | 读产品 |

4. 4 个都打上勾后，拉到页面**最下方**点 **Save（保存）**。

> 检查：保存后这 4 项应显示为已选中。少勾哪个回来补勾再保存即可。

---

## 第 6 步：安装应用 + 复制 token 🔴（最重要！）
1. 回到应用页，**右上角**点 **Install app（安装应用）**。
2. 弹窗点 **Install（安装）**。
3. 安装成功后，页面会出现一个 **Admin API access token**，以 **`shpat_`** 开头。
4. 🔴 它旁边有 **Reveal token once（显示一次）**——点它，token 会显示出来。
5. 🔴🔴 **立刻全选复制，粘到你的记事本里！** 这个 token **只显示这一次**，关掉页面就再也看不到了（丢了只能卸载重装重生成）。

这段 `shpat_...` 就是要发回的**第 2 样**。

---

## 第 7 步：复制 API secret（第 3 样）
1. 还在这个应用页，点上方 **API credentials（API 凭证）** 标签。
2. 找到 **API secret key**，点旁边的显示/眼睛图标。
3. 复制它，粘到记事本。

这是要发回的**第 3 样**。

---

## 第 8 步：把这 3 样发回（走飞书私发）
把记事本里这 3 样发给对接人：

1. **店铺域名**（第 1 步记的，形如 `xxxx.myshopify.com`）
2. **Admin API access token**（`shpat_` 开头那段）
3. **API secret key**

> 🔒 这 3 样等于店铺钥匙，**只走飞书私聊发，别发群、别截图发朋友圈**。对方收到后会加密保存。

---

## ✅ 完成！你的部分就到这。
剩下的（建折扣码、订单回传、归因出数）全是我们这边自动接，你和店铺都不用再动。

---

## 常见问题（出岔子看这里）
- **找不到 Develop apps 按钮**：先点 Allow custom app development 开权限（第 3 步说明）。
- **token 没来得及复制就关了**：没关系，把这个 app **Uninstall（卸载）** 再 **Install（安装）** 一次，token 会重新显示，记得这次当场复制。
- **保存权限时报错 / 灰色**：确认你的账号是 Owner 或有 apps 管理权限；不是的话找店铺 Owner 来操作这一步。
- **不确定店铺域名是哪个**：地址栏 `xxxx.myshopify.com/admin` 里 `/admin` 前那串就是；自定义域名 `www.viltrox.com` 也行，但 `.myshopify.com` 那个最稳。
- **担心权限给多了**：只勾了读订单/读产品 + 建折扣码，没有任何删除/改店铺/收款权限，安全。

---

## 一句话版（如果她赶时间）
> Shopify 后台 → Settings → Apps and sales channels → Develop apps → Create an app → Configuration → Admin API 勾 `read_orders`+`write_discounts`+`write_price_rules`+`read_products` → Save → Install app → **复制 `shpat_` token（只显示一次！）** → API credentials 复制 **API secret** → 把 **店铺域名 + token + secret** 飞书发我。
