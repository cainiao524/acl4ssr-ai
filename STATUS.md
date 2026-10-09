# STATUS

> 本文件由 `check.py` 每个运行日自动写入 —— 请勿手改。
>
> 它的存在有两个作用：
> 1. **让定时工作流活下去。** GitHub 会在仓库连续 60 天没有提交后自动禁用
>    定时工作流。本仓库的 ini 全是远程引用，上游改了它常常不变，
>    所以需要一份每日都会变的文件来产生提交。
> 2. **提前发现上游规则集挂掉。** 这里对每条被引用的 URL 做一次可达性检查，
>    任何一个 404 都会让订阅转换时少一批规则。

| | |
|---|---|
| 最近巡检 | 2026-10-09 11:12 UTC |
| 巡检脚本 | `check.py` v1.0.0 |
| 被引用规则集 | **42** 条 |
| 不可达 | **0** 条 |

## ✅ 全部规则集可达

所有被引用的远程规则集都返回 2xx。

## 全部规则集

| 策略组 | URL | HTTP | 大小 |
|---|---|---|---|
| `🎯 全球直连` | `ACL4SSR/ACL4SSR/master/Clash/LocalAreaNetwork.list` | 200 | 1.2 KB |
| `🎯 全球直连` | `ACL4SSR/ACL4SSR/master/Clash/UnBan.list` | 200 | 1.0 KB |
| `🛑 广告拦截` | `ACL4SSR/ACL4SSR/master/Clash/BanAD.list` | 200 | 15.2 KB |
| `🍃 应用净化` | `ACL4SSR/ACL4SSR/master/Clash/BanProgramAD.list` | 200 | 31.9 KB |
| `📢 谷歌FCM` | `ACL4SSR/ACL4SSR/master/Clash/Ruleset/GoogleFCM.list` | 200 | 1.6 KB |
| `🎯 全球直连` | `ACL4SSR/ACL4SSR/master/Clash/GoogleCN.list` | 200 | 1.0 KB |
| `Ⓜ️ 微软Bing` | `ACL4SSR/ACL4SSR/master/Clash/Bing.list` | 200 | 0.2 KB |
| `Ⓜ️ 微软云盘` | `ACL4SSR/ACL4SSR/master/Clash/OneDrive.list` | 200 | 0.5 KB |
| `Ⓜ️ 微软服务` | `ACL4SSR/ACL4SSR/master/Clash/Microsoft.list` | 200 | 2.3 KB |
| `🍎 苹果服务` | `ACL4SSR/ACL4SSR/master/Clash/Apple.list` | 200 | 0.9 KB |
| `📲 电报消息` | `ACL4SSR/ACL4SSR/master/Clash/Telegram.list` | 200 | 0.5 KB |
| `💬 Ai平台` | `ACL4SSR/ACL4SSR/master/Clash/Ruleset/AI.list` | 200 | 1.4 KB |
| `💬 Ai平台` | `ACL4SSR/ACL4SSR/master/Clash/Ruleset/OpenAi.list` | 200 | 0.5 KB |
| `💬 Ai平台` | `jsdelivr:VPSDance/ai-proxy-rules@main/rules/surge/anthropic.list` | 200 | - |
| `💬 Ai平台` | `jsdelivr:VPSDance/ai-proxy-rules@main/rules/surge/openai.list` | 200 | - |
| `🎶 网易音乐` | `ACL4SSR/ACL4SSR/master/Clash/Ruleset/NetEaseMusic.list` | 200 | 1.3 KB |
| `🎮 游戏下载` | `cainiao524/acl4ssr-steam/main/rules/GameDownload.list` | 200 | 2.7 KB |
| `🎮 Steam 商店/社区` | `cainiao524/acl4ssr-steam/main/rules/GameSteamWeb.list` | 200 | 1.1 KB |
| `🎮 游戏平台` | `blackmatrix7/ios_rule_script/master/rule/Clash/Steam/Steam.list` | 200 | 2.0 KB |
| `🎮 游戏平台` | `blackmatrix7/ios_rule_script/master/rule/Clash/SteamCN/SteamCN.list` | 200 | 0.6 KB |
| `🎮 游戏平台` | `blackmatrix7/ios_rule_script/master/rule/Clash/Epic/Epic.list` | 200 | 0.6 KB |
| `🎮 游戏平台` | `blackmatrix7/ios_rule_script/master/rule/Clash/EA/EA.list` | 200 | 5.2 KB |
| `🎮 游戏平台` | `blackmatrix7/ios_rule_script/master/rule/Clash/Blizzard/Blizzard.list` | 200 | 2.2 KB |
| `🎮 游戏平台` | `blackmatrix7/ios_rule_script/master/rule/Clash/UBI/UBI.list` | 200 | 0.3 KB |
| `🎮 游戏平台` | `blackmatrix7/ios_rule_script/master/rule/Clash/Xbox/Xbox.list` | 200 | 1.3 KB |
| `🎮 游戏平台` | `blackmatrix7/ios_rule_script/master/rule/Clash/PlayStation/PlayStation.list` | 200 | 0.3 KB |
| `🎮 游戏平台` | `blackmatrix7/ios_rule_script/master/rule/Clash/Nintendo/Nintendo.list` | 200 | 4.0 KB |
| `🎮 游戏平台` | `blackmatrix7/ios_rule_script/master/rule/Clash/Riot/Riot.list` | 200 | 1.8 KB |
| `🎮 游戏平台` | `blackmatrix7/ios_rule_script/master/rule/Clash/Rockstar/Rockstar.list` | 200 | 0.4 KB |
| `🎮 游戏平台` | `blackmatrix7/ios_rule_script/master/rule/Clash/Gog/Gog.list` | 200 | 0.3 KB |
| `🎮 游戏平台` | `blackmatrix7/ios_rule_script/master/rule/Clash/Garena/Garena.list` | 200 | 0.6 KB |
| `📹 油管视频` | `ACL4SSR/ACL4SSR/master/Clash/Ruleset/YouTube.list` | 200 | 0.4 KB |
| `🎥 奈飞视频` | `ACL4SSR/ACL4SSR/master/Clash/Ruleset/Netflix.list` | 200 | 1.3 KB |
| `📺 巴哈姆特` | `ACL4SSR/ACL4SSR/master/Clash/Ruleset/Bahamut.list` | 200 | 0.2 KB |
| `📺 哔哩哔哩` | `ACL4SSR/ACL4SSR/master/Clash/Ruleset/BilibiliHMT.list` | 200 | 0.8 KB |
| `📺 哔哩哔哩` | `ACL4SSR/ACL4SSR/master/Clash/Ruleset/Bilibili.list` | 200 | 0.6 KB |
| `🌏 国内媒体` | `ACL4SSR/ACL4SSR/master/Clash/ChinaMedia.list` | 200 | 1.3 KB |
| `🌍 国外媒体` | `ACL4SSR/ACL4SSR/master/Clash/ProxyMedia.list` | 200 | 11.2 KB |
| `🚀 节点选择` | `ACL4SSR/ACL4SSR/master/Clash/ProxyGFWlist.list` | 200 | 186.6 KB |
| `🎯 全球直连` | `ACL4SSR/ACL4SSR/master/Clash/ChinaDomain.list` | 200 | 16.8 KB |
| `🎯 全球直连` | `ACL4SSR/ACL4SSR/master/Clash/ChinaCompanyIp.list` | 200 | 7.1 KB |
| `🎯 全球直连` | `ACL4SSR/ACL4SSR/master/Clash/Download.list` | 200 | 0.9 KB |

