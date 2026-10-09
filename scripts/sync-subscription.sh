#!/usr/bin/env bash
# shellcheck shell=bash
# =============================================================================
#  sync-subscription.sh —— 机器侧订阅同步（VPS 上每天跑一次）
# -----------------------------------------------------------------------------
#  它【只做四件事】，刻意不做第五件：
#      1) 从 GitHub 下载 CI 构建好的模板 + META.json
#      2) 把模板里的 @NODE_INJECT_POINT@ 换成这台机器的真实节点
#      3) 用 mihomo -t 校验合成后的配置
#      4) 校验通过才原子替换线上产物（失败则线上产物一个字节都不动）
#
#      ★ 它【不】拉上游规则集、【不】编译规则。
#        规则更新的全部工作都在 GitHub Actions（.github/workflows/fleet.yml）。
#
#  ── 为什么规则不能在机器上更新 ──────────────────────────────────────────────
#  机器上的规则集是【持久化文件】，非空就复用。原来那套本地生成 + cron REUSE，
#  让 rule<NN>.list 与 ini 索引错配，静默持续了 10 天，把 76 条游戏平台下载 CDN
#  塞进了「💬 Ai平台」，同时 Anthropic / OpenAI 两个规则集一条都没进产物
#  （详见 acl4ssr-ai 的 OVERVIEW / README）。
#  CI runner 每次都是全新机器，没有历史缓存 —— 「不可能错配」由架构保证，
#  而不是靠记得加 --force。
#
#  用法:
#      bash sync-subscription.sh                 # 正常同步
#      bash sync-subscription.sh --dry-run       # 只校验不替换
#      bash sync-subscription.sh --force         # 忽略 META 判断，强制替换
#      FORCE=1 bash sync-subscription.sh         # 同上
#
#  退出码:  0 已是最新 / 1 同步失败（线上产物未改动）/ 2 参数或环境错误
# =============================================================================
set -Eeuo pipefail

# ---------------------------------------------------------------------------
# 参数
# ---------------------------------------------------------------------------
DRY_RUN=0
FORCE="${FORCE:-0}"
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        --force)   FORCE=1 ;;
        -h|--help) sed -n '2,40p' "$0"; exit 0 ;;
        *) echo "未知参数: $arg" >&2; exit 2 ;;
    esac
done

# ---------------------------------------------------------------------------
# 路径（全部可用环境变量覆盖，便于测试）
# ---------------------------------------------------------------------------
readonly SB_HOME="${SB_ONECLICK_DIR:-/root/sb-oneclick}"
readonly LOG_DIR="${SYNC_LOG_DIR:-${SB_HOME}/logs}"
readonly SUB_DIR="${SB_SUB_DIR:-/etc/sing-box/subscribe}"
readonly NODES_FILE="${SYNC_NODES_FILE:-${SUB_DIR}/proxies}"
readonly OUT_YAML="${SYNC_OUT:-${SUB_DIR}/acl4ssr-game.yaml}"
readonly BACKUP_DIR="${SYNC_BACKUP_DIR:-/root}"

# CI 产物地址（公开仓库；模板不含任何凭据）
readonly REPO_RAW="${SYNC_REPO_RAW:-https://raw.githubusercontent.com/cainiao524/acl4ssr-ai/main/fleet}"
readonly TEMPLATE_URL="${REPO_RAW}/acl4ssr-game.template.yaml"
readonly META_URL="${REPO_RAW}/META.json"

readonly MARKER='@NODE_INJECT_POINT@'
readonly MIHOMO_BIN="${MIHOMO_BIN:-/usr/local/bin/mihomo}"
readonly MIHOMO_VER="${MIHOMO_VER:-v1.19.31}"

TS="$(date +%Y%m%d-%H%M%S)"
WORK="$(mktemp -d /tmp/sync-sub.XXXXXX)"
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

log()  { printf '%s [sync-sub] %s\n' "$(date '+%F %T')" "$*"; }
die()  { printf '%s [sync-sub] FAIL: %s\n' "$(date '+%F %T')" "$*" >&2; exit 1; }

mkdir -p "$LOG_DIR"

