# ACL4SSR Full · ClaudeAI MultiMode

**为 Claude / ChatGPT 账号优化的 ACL4SSR 订阅转换配置，保留游戏平台（含 Steam）三分流。**

底板是 [cainiao524/acl4ssr-steam](https://github.com/cainiao524/acl4ssr-steam) 的
`ACL4SSR_Online_Full_GameControl_MultiMode.ini`，在其上打两个 patch。

---

## 快速使用

### 方式一：填进订阅转换站

把下面这个地址粘进订阅转换站的 **「远程配置 / 配置文件 / Config」** 输入框：

```text
https://raw.githubusercontent.com/cainiao524/acl4ssr-ai/main/ACL4SSR_Online_Full_ClaudeAI_MultiMode.ini
```

然后照常填你的机场/自建订阅链接 → 生成。

> 国内访问 raw.githubusercontent.com 不稳定时，把 `raw.githubusercontent.com`
> 换成 `cdn.jsdelivr.net/gh`（路径变成 `cdn.jsdelivr.net/gh/cainiao524/acl4ssr-ai@main/...`）。

### 方式二：作为底板再改

自己 fork 后改 `build.py` 里的 patch，让 Actions 每天重新生成。

### 方式三：不用订阅转换站，直接喂给 mihomo / Clash Verge

如果你不想经过第三方转换站（不想把订阅链接交给别人、或者你的转换站不支持自定义 base），
可以把本仓库的 `base/clash-base.yaml` 当模板，自己拼出配置。好处是 **DNS 一定按你看到的那样生效**。

> `ACL4SSR_Online_Full_ClaudeAI_MultiMode.ini` 是给 **subconverter** 用的，
> 它本身不是一份可直接加载的 mihomo 配置 —— 它靠 `ruleset=` 引用几十个远程规则集，
> 由转换器在生成时展开。

---

## 它改了什么

### 1️⃣ AI 策略组：从「会漂移 + 可直连」改成「钉死 / 不泄漏」

**上游原版**（问题所在）：

```ini
custom_proxy_group=💬 Ai平台`select`[]🚀 节点选择`[]♻️ 自动选择`[]🇸🇬 狮城节点`[]...`[]DIRECT
```

两个隐患：

| 隐患 | 说明 |
|---|---|
| **默认链路会漂** | `select` 组的**第一项就是默认选中项**，而它是 `🚀 节点选择`；该组的第一项又是 `♻️ 自动选择`(url-test)。于是默认链路是<br>`💬 Ai平台 → 🚀 节点选择 → ♻️ 自动选择(url-test)`<br>出口会随健康检查变化。**对 Claude / GPT 账号，IP 漂移是头号封号信号。** |
| **挂着 `[]DIRECT`** | 节点全部不可用时，AI 流量会**静默走真实 IP**，而客户端界面看不出任何异常。 |

**本仓库改成**：

```ini
custom_proxy_group=💬 Ai平台`select`[]🔒 AI 专用`[]🚀 手动切换
custom_proxy_group=🔒 AI 专用`select`[[]SELF[]]
```

- ✅ **没有** `自动选择` / `故障转移` / `负载均衡` → 不会漂
- ✅ **没有** `DIRECT` → 节点全挂时**直接失败**（fail closed），你会立刻发现，而不是几天后才发现账号裸奔过
- ✅ 有自建节点时默认走它；没有时退到 `🚀 手动切换` 让你手选一个，**选完即固定** —— 两条路都不会偷偷换 IP
- ✅ 只认带 `[SELF]` 的**自建**节点 → 机场节点进不来（原因见下文「部署后必须做的一件事」）

### 2️⃣ 补两个 Claude / ChatGPT 深度规则集

上游只引用了 ACL4SSR 官方的 `AI.list`（47 条）与 `OpenAi.list`（17 条）。
其中 Claude 侧靠 `DOMAIN-KEYWORD,claude` 兜底 —— **兜不住不含 "claude" 字样的 `clau.de`，也兜不住 Anthropic 自有网段。**

新增的是 [VPSDance/ai-proxy-rules](https://github.com/VPSDance/ai-proxy-rules)
（MIT · 每日自动同步的聚合器：合并 v2fly / blackmatrix7 / xiaolai / net-coffee 多源后按 provider 精修）：

| 规则集 | 条数 | 补出来的内容举例 |
|---|---|---|
| `anthropic` | **50** | `clau.de` · `claude.app/new/site/dev` · `claudemcpclient.com` · `modelcontextprotocol.com/.net/.org` · `anthropic.auth0.com` · `IP-CIDR,160.79.104.0/21` · `DOMAIN-KEYWORD,datadog\|sentry` |
| `openai` | **52** | `oaistatic.com` · `oaiusercontent.com` · `auth0.com` · `identrust.com` · `statsigapi.net` · `IP-CIDR,199.47.142.0/23` |

> ⚠️ 用的是 **Surge 格式的 `.list`**（`rules/surge/*.list`），不是 clash 的 `.yaml`。
> 后者是 mihomo rule-provider 风格（`payload:` + `+.x.y`），部分 subconverter 版本对它的支持并不一致；
> 而 `surge/*.list` 是纯粹的 `TYPE,VALUE[,modifier]`，是所有 subconverter 都吃的最大公约数。

### 3️⃣ 位置：新规则集必须紧跟上游 AI 规则集之后

ini 里规则是**先匹配者胜**。上游 `🚀 节点选择` 引用的 `ProxyGFWlist.list` 有 **7000+ 条**，其中就包含：

```
DOMAIN-SUFFIX,anthropic.com
DOMAIN-SUFFIX,claude.ai
DOMAIN-SUFFIX,chatgpt.com
DOMAIN-SUFFIX,openai.com
```

如果把新规则集**追加到文件末尾**，它会排在 GFWlist 之后 —— 于是 `claude.ai` 先被 GFWlist 命中，
走 `🚀 节点选择`（可能落在任何一台机器上），而我们钉死的那套**永远轮不到**。

**这是最隐蔽的一类失效**：配置能加载、AI 能正常用、分流看着也正常，**但账号出口根本不是你以为的那台。**

所以 `build.py` 用**锚点插入**，把新规则集放到 `AI.list` / `OpenAi.list` 那一组的紧后面
（生成物实测：第 36 行，而 `ProxyGFWlist` 在第 63 行）。

### 4️⃣ 自带 `clash_rule_base` —— 补齐 DNS / sniffer / IPv6

**这是最关键的一处，也是最容易被忽略的。**

上游 ini 里这一行是**注释掉**的：

```ini
;clash_rule_base=https://raw.githubusercontent.com/ACL4SSR/ACL4SSR/master/Clash/GeneralClashConfig.yml
```

于是 subconverter 用它自己的**内建模板** —— 而内建模板里**没有 `dns:` 段**。

**没有 DNS 段不只是"不安全"，它会让分流本身失效：**

| 后果 | 说明 |
|---|---|
| **`GEOIP,CN` 兜底失效** | 这条规则靠**真实解析结果**判断归属。DNS 配不好 → 国内小站被判成境外 → 落到 `FINAL` 走代理 → **绕道境外，明显变慢** |
| **AI 域名解析可能被污染** | 用国内 DNS 解析 `claude.ai` |
| **DNS 泄漏** | 把"你在访问 claude.ai"这一元数据交给本地 ISP |
| **域名规则可能匹配不上** | 部分客户端默认 `enhanced-mode` 不是 fake-ip，目标已是 IP 时 `DOMAIN-SUFFIX` 规则失效 → AI 域名掉进 `FINAL` |
| **IPv6 绕过** | 没有 `ipv6: false`。TUN 未接管 IPv6 时，解析出 AAAA 的域名会走物理网卡直连 —— **这是真实 IP 泄漏最短的一条路径，而客户端界面看不出任何异常** |

（顺带一提：那个被注释掉的目标 `GeneralClashConfig.yml` 本身也是 **Clash for Windows 时代的老文件** —— 没有 `dns` 段、没有 `tun` 段、`ipv6: true`，还带着一堆 `cfw-*` 专有字段。）

**本仓库的做法**：自带一份 [`base/clash-base.yaml`](./base/clash-base.yaml)，并在 `build.py` 里把那一行激活指向它：

```ini
clash_rule_base=https://raw.githubusercontent.com/cainiao524/acl4ssr-ai/main/base/clash-base.yaml
```

base 里写了：

```yaml
ipv6: false                    # 防 IPv6 绕过 TUN 泄漏真实 IP
dns:
  enable: true
  enhanced-mode: fake-ip
  fake-ip-range: 198.18.0.1/16
  fake-ip-filter: [18 条]      # 游戏联机 / P2P / NTP / 推送
  respect-rules: true
  nameserver: [doh.pub, alidns]
  proxy-server-nameserver: [doh.pub]        # respect-rules 下缺它 mihomo 起不来
  fallback: [1.1.1.1/dns-query, 8.8.8.8/dns-query]   # 只 DoH，绝不用明文/DoT
  fallback-filter: {geoip: true, geoip-code: CN}
  nameserver-policy:            # AI 域名固定走境外 DoH
    "+.claude.ai":     [1.1.1.1/dns-query, 8.8.8.8/dns-query]
    "+.anthropic.com": [1.1.1.1/dns-query, 8.8.8.8/dns-query]
    "+.openai.com":    [1.1.1.1/dns-query, 8.8.8.8/dns-query]
    ...                          # 共 9 条
sniffer:                        # HTTP + TLS + QUIC 全开，保证 fake-ip 下按域名命中
  enable: true
  sniff: {HTTP: {...}, TLS: {...}, QUIC: {...}}
```

**⚠️ 但要注意**：有些订阅转换站会**忽略** ini 里的 `clash_rule_base`、强制用自己固定的模板。

**怎么确认生效了**：生成配置后搜一下 `enhanced-mode` 是不是 `fake-ip`、`ipv6` 是不是 `false`。
如果不是 → 换一个支持自定义 base 的转换站，或者改用 [Clash Verge](https://github.com/clash-verge-rev/clash-verge-rev) / mihomo 内核直接引用这份配置。

### 5️⃣ Steam 三分流：默认出口已修正为 DIRECT

| 组 | 默认 | 说明 |
|---|---|---|
| `🎮 游戏下载` | **DIRECT** | 76 条 CDN，含 `dl.steam.clngaa.com` / `content.steamchina.com` 等国内节点 —— 直连比绕代理快得多，而且不会把几十 GB 下载计进流量 |
| `🎮 Steam 商店/社区` | 节点选择 | 28 条，含 `steampowered.com` / `steamcommunity.com` / `steamstatic.com`（国内常被墙，**保持走节点**） |
| `🎮 游戏平台` | **DIRECT** | Steam / Epic / Xbox / PlayStation / Nintendo … |
| `🔑 Steam 登录` | 固定出口 | `login.` / `api.` / `checkout.` / `help.steampowered.com` 单独拆出，默认走固定节点（涉及账号状态，出口不漂） |

> ⚠️ **"子分流里有 DIRECT" 不等于 "下载默认直连"。**
> subconverter / ACL4SSR 的 select 组语义是「**第一项 = 默认选中项**」。
> 底板原本把 `[]DIRECT` 放在成员末尾，于是默认出口是「🚀 节点选择」——
> 配置看着完全正常，下载却整包走代理。
> 本仓库的 `assets/local-overlay.ini` 把 DIRECT 提到首位；
> 而**编译产物**（`fleet/`）里另有一层保证：`assets/acl4ssr_build.py` 的
> `build_groups()` 按 ini 的**原始声明顺序**合并成员，不会再把内建策略
> append 到末尾（这个 bug 曾让 ini 里写对了也没用，详见 `fleet/README-fleet.md`）。

---

### 🇯🇵 日本节点：钉死到单一协议（**不再自动切换**）

```ini
custom_proxy_group=🇯🇵 日本节点`select`[]🇯🇵 日本 [SELF] xtls-reality`[]🇯🇵 日本 [SELF] hysteria2
```

出口**恒定**为 REALITY（`select` 首位即默认项），两个协议都留在组里，
需要时在客户端一键切 —— 不用改配置。

> ⚠️ **两条线的写法故意不同，别去"统一"。**
> 上面这段是 `assets/local-overlay.ini`（自建线：节点名由本机 sing-box 生成、
> 不经过任何第三方）的写法 —— **显式列名字**。改名后 `acl4ssr_build.py` 会把它
> 记进 `missing_group_refs`，属于「显式失败」。
>
> 而给**订阅转换站**用的 `build.py` 里，同样的组改用了**过滤器**，
> 并且**多一个成员**（机场日本节点）：
>
> ```ini
> custom_proxy_group=🇯🇵 日本节点`select`(\[SELF\].*xtls-reality)`(\[SELF\].*hysteria2)`(^(?!.*\[SELF\]).*(日本|🇯🇵|Japan|JP))
> ```
>
> 改过滤器的原因：转换站的 `append_type=true` 会给节点名插协议前缀，实际产出是
> `🇯🇵 [VLESS] 日本 [SELF] xtls-reality`；而改名**只作用于节点名、
> 不作用于组里写死的 `[]名字`** → 引用悬空 → 客户端报
> `proxy group[3]: 日本节点: '🇯🇵 日本 [SELF] xtls-reality' not found`
> 并**拒绝加载整份配置**（FlClash 上的真实事故）。
> 过滤器里的 `.*` 跨过任何 `[TYPE]` 前缀，所以改名免疫；协议名仍是区分点，
> 因此仍只命中一个节点、首位依旧恒定。
> 对照组：「🔒 AI 专用」一直用的是过滤器 `[[]SELF[]]`，所以它从没出过这个问题。
>
> ⚠️ **第三个成员（机场日本节点）只在转换线存在，而且必须排在自建之后。**
> `🇯🇵 日本节点` 不只是 AI 链路上的一环，它是 ACL4SSR 的**地区组** ——
> 另外 13 个组（油管 / 奈飞 / 国外媒体 / 漏网之鱼 / Steam 商店 …）都
> `[]🇯🇵 日本节点` 引用它。曾经把它写成"只有自建 2 个"，后果是那 13 个组
> 一起失去了"选一个机场日本节点"的能力：组名叫「日本节点」，
> 却一个机场日本节点都没有。
>
> 加回来为什么不会让出口漂移：成员顺序是
> **自建 Reality → 自建 Hysteria2 → 机场日本**，而 `select` 首位即默认出口，
> 所以默认永远是自建 Reality；它又是 `select` 而非
> `url-test`/`fallback`/`load-balance`，没有任何"自己会换"的机制。
> 自建线不需要这个成员 —— 那台机器上根本没有机场节点。
>
> 见「🚀 自建节点 / ✈️ 机场节点」：转换线里自建与机场混在同一份订阅，
> 所以额外给了两个显式入口，用来一眼分清哪些是自己的机器。

#### 为什么不再用 `fallback` / `url-test`

这里曾经是 `fallback`（Hysteria2 优先、失效切 Reality）或上游默认的 `url-test`
（按延迟择优）。两种都会**反复换协议**：本机两个节点是**同一 IP 的两种协议**，
延迟差常年在几十毫秒内浮动，于是健康检查一跑就换：

```
UDP 顺   → 选中 Hysteria2
UDP 被限速 → 掉下去，切回 Reality
Reality 恢复 → 又切回去
```

表现就是「**连接方式一直变、不稳定**」—— 正在跑的 TCP 连接随组切换被重建，
下载 / SSH / 视频都会被反复打断。**不是节点坏了，是切换本身造成的。**

> 实测数据：两种协议对「客户端 ←→ 本机」这段链路的表现，**服务器侧测不出来**
> （VPS 测自己是环回，两者都是 204，34 vs 32 MB/s，完全等价）。
> 选 REALITY 是机理判断：TCP/443 是运营商不敢整体封的端口，UDP 常被
> QoS / 限速甚至整段丢弃；REALITY + XTLS-Vision 本身就是为抗 DPI 设计的。
> 想反过来钉 Hysteria2，把两个成员调换顺序即可。

#### 节点名格式

必须是 `<地区> [SELF] <协议>`，且**协议名在最后**：

- `🇯🇵 日本 [SELF] xtls-reality`
- `🇯🇵 日本 [SELF] hysteria2`

`[SELF]` 是自建 / 机场的隔离标识（见上文「部署后必须做的一件事」）。
改了节点名要同步改 `build.py` 的 `JAPAN_FALLBACK_GROUP`，否则该组会变空组 ——
这是刻意的**显式失败**，不会静默降级。`🚀 节点选择` 默认指向 `🇯🇵 日本节点`，
所以默认出口也是恒定的。

---
## 默认分流行为：本质是「国内直连、其余走代理」

```
 1. LocalAreaNetwork / UnBan / GoogleCN        → 🎯 全球直连 = DIRECT
 2. BanAD / BanProgramAD                        → REJECT
 3. GoogleFCM / Bing / OneDrive / Microsoft / Apple → DIRECT
 4. Telegram                                    → 代理
 5. AI（166 条）                                → 💬 Ai平台 = 🔒 AI 专用（钉死）
 6. 网易音乐 / 游戏 / Steam / YouTube / Netflix / 巴哈 → 各自组
 7. 哔哩哔哩（41 条）                           → DIRECT
 8. 国内媒体                                    → DIRECT
 9. 国外媒体                                    → 代理
10. ProxyGFWlist（7052 条）                     → 代理
11. ChinaDomain（726 条）                       → DIRECT
12. ChinaCompanyIp / Download                   → DIRECT
13. GEOIP,CN                                    → DIRECT   ← 白名单兜底
14. FINAL → 🐟 漏网之鱼                         → 🚀 节点选择 = 代理
```

**第 13 步是"国内白名单"强度的关键。** `ChinaDomain.list` 只有 **726 条**，而国内域名有几十万 ——
真正兜住"国内小众站直连"的就是 `GEOIP,CN`：域名不在清单里没关系，解析出中国 IP 就直连。

**→ 这也再次说明为什么第 4 节的 DNS 必须配好：`GEOIP,CN` 依赖真实解析结果。**

### B 站会走代理吗？

**不会。** 实测三个层面都是直连：

- `Bilibili.list`（20 条）+ `BilibiliHMT.list`（21 条）在第 **59–60** 行，而 `ProxyGFWlist` 在第 **63** 行 → 先匹配者胜
- `ProxyGFWlist` 里 B 站域名 **0 条**（`bilibili` / `hdslb` / `bilivideo` / `b23.tv` 全部为 0）
- `📺 哔哩哔哩` 组默认就是 `🎯 全球直连` → `DIRECT`

**唯一的特性**：`BilibiliHMT.list`（**港澳台限定番剧**）走的是同一个组，所以默认也直连 →
港澳台限定内容依然看不了。想看的话在客户端把 `📺 哔哩哔哩` 切到 `🇨🇳 台湾节点`。

### 顺带核查：GFWlist 里没有误伤国内服务

```ini
baidu   → baidu.jp            （百度日本站，走代理合理）
weibo   → freeweibo.com / weiboleak.com / whatsonweibo.com   （第三方，非微博本体）
zhihu   → duanzhihu.com / freezhihu.org                      （第三方）
jd.com  → dbgjd.com                                          （第三方）
```

**全是名字沾边但完全无关的域名，没有误伤国内正主。**

---

## ⚠️ 部署后必须做的一件事：确认 AI 组只指向【自建】节点

`🔒 AI 专用` 按**节点名里的归属标识**匹配：

```ini
custom_proxy_group=🔒 AI 专用`select`[[]SELF[]]
```

**这个过滤器命中你哪个节点，AI 流量就走哪个节点。** 而 `select` 组的
**首位即默认出口**，所以这一条直接决定 Claude / ChatGPT 看到哪个 IP。

### 为什么是 `[SELF]` 而不是「专线 / AI 字样」

这里曾经是 `(AI|Claude|GPT|OpenAI|专用|专线|Dedicated|落地|解锁)` ——
靠**品牌词**猜"这个节点是不是给 AI 用的"。那个口径在你**同时挂了机场节点**后会崩：
机场节点叫「🇯🇵 日本 IEPL 专线」这种太常见了，于是它们被吸进 AI 组，
新导入的客户端直接选到**机场节点** —— AI 出口 IP 当场就变了。

`[SELF]` 是**归属标识**，不是品牌词：自建节点名里带它，机场节点没有。
无论机场怎么命名都进不来。

| 你的情况 | 该怎么做 |
|---|---|
| 节点是你自己的 VPS（本仓库的默认场景） | 给节点名加上 `[SELF]`，见下 |
| 节点名已经是 `🇯🇵 日本 [SELF] xtls-reality` 这种 | ✅ 什么都不用改 |
| **同时挂了机场** | 保持 `[SELF]` 标识 —— 这就是自建 / 机场的隔离边界 |
| **一个自建节点都没有** | `🔒 AI 专用` 会是空组，`💬 Ai平台` 只剩 `🚀 手动切换` —— **请在客户端里手动把「💬 Ai平台」切到一个固定节点**，并保持不动 |

### 标识怎么加（以及一个必须注意的顺序问题）

本仓库的机器侧（`sb-oneclick`）由 sing-box 的 `inbound.tag` 生成订阅里的节点名，
所以在 tag 上加一次即可，v2rayn / sing-box / shadowrocket / clash2 全都跟着变。

⚠️ **`[SELF]` 必须放在协议名之前**：

```
🇯🇵 日本 [SELF] xtls-reality     ✅
🇯🇵 日本 xtls-reality [SELF]     ❌ —— 机器侧内核靠"剥掉末尾协议名"反推节点名
```

内核用的是这两条：

```bash
sed -n "s/.*\"tag\":\"\(.*\) xtls-reality.*/\1/p"        # xtls-reality
awk -v suffix=" hysteria2" '… sub(suffix"$", "", v) …'   # hysteria2
```

两者都要求**协议名是最后一个 token**。放在末尾会被重建出
`… hysteria2 [SELF] hysteria2` 这种脏名字（实测踩过）。

⚠️ **不要用 `rename-node.sh` 做这件事**：它的改名公式是 `region_prefix + suffix`，
而 `PROTO_SUFFIXES` 含空字符串候选 → `tag.endswith("")` 恒真 → `suffix=""` →
会把节点名**截断成「🇯🇵 日本 」丢掉协议后缀**。那个脚本是为"加地区词"设计的。

**最后一条很重要**：如果你的节点都很普通、也不想区分归属，那就**手动选一个、然后永远别换**。
「一直用同一个 IP」比「用哪个 IP」更关键。

**不要**把 `💬 Ai平台` 切到 `♻️ 自动选择` 或任何 `url-test` 组 —— 那正是本仓库要消除的东西。

---

## 自动更新

`.github/workflows/update.yml` 每天 UTC 04:00 会：

1. 从 `cainiao524/acl4ssr-steam` 拉最新的底板 ini
2. 应用 `build.py` 里的 patch
3. 只有产物真的变了才提交

**这样上游的规则改动会自动进来，而我们的 patch 始终只有那 3 行。**

（做法和 `acl4ssr-steam` 自己维护游戏分流的方式一致。）

### 同一天还有 `fleet.yml`：给机器用的「编译成品」

`update.yml` 产出的是**给订阅转换站吃的 ini**（规则还没展开）。
如果你有一台跑 mihomo / Clash 的机器，希望它**直接拿到展开好的完整配置**、
且**规则更新不落在机器上**，那就是 `fleet.yml` 的活：

```text
.github/workflows/fleet.yml   每天 UTC 04:20
  ├─ 拉上游 ini + 应用 assets/local-overlay.ini
  ├─ fetch --force 下载全部 42 个规则集
  ├─ 断言「rule<NN>.list ↔ ini 索引」一致（错配直接红）
  ├─ 生成完整版 + 模板（--emit-template，proxies 段留注入标记）
  ├─ mihomo -t 权威校验
  └─ 断言产物契约（Steam 首位 DIRECT / AI 组钉死 / 模板无凭据）
        ↓ 提交到 fleet/
fleet/acl4ssr-game.template.yaml   规则+策略组+dns 完整，节点处是标记
fleet/META.json                    上游 sha256 / 各规则集 sha256 / 规则数
```

机器侧只做「**下载 + 注入自己的节点 + 校验 + 原子替换**」：

```bash
bash scripts/sync-subscription.sh --dry-run   # 只校验，不落盘
bash scripts/sync-subscription.sh             # 正式同步
```

> **为什么规则必须搬去 CI，不能留在机器上生成。**
> 机器上的规则集是**持久化文件**：非空即复用，永不重下。上游 ini 的 ruleset
> 列表一变，索引就整体位移，旧文件与新索引错配 —— 而所有自检只查
> "非空 / 个数 / 条数"，于是错配**静默通过并每天重新编译一遍**。
> 实测状态持续了 10 天：`rule14` 拿的是网易云音乐的规则、`rule15` 拿的是
> GameDownload 的规则，导致「💬 Ai平台」被塞进 76 条游戏平台下载 CDN
> （172 条里 117 条与 AI 无关），同时 **Anthropic / OpenAI 两个深度规则集
> 一条都没进产物**，Steam 下载全部走代理。
>
> CI runner 每次都是全新机器：没有历史缓存、没有旧 `rule<NN>.list`。
> **「不可能错配」由架构保证，而不是靠记得加 `--force`。**
>
> 节点凭据不进仓库（本仓库是公开的）：模板里只有 `@NODE_INJECT_POINT@` 标记，
> 真实 uuid / public-key / password 永远留在机器上。


---

## 为什么用 `build.py` 而不是直接放一份改好的 ini

手工改一份放仓库里有两个问题：

1. **上游加新策略组时你收不到** —— 副本会慢慢腐烂；
2. **看不出改了什么** —— git diff 里全是上游的日常变动，我们的 3 行淹没其中。

把改动写成**显式的 patch**，diff 就永远只有两类：上游的变动，和我们的那几行。

---

## 与 `acl4ssr-steam` 的关系

| | `acl4ssr-steam` | 本仓库 |
|---|---|---|
| 定位 | 游戏平台三分流（通用底板） | **AI 账号安全** + 游戏三分流 |
| AI 组 | 上游 ACL4SSR 原版（会漂、可直连） | **钉死单节点、无 DIRECT** |
| AI 规则 | ACL4SSR `AI.list` + `OpenAi.list` | 追加 Claude 50 条 + OpenAI 52 条 |
| 关系 | 本仓库的底板 | 在其上打 2 个 patch |

**本项目只是加了一层 patch，游戏分流的全部功劳属于 [cainiao524/acl4ssr-steam](https://github.com/cainiao524/acl4ssr-steam) 与 [ACL4SSR](https://github.com/ACL4SSR/ACL4SSR)。**

---

## License

MIT
