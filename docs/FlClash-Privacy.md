# FlClash Claude/OpenAI 隐私分流（Windows + Android）

## 使用

推荐的订阅转换远程配置：

```
https://raw.githubusercontent.com/cainiao524/acl4ssr-ai/main/ACL4SSR_ClaudeAI_Simple.ini
```

这是 **subconverter 的 INI，不是 FlClash 可直接加载的 YAML**。让订阅转换站生成 Mihomo YAML，再导入 FlClash。若转换器忽略 `clash_rule_base`，DNS/TUN 配置不会随 INI 生效；必须打开**生成后的 YAML**，核实 `dns.nameserver-policy`、`dns.respect-rules` 和 `tun`。

保留现有完整版 `ACL4SSR_Online_Full_ClaudeAI_MultiMode.ini`，避免破坏老用户和 GitHub fleet 发布流程。精简版只保留 AI、直连、广告、Steam 下载、Steam 商店、游戏平台、节点选择、漏网之鱼等 11 个策略组；删除按地区/流媒体拆成的众多重复选择组。

## 防直连

- `💬 Ai平台 → 🔒 AI 专用`；`🔒 AI 专用` 只收节点名含 `[SELF]` 的自建节点，包含 `REJECT`；禁止切到 `DIRECT`，没有匹配节点时预期无法访问 AI。
- 节点名字例子：`日本 [SELF] xtls-reality`、`日本 [SELF] hysteria2`。选择的 `select` 出口不会自行测速切换。
- AI 优先规则必须比国内直连、微软和 GFW 规则更靠前。Anthropic **入站** IPv4 `160.79.104.0/23`、IPv6 `2607:6bc0::/48`；不能用它的**出站** IPv4 `/21` 作为客户端全部入站目标。
- 这种防直连仅保证“已经进入 Mihomo、且匹配到 AI 规则”的流量。关闭 FlClash、系统 VPN 断开、旁路程序/硬编码 DoH 并不会被普通规则自动拦下。

## DNS 防泄漏

- `dns.enhanced-mode: fake-ip`，`dns.respect-rules: true`，`dns.ipv6: false`，远端 DoH 绑定 `#💬 Ai平台`；这样匹配的 Claude/OpenAI DNS 查询也通过 AI 节点，不会回退到国内 DNS。
- 代理节点域名的引导解析单独使用 `proxy-server-nameserver`（国内 DoH）；这并非 AI 网站解析通道。宽泛的第三方遥测关键字规则未必能被 DNS 域名策略全部覆盖；通过流量日志核查。
- **Windows：** FlClash 启用规则模式、系统代理与 TUN，确认真实内核配置启用了 `auto-route`、`strict-route`、`dns-hijack`。Windows 其他网卡 DNS、多宿主 DNS、浏览器内置 Secure DNS/DoH 都可能绕过普通 UDP/53 劫持。
- **Android：** 在 FlClash 启用 VPN，系统中按需设置“始终开启 VPN / 屏蔽未使用 VPN 的连接”。检查 Android 系统的“私人 DNS”：开着时它不一定被 Mihomo 的 DNS 劫持接管（特别是加密 DNS）。

## WebRTC 与 UDP 防泄漏

**没有一组 Claude 域名规则可以保证 WebRTC 不泄漏。** WebRTC ICE/STUN 能从别的域名和服务器发现地址，而且浏览器可能用未代理的 UDP。

- Windows Chromium/Edge 可以通过企业策略 `WebRtcIPHandling=disable_non_proxied_udp` 限制非代理 UDP；该策略对浏览器配置生效，不是 Mihomo 字段。确认版本支持及组织策略可配置。请参考 Chromium 官方策略文档。
- FlClash 启用 TUN，确认 UDP 流量进入内核；用 [WebRTC 检测](https://ip.net.coffee/webrtc/) 查看是否出现不应公开的公网地址或局域网地址。
- 如果需要**完全禁止 STUN**，可在生成的 YAML 顶部添加可选规则（注意：可能影响语音、视频、游戏联机）：

```yaml
# Optional strict STUN block; only add when consciously accepting breakage.
- AND,((NETWORK,UDP),(DST-PORT,3478)),REJECT
- AND,((NETWORK,UDP),(DST-PORT,5349)),REJECT
- AND,((NETWORK,UDP),(DST-PORT,19302-19309)),REJECT
```

这些端口只能覆盖常见 STUN 端口；WebRTC 可采用其他端口或 TURN/TCP，所以仍需要浏览器侧策略与测试。**默认不启用阻断**，避免破坏 Steam/其他游戏平台实时 UDP。

## 验证步骤

1. 生成 YAML 并检查 `proxy-groups`、`rules`、`dns` 是否存在；`💬 Ai平台`、`🔒 AI 专用` 没有 `DIRECT`。
2. Windows TUN / Android VPN 开启后访问 Claude 和 ChatGPT，确认 FlClash 连接日志的**实际出口**为自建节点；检查 `https://ip.net.coffee/dns/` 与 `https://ip.net.coffee/webrtc/`，还要检查浏览器内置 DoH。
3. **先备份配置**，然后暂时断开 `[SELF]` 节点，确认 Claude 不能回退到本地网络；恢复节点后重复。
4. 在 Steam 下载时核对 `🎮 游戏下载 → DIRECT`，Steam 商店和社区默认走代理。使用 `steam.exe` 的真实连接记录，而不只看测速页面。
5. 如 FlClash 退出时仍需阻断敏感流量，必须配合操作系统防火墙或 Android 的 VPN 锁定功能；普通配置不是 Kill Switch。

相关文档：[Mihomo DNS](https://wiki.metacubex.one/en/config/dns/)、[Mihomo TUN](https://wiki.metacubex.one/en/config/inbound/tun/)、[Anthropic 地址](https://platform.claude.com/docs/en/api/ip-addresses)。