# ---------------------------------------------------------------------------
# 0) 前置检查
# ---------------------------------------------------------------------------
[ -s "$NODES_FILE" ] || die "节点文件不存在或为空: $NODES_FILE（请先完成阶段 1/2 生成订阅节点）"
[ -w "$SUB_DIR" ]    || die "输出目录不可写: $SUB_DIR"
command -v curl      >/dev/null 2>&1 || die "缺少 curl"
command -v python3   >/dev/null 2>&1 || die "缺少 python3"

log "开始同步（dry-run=$DRY_RUN force=$FORCE）"

# ---------------------------------------------------------------------------
# 1) 下载模板 + META.json
# ---------------------------------------------------------------------------
log "下载模板: $TEMPLATE_URL"
curl -fsSL --retry 3 --retry-delay 2 --connect-timeout 15 --max-time 120 \
    -o "$WORK/template.yaml" "$TEMPLATE_URL" \
    || die "模板下载失败 —— 线上产物保持不变"
[ -s "$WORK/template.yaml" ] || die "模板下载后为空 —— 线上产物保持不变"
grep -qF "$MARKER" "$WORK/template.yaml" \
    || die "模板里找不到注入标记 $MARKER —— 产物结构变了，拒绝使用（线上产物保持不变）"

if curl -fsSL --retry 2 --connect-timeout 15 --max-time 60 -o "$WORK/META.json" "$META_URL" 2>/dev/null; then
    HAVE_META=1
    log "META: 构建于 $(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["built_at_utc"])' "$WORK/META.json" 2>/dev/null || echo '?')"
else
    HAVE_META=0
    log "WARN: META.json 下载失败，跳过「已是最新」判断，继续走完整校验"
fi

# ---------------------------------------------------------------------------
# 2) 注入真实节点
# ---------------------------------------------------------------------------
# 只替换标记【所在的那一行】：标记是缩进注释，与真实节点条目同级，
# 所以注入后 YAML 结构与 CI 产出的完整版完全一致。
log "注入本地节点: $NODES_FILE"
python3 - "$WORK/template.yaml" "$NODES_FILE" "$WORK/merged.yaml" "$MARKER" <<'PY'
import pathlib, sys

tpl_path, nodes_path, out_path, marker = sys.argv[1:5]
tpl = pathlib.Path(tpl_path).read_text(encoding="utf-8")

# 节点文件形如：
#     proxies:
#       - {name: "...", ...}
#       - {name: "...", ...}
# 只取条目行，丢掉 `proxies:` 头 —— 头在模板里本来就有。
entries, seen_header = [], False
for ln in pathlib.Path(nodes_path).read_text(encoding="utf-8").splitlines():
    s = ln.strip()
    if not s or s.startswith("#"):
        continue
    if s == "proxies:":
        seen_header = True
        continue
    entries.append(ln.rstrip())

if not entries:
    sys.exit("节点文件里没有解析到任何节点条目")
if not seen_header:
    # 没有 `proxies:` 头也接受（有人可能只维护条目），但要说出来
    print("  [WARN] 节点文件缺少 `proxies:` 头，按纯条目解析")

out, hits = [], 0
for ln in tpl.splitlines():
    if marker in ln:
        out.extend(entries)
        hits += 1
    else:
        out.append(ln)

if hits != 1:
    sys.exit("注入标记出现 %d 次（期望 1 次）—— 拒绝生成无法预期的配置" % hits)

pathlib.Path(out_path).write_text("\n".join(out) + "\n", encoding="utf-8")
print("  注入 %d 个节点，产物 %d 行" % (len(entries), len(out)))
PY
[ -s "$WORK/merged.yaml" ] || die "节点注入失败 —— 线上产物保持不变"
if grep -qF "$MARKER" "$WORK/merged.yaml"; then
    die "注入后仍存在标记 —— 注入逻辑有误（线上产物保持不变）"
fi

# ---------------------------------------------------------------------------
# 3) 结构断言（不依赖 mihomo，先做一轮廉价且明确的检查）
# ---------------------------------------------------------------------------
log "结构断言"
python3 - "$WORK/merged.yaml" <<'PY'
import pathlib, sys, re
text = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
problems = []

