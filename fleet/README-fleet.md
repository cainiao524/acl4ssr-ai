# fleet/ —— 给机器用的编译成品

本目录里的文件**全部由 CI 生成，不要手改**。改了会在下一次 `fleet.yml` 运行时被覆盖。

| 文件 | 是什么 | 谁能用 |
|---|---|---|
| `acl4ssr-game.template.yaml` | mihomo 完整配置，但 `proxies` 段只有一行注入标记 | 机器侧（`scripts/sync-subscription.sh`） |
| `acl4ssr-game.full.yaml` | 同上，但节点是 `proxies.example.yaml` 里的占位假值 | **只用于 CI 自检**（`mihomo -t`、产物契约）。不要拿它当配置用 |
| `META.json` | 上游 ini 与各产物的 sha256、规则数、构建时间 | 机器侧判断"要不要换"、排查"这份产物是哪天的" |
| `proxies.example.yaml` | 占位节点（**不是产物**，是输入） | CI 编译时算策略组成员用 |

---

## 为什么节点要用"模板 + 注入"而不是直接提交完整配置

本仓库是**公开**的。完整配置里有：

- `uuid`（vless 凭据）
- `reality-opts.public-key` / `short-id`
- hysteria2 的 `password`

这些等价于机器口令，**绝不能进公开仓库**（哪怕是私有仓库的分支也不行，见下）。
所以流程拆成两半：

```text
GitHub Actions（公开仓库，无凭据）
    └─ 编译出 template.yaml（proxies 段 = @NODE_INJECT_POINT@）
            ↓ 机器每天下载
机器（凭据只在这里）
    ├─ 读本地 /etc/sing-box/subscribe/proxies（真节点）
    ├─ 把标记那一行换成真节点
    ├─ mihomo -t 校验
    └─ 校验通过才原子替换线上产物
```

**顺带的好处**：策略组里的节点名本来就是公开信息（`🇯🇵 日本 xtls-reality` 这种），
而"哪台机器、什么端口、什么密钥"完全不出机器。

---

## `proxies.example.yaml` 的两条纪律

### 1. 节点 `name` 必须与机器上的**逐字一致**

它不是"节点引用列表"，而是会被**展开成真实节点名**写进策略组。例如
`assets/local-overlay.ini` 里的

```ini
custom_proxy_group=🔒 AI专用`select`(日本|JP|Japan)
```

在产物里会变成

```yaml
- name: "🔒 AI专用"
  proxies:
    - "🇯🇵 日本 xtls-reality"
    - "🇯🇵 日本 hysteria2"
```

名字来自真实节点名 —— CI 必须"见过"这些名字，产物里的 AI 组才不是空组。
机器上改过节点名（`scripts/rename-node.sh`）后，这里要同步改，
否则 CI 的产物契约断言会以「`🔒 AI专用` 没有匹配到任何节点」失败。

### 2. 假值也必须**语法合法**

这不是洁癖，是踩出来的。`mihomo -t` 会真的去校验 REALITY public key：

```text
level=error msg="proxy 0: invalid REALITY public key"
configuration file ... test failed
```

x25519 的公钥必须是 **43 个 base64url 字符**（32 字节）。随手写个
`PLACEHOLDER_PUBLIC_KEY_xxx` 会让"用占位节点做权威校验"这条路直接堵死。
下面那串就是随机生成的真格式假值。

---

## 两个踩过的坑（都是"看着成功、其实没生效"）

### 1. `sha256` 相等 ≠ "线上就是我校验过的那一份"

同步脚本原先这样判断"要不要换"：

```bash
if [ "$NEW_SHA" = "$OLD_SHA" ]; then log "无需替换"; exit 0; fi
```

`sha` 只回答"一不一样"，而脚本真正要知道的是"线上那份**是不是我刚校验过的**那一份"。
两者会分叉。实测撞上：`raw.githubusercontent.com` 的 CDN 还在发上一版模板时，
脚本抓到的模板 → 注入 → 合并件恰好与线上相等 → 报"无需替换"，
而线上其实还是旧规则（旧规则里 `♻️ 自动选择` 仍是 url-test）。

现在改成**逐字节比对**，并且替换后再 `cmp` 自证一次：

```bash
if [ "$FORCE" != "1" ] && cmp -s "$WORK/merged.yaml" "$OUT_YAML"; then
    log "逐字节一致，无需替换"; exit 0
fi
...
cmp -s "$WORK/merged.yaml" "$OUT_YAML" || die "落盘后内容与校验件不一致"
```

代价是每次同步多读一遍文件；换来的是这句话变成真的：
**"下发给客户端的，就是我刚刚用 `mihomo -t` 校验过的那一份。"**

### 2. 发布后约 5 分钟，raw CDN 可能还在发旧内容

CI 提交产物后，`--emit-template` 那一份要等 CDN 刷新才拿得到。同步脚本如果正好
落在窗口里，会抓到旧模板（此时第 1 条的 `cmp` 会正确地要求替换 —— 但内容本身是旧的）。
所以 **cron 才刻意排在构建之后 80 分钟**（CI `04:20` / 同步 `05:40`），
而不是紧随其后。手工执行时如果刚推完产物，等几分钟或加 `--force` 重跑。

---

## 手工复现 CI

```bash
# 1) 拉上游 + 打覆盖层
curl -fsSL -o upstream.ini \
  https://raw.githubusercontent.com/cainiao524/acl4ssr-steam/main/ACL4SSR_Online_Full_GameControl_MultiMode.ini
python assets/overlay_merge.py --base upstream.ini \
  --overlay assets/local-overlay.ini --output patched.ini --report overlay-report.json

# 2) 解析 + 全量重下（--force 是关键）
python assets/acl4ssr_build.py parse --ini patched.ini --parsed-json parsed.json
python assets/acl4ssr_build.py fetch --ini patched.ini --parsed-json parsed.json \
  --rules-dir rules --force

# 3) 生成两份产物
python assets/acl4ssr_build.py build --ini patched.ini --parsed-json parsed.json \
  --stats-json stats.json --rules-dir rules --proxies fleet/proxies.example.yaml \
  --output /tmp/full.yaml
python assets/acl4ssr_build.py build --ini patched.ini --parsed-json parsed.json \
  --rules-dir rules --proxies fleet/proxies.example.yaml \
  --output /tmp/template.yaml --emit-template

# 4) 权威校验
mihomo -t -d /tmp/mihomo-test -f /tmp/full.yaml
```

> 在**本机**复现时，`--force` 不能省。省略它就会掉进那个"文件非空即复用"的坑里，
> 拿到的产物和 CI 不一致 —— 而它**不会报错**，只会静默不同。