def members(name):
    m = re.search(r'^  - name: "%s"\n(.*?)(?=^  - name: |\Z)' % re.escape(name),
                  text, re.M | re.S)
    return None if not m else re.findall(r'^      - "(.+)"$', m.group(1), re.M)

for grp in ("🎮 游戏下载", "🎮 游戏平台", "🎮 Steam 商店/社区",
            "💬 Ai平台", "🔒 AI专用", "🚀 自建节点"):
    if members(grp) is None:
        problems.append("缺少策略组: %s" % grp)
    elif not members(grp):
        problems.append("策略组 %s 没有成员" % grp)

steam = members("🎮 游戏下载") or []
if steam[:1] != ["DIRECT"]:
    problems.append("「🎮 游戏下载」首位成员是 %r，必须是 DIRECT（否则下载会走代理吃流量）" % (steam[:1],))

ai = members("💬 Ai平台") or []
if ai != ["🔒 AI专用"]:
    problems.append("「💬 Ai平台」成员为 %r，必须仅指向「🔒 AI专用」" % (ai,))

# ── 自建 / 机场 隔离（2026-10-09）───────────────────────────────────────────
# 防的是一次真实失效：AI 组原用地理正则 (日本|JP|Japan) 过滤，机场节点只要
# 名字含「日本」/「专线」就会被吸进来；而 select 组首位即默认出口 ——
# AI 出口 IP 会悄悄变成机场节点。对 Claude / ChatGPT 账号，这是头号封号信号。
# 边界现在是节点名里的 [SELF]（归属标识），这里把它钉死。
ai_nodes = members("🔒 AI专用") or []
bad = [n for n in ai_nodes if "[SELF]" not in n]
if bad:
    problems.append("「🔒 AI专用」含非自建（缺 [SELF]）节点: %s —— 机场节点可能已污染 AI 出口" % bad)

self_nodes = members("🚀 自建节点") or []
if any("[SELF]" not in n for n in self_nodes):
    problems.append("「🚀 自建节点」含非自建节点: %s" % self_nodes)

jp_nodes = members("🇯🇵 日本节点") or []
bad = [n for n in jp_nodes if "[SELF]" not in n]
if bad:
    problems.append("「🇯🇵 日本节点」混入非自建节点: %s" % bad)

if not re.search(r'^- "MATCH,', text, re.M) and not re.search(r'^  - "MATCH,', text, re.M):
    problems.append("缺少 MATCH 兜底规则")

n_rules = len(re.findall(r'^  - "', text, re.M))
if n_rules < 8000:
    problems.append("规则数只有 %d 条（<8000）—— 疑有规则集未生效" % n_rules)

if problems:
    for p in problems:
        print("  [FAIL] " + p, file=sys.stderr)
    sys.exit(1)
print("  规则 %d 条 / Steam 下载组首位 DIRECT / AI 组钉死" % n_rules)
PY

# ---------------------------------------------------------------------------
# 4) mihomo -t 权威校验（唯一能抓住 ProxyGroup 循环 / 成员缺失的一层）
# ---------------------------------------------------------------------------
ensure_mihomo() {
    [ -x "$MIHOMO_BIN" ] && return 0
    log "本机没有 mihomo，下载 $MIHOMO_VER 用于权威校验"
    local url="https://github.com/MetaCubeX/mihomo/releases/download/${MIHOMO_VER}/mihomo-linux-amd64-${MIHOMO_VER}.gz"
    curl -fsSL --retry 3 --connect-timeout 15 --max-time 240 -o "$WORK/mihomo.gz" "$url" || return 1
    gunzip -c "$WORK/mihomo.gz" > "$WORK/mihomo" || return 1
    install -m 0755 "$WORK/mihomo" "$MIHOMO_BIN" || return 1
    # ⚠️ 不要写成 "$MIHOMO_BIN" -v | head -1：
    #    本脚本是 set -Eeuo pipefail，head 读够一行就退出 -> mihomo 收到 SIGPIPE(141)
    #    -> 整条管道判失败 -> 函数返回 1 -> 调用方误判"拿不到 mihomo"而跳过 -t 校验。
    #    实测撞到过：日志里先是下载成功，紧接着又打 WARN 说无法取得 mihomo。
    #    先存进变量再打印，避免任何上游进程被 SIGPIPE 打断。
    local ver
    ver="$("$MIHOMO_BIN" -v 2>/dev/null | sed -n '1p')" || true
    [ -n "$ver" ] && log "  $ver"
    return 0
}

if ensure_mihomo; then
    log "mihomo -t 校验"
    if ! "$MIHOMO_BIN" -t -d "$WORK/mihomo-dir" -f "$WORK/merged.yaml" > "$WORK/mihomo-test.log" 2>&1; then
        tail -20 "$WORK/mihomo-test.log" | sed 's/^/    /' >&2
        die "mihomo -t 未通过 —— 线上产物保持不变"
    fi
    log "mihomo -t 通过"
else
    # 不因为"校验器拿不到"就阻断更新：结构断言已经跑过，
    # 真正权威的 mihomo -t 在 CI 那一步也跑过同一份规则。
    log "WARN: 无法取得 mihomo，跳过 -t（结构断言已通过）"
fi

# ---------------------------------------------------------------------------
# 5) 原子替换
# ---------------------------------------------------------------------------
# ⚠️ 「是否已是最新」用【逐字节内容比对】而不是 sha256 相等：
#    sha 只回答"一不一样"，而我们要的是"线上那份是不是【本次校验过的】那一份"。
#    这两者会分叉 —— 实测踩过：GitHub raw 的 CDN 缓存还没刷新时，脚本抓到的
#    是上一版模板，于是 sha 与线上相等、报"无需替换"，而线上其实还是旧规则。
#    内容比对 + 落盘后自证，才把"下发件 == 我校验过的件"这句话变成真的。
NEW_SHA="$(sha256sum "$WORK/merged.yaml" | awk '{print $1}')"
OLD_SHA=""
[ -f "$OUT_YAML" ] && OLD_SHA="$(sha256sum "$OUT_YAML" | awk '{print $1}')"

if [ "$FORCE" != "1" ] && [ -f "$OUT_YAML" ] && cmp -s "$WORK/merged.yaml" "$OUT_YAML"; then
    log "线上产物与本次构建【逐字节一致】（sha256=${NEW_SHA:0:16}），无需替换"
    exit 0
fi

if [ "$DRY_RUN" = "1" ]; then
    log "DRY-RUN：校验全部通过，将替换 $OUT_YAML（${OLD_SHA:0:16} -> ${NEW_SHA:0:16}），但本次不落盘"
    exit 0
fi

if [ -f "$OUT_YAML" ]; then
    cp -a "$OUT_YAML" "${BACKUP_DIR}/$(basename "$OUT_YAML").bak-${TS}"
    log "已备份: ${BACKUP_DIR}/$(basename "$OUT_YAML").bak-${TS}"
fi

# 同目录内 mv 是原子的：客户端要么看到旧文件，要么看到新文件，不会读到半个
TMP_OUT="${SUB_DIR}/.acl4ssr-game.yaml.new-${TS}"
cp -a "$WORK/merged.yaml" "$TMP_OUT"
chmod 644 "$TMP_OUT"
mv -f "$TMP_OUT" "$OUT_YAML"

# 自证：落盘的那一份必须与【我刚刚校验过的那一份】逐字节相同。
# 只比 sha 不够 —— cmp 能抓住"内容不同但长度/摘要恰好一致"以外的所有写入问题，
# 也把"文件被别的东西同时改过"这种情况暴露出来。
cmp -s "$WORK/merged.yaml" "$OUT_YAML" \
    || die "落盘后内容与校验件不一致 —— 请检查磁盘或并发写入（线上可能已损坏，备份见 ${BACKUP_DIR}）"

log "完成：$OUT_YAML 已更新（${OLD_SHA:0:16} -> ${NEW_SHA:0:16}，$(grep -cE '^  - "' "$OUT_YAML") 条规则）"
exit 0
