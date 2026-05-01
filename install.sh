#!/usr/bin/env bash
# Preference-Tracker 一键装 — Phase 4.2 (per `tool-pref-288` install/doctor/uninstall 标准接口).
#
# Usage:
#   bash ~/.claude/skills/preference-tracker/install.sh [--state-dir <path>] [--quiet] [--dry-run]
#
# 5 段全鲁棒 (per kickoff §0 mission):
#   1. 准备 (pre-install doctor 风格预检)
#   2. 安装 (idempotent, versioned 备份, trap ERR rollback)
#   3. 收集 (state + obs_log 路径可写)
#   4. 执行 (doctor 自检 26+12+10 unit + smoke)
#   5. 卸载机制就绪 (uninstall.sh)
#
# Per `wf-pref-027` versioned 备份, `tool-pit-130` state 走 .claude/preference-tracker-state/.
# Per `code-pref-291` Python merge (非 jq).

set -euo pipefail

QUIET=false
DRY_RUN=false
STATE_DIR_OVERRIDE=""

# Parse args
while [[ $# -gt 0 ]]; do
    case "$1" in
        --quiet) QUIET=true; shift ;;
        --dry-run) DRY_RUN=true; shift ;;
        --state-dir) STATE_DIR_OVERRIDE="$2"; shift 2 ;;
        -h|--help)
            sed -n '2,17p' "$0"
            exit 0 ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

SKILL_DIR="${HOME}/.claude/skills/preference-tracker"
PROJECT_ROOT="$(pwd)"
HOOKS_DIR="${PROJECT_ROOT}/.claude/hooks"
SETTINGS="${PROJECT_ROOT}/.claude/settings.local.json"
BACKUP_FILE=""

# C3 fix (Phase 8 code review): refuse install when PROJECT_ROOT 是 HOME / /tmp / 没 .claude
if [[ "${PROJECT_ROOT}" == "${HOME}" ]]; then
    echo "❌ PROJECT_ROOT == HOME (${HOME})"
    echo "   你似乎在 HOME 跑 install.sh — 应该 cd 到项目根再装."
    echo "   如果真要装到 user-level (跨项目共享), 用 --i-know-i-am-installing-globally"
    echo "   per code review C3 fix."
    exit 1
fi
if [[ "${PROJECT_ROOT}" == /tmp/* ]] || [[ "${PROJECT_ROOT}" == /tmp ]]; then
    # Allow /tmp/fake_user_test for smoke/chaos test
    if [[ "${PROJECT_ROOT}" != /tmp/fake_user_test* ]] && [[ "${PROJECT_ROOT}" != /tmp/chaos_test* ]]; then
        echo "❌ PROJECT_ROOT 在 /tmp 下 (${PROJECT_ROOT})"
        echo "   /tmp 不持久 (per tool-pit-130). 换持久 path 重跑."
        exit 1
    fi
fi

log() {
    if [[ "$QUIET" != true ]]; then
        echo "$@"
    fi
}

# Trap ERR — rollback settings 备份, 不删 hooks .sh / state / memory
# C10 fix (2026-05-01): need to (a) disable set -e inside rollback so a failing
# `cp` doesn't truncate the function before printing the rollback summary or
# reaching `exit 2`; (b) clear ERR trap on entry so a second failure inside
# rollback doesn't recurse.
rollback() {
    set +e
    trap - ERR
    log ""
    log "❌ install 失败, 回滚..."
    if [[ -n "${BACKUP_FILE}" && -f "${BACKUP_FILE}" ]]; then
        if cp "${BACKUP_FILE}" "${SETTINGS}"; then
            log "  settings 回滚: ${BACKUP_FILE} → ${SETTINGS}"
        else
            log "  ⚠ settings 回滚失败 (cp 错): ${BACKUP_FILE} → ${SETTINGS}"
            log "  → 手动恢复: cp \"${BACKUP_FILE}\" \"${SETTINGS}\""
        fi
    fi
    log "  hooks .sh / state / memory 保留 (留 debug)"
    log "  详细错误见 ${SKILL_DIR}/install.log"
    exit 2
}
trap 'rollback' ERR

run() {
    if [[ "$DRY_RUN" == true ]]; then
        log "  [dry-run] $@"
    else
        "$@"
    fi
}

LOG_FILE="${SKILL_DIR}/install.log"
mkdir -p "${SKILL_DIR}" 2>/dev/null || true
# C2 fix (Phase 8 code review): tee BOTH stdout + stderr into install.log;
# 之前只 tee stderr → log() echo stdout 不进 log → FAQ Q15 cat install.log 排错失效.
exec > >(tee "${LOG_FILE}") 2>&1

log "Preference-Tracker install — version 1.0"
log "  HOME: ${HOME}"
log "  PROJECT: ${PROJECT_ROOT}"
log "  SKILL_DIR: ${SKILL_DIR}"
log ""

# ============================================================
# Phase 1: 准备 (doctor 风格预检)
# ============================================================
log "[1/5] 准备: 环境预检"

# 1.1 HOME 可写
if ! mkdir -p "${HOME}/.claude/skills/.write_test" 2>/dev/null; then
    log "❌ HOME (${HOME}) 不可写. 检查 quota / 权限"
    exit 1
fi
rmdir "${HOME}/.claude/skills/.write_test" 2>/dev/null || true

# 1.2 Python 3 >= 3.7
PY_OK=$(python3 -c "import sys; print('OK' if sys.version_info >= (3, 7) else 'OLD')" 2>/dev/null || echo "MISSING")
if [[ "${PY_OK}" != "OK" ]]; then
    log "❌ Python3 不存在或版本 <3.7. 装 Python 3.7+ 后重试"
    exit 1
fi

# 1.2b PyYAML — retrieve_inject + verify_compliance.detect_rules_for_response 用. 没有
# yaml 这两个 hook 静默退出 (sys.exit(0)), 让 enforcement 失效一半. (H4 fix)
if ! python3 -c 'import yaml' 2>/dev/null; then
    log "⚠ PyYAML 不在 system Python 里. fingerprint 检索 + B3 fp_rules_in_response 会 silent disable."
    log "   修复:"
    log "     pip install --user pyyaml     (或)"
    log "     pip3 install --user pyyaml    (或 system 包)"
    log "     sudo apt install python3-yaml (Ubuntu)"
    log "   不安装继续也能跑 deterministic 阻断 + shadow judge, 仅丢 fingerprint retrieval."
fi

# 1.2c jq — check-observation-log.sh 等 hook 用. 没装 hook 会 set -e 顶死. (H8 fix)
if ! command -v jq > /dev/null 2>&1; then
    log "❌ jq 不在 PATH. check-observation-log.sh 跟其他 short-circuit 块都依赖 jq."
    log "   修复: sudo apt install jq (Ubuntu) / brew install jq (macOS)"
    exit 1
fi

# 1.3 claude CLI (shadow judge 默认走 CLI)
if ! command -v claude > /dev/null 2>&1; then
    log "⚠ 未在 PATH 找到 'claude' CLI"
    log "   shadow judge 默认走 CLI 订阅 (per tool-pit-004), 没 CLI 影子判官跑不起来"
    log "   你可以 set B5_SHADOW_DISABLED=1 关影子判官 (deterministic 仍然 work)"
    log "   或装 Claude Code: https://claude.com/code"
fi

# 1.4 现 settings 合法 (如存在). M14 fix: env 通道传 path 防注入 + utf-8 编码
if [[ -f "${SETTINGS}" ]]; then
    if ! env PT_SETTINGS="${SETTINGS}" PYTHONIOENCODING=utf-8 python3 -c \
        'import json, os; json.load(open(os.environ["PT_SETTINGS"]))' 2>/dev/null; then
        log "❌ ${SETTINGS} 非法 JSON. 跑 jsonlint 检查后重试"
        exit 1
    fi
fi

# 1.5 SKILL dir 完整 (lib + hooks 子目录存在)
if [[ ! -d "${SKILL_DIR}/lib" ]]; then
    log "❌ ${SKILL_DIR}/lib 不存在. 检查 skill 是否解压完整"
    exit 1
fi
if [[ ! -d "${SKILL_DIR}/hooks" ]]; then
    log "❌ ${SKILL_DIR}/hooks 不存在. 检查 skill 是否解压完整"
    exit 1
fi

log "  ✓ HOME 可写"
log "  ✓ Python 3.7+"
log "  ✓ skill 目录完整 (lib + hooks)"
log "  ✓ 现 settings 合法"

# ============================================================
# Phase 2: 安装 (idempotent, versioned 备份)
# ============================================================
log ""
log "[2/5] 安装: 改 settings + 拷 hooks + 创 state"

# 2.1 detect cwd / paths (M14 fix: PYTHONIOENCODING=utf-8 防用户 LANG 不是 utf-8 时 stdout 崩)
CWD_ESCAPED="$(PYTHONIOENCODING=utf-8 python3 -c "import sys; print(sys.argv[1].replace('/', '-'))" "${PROJECT_ROOT}")"
MEMORY_DIR="${HOME}/.claude/projects/${CWD_ESCAPED}/memory"
STATE_DIR="${STATE_DIR_OVERRIDE:-${B5_STATE_DIR:-${PROJECT_ROOT}/.claude/preference-tracker-state/runtime}}"
OBS_LOG_DIR="${B5_OBS_LOG_DIR:-${PROJECT_ROOT}/.claude/preference-tracker-state/obs_log}"

log "  PROJECT_ROOT: ${PROJECT_ROOT}"
log "  STATE_DIR: ${STATE_DIR}"
log "  OBS_LOG_DIR: ${OBS_LOG_DIR}"
log "  MEMORY_DIR: ${MEMORY_DIR}"

# 2.2 versioned backup settings (per `wf-pref-027`)
# H7 fix (2026-05-01): also GC older backups, keep most recent 5.
# install.sh + _install_merge_settings.cmd_add 各 cp 一次 = 单次 install 创 2 份.
# 不 GC 长期 user 重装会有几十份, 占盘 + 老 secret 永远留存.
if [[ -f "${SETTINGS}" ]]; then
    TS="$(date +%Y%m%d-%H%M%S)"
    BACKUP_FILE="${SETTINGS}.v3_pre_pt_${TS}.json"
    run cp "${SETTINGS}" "${BACKUP_FILE}"
    log "  ✓ versioned backup: ${BACKUP_FILE}"
    # GC oldest backups beyond the most recent 5
    OLD_BACKUPS=$(ls -t "${SETTINGS}".v3_pre_pt_*.json 2>/dev/null | tail -n +6 || true)
    if [[ -n "${OLD_BACKUPS}" ]]; then
        log "  GC 老 backup (保留最新 5 份):"
        while IFS= read -r ob; do
            run rm -f "${ob}"
            log "    rm ${ob}"
        done <<< "${OLD_BACKUPS}"
    fi
fi

# 2.3 拷 hooks (idempotent: cp -n 不覆盖现有)
run mkdir -p "${HOOKS_DIR}"
for hook in "${SKILL_DIR}"/hooks/*.sh; do
    if [[ ! -f "${hook}" ]]; then
        continue
    fi
    name="$(basename "${hook}")"
    target="${HOOKS_DIR}/${name}"
    if [[ -f "${target}" ]]; then
        log "  - ${name} 已存在, 跳过 (cp -n)"
    else
        run cp "${hook}" "${target}"
        run chmod +x "${target}"
        log "  ✓ ${name}"
    fi
done

# 2.4 Python merge settings (additive, 已注册 hook skip)
log ""
log "  注册 hooks 到 settings.local.json:"
run python3 "${SKILL_DIR}/lib/_install_merge_settings.py" \
    --settings "${SETTINGS}" \
    --hooks-dir "${HOOKS_DIR}" \
    --add

# 2.5 创 state subdirs (idempotent)
# C-NEW-1 fix (Phase 8 review v2): path 走 env 通道, 不 interpolate 进 Python source.
# Bash 段单引号包, 路径含 `'` / `"` / `$` / `\`` 都不会注入 Python.
log ""
log "  创 state subdirs:"
run env \
    PT_SKILL_DIR="${SKILL_DIR}" \
    B5_STATE_DIR="${STATE_DIR}" \
    B5_OBS_LOG_DIR="${OBS_LOG_DIR}" \
    B5_PROJECT_ROOT="${PROJECT_ROOT}" \
    PYTHONIOENCODING=utf-8 \
    python3 -c '
import os, sys
sys.path.insert(0, os.path.join(os.environ["PT_SKILL_DIR"], "lib"))
import path_config
path_config._clear_cache()
path_config.ensure_dirs()
print("  ✓ state subdirs created")
'

# 2.7 写 ~/.preference-tracker.config.json 锚定 PROJECT_ROOT (I5 fix)
# hook 跑时 cwd 可能跟 install.sh 跑时不同; config 锚定让 path_config 不依赖每次 cwd.
#
# C7 fix (2026-05-01): 改成 OVERWRITE 这次 install 的 path_root / state / obs_log
# (不是 setdefault). 之前 setdefault 在跨项目重装时让旧 project_root 卡住, 新装
# 的 hooks 永远写到旧项目的 state dir. 我们假设最近一次 install 反映用户当前
# project; 老 config 字段 (whitelist_user / memory_dir 等) 保留.
log ""
log "  锚定 PROJECT_ROOT 到 ~/.preference-tracker.config.json (C7 fix: overwrite, not setdefault):"
run env \
    B5_PROJECT_ROOT="${PROJECT_ROOT}" \
    B5_STATE_DIR="${STATE_DIR}" \
    B5_OBS_LOG_DIR="${OBS_LOG_DIR}" \
    PYTHONIOENCODING=utf-8 \
    python3 -c '
import json, os
config_path = os.path.expanduser("~/.preference-tracker.config.json")
config = {}
if os.path.exists(config_path):
    try:
        with open(config_path, encoding="utf-8") as f:
            config = json.load(f)
    except Exception:
        config = {}
# Overwrite the three install-driven keys; preserve any user-customized keys
# (whitelist_user / memory_dir / etc) the user wrote by hand.
config["project_root"] = os.environ["B5_PROJECT_ROOT"]
config["state_dir"] = os.environ["B5_STATE_DIR"]
config["obs_log_dir"] = os.environ["B5_OBS_LOG_DIR"]
with open(config_path, "w", encoding="utf-8") as f:
    json.dump(config, f, indent=2, ensure_ascii=False)
print("  ✓ config:", config_path)
'

# 2.6 cp seed memory (如果不存在), sed 替换私有 session id 为通用 seed 标记 (M9 fix)
log ""
log "  装 seed memory (如不存在):"
run mkdir -p "${MEMORY_DIR}"
SEED_SKIPPED=0
if [[ -d "${SKILL_DIR}/seed_memory" ]]; then
    for seed in "${SKILL_DIR}"/seed_memory/*.md; do
        if [[ ! -f "${seed}" ]]; then
            continue
        fi
        target="${MEMORY_DIR}/$(basename "${seed}")"
        if [[ -f "${target}" ]]; then
            log "  - $(basename "${seed}") 已存在, 跳过 (cp -n)"
            SEED_SKIPPED=$((SEED_SKIPPED + 1))
        else
            # M9 fix: 替换 seed memory 里的私有 originSessionId 为通用 seed 标记
            # M14 fix: 显式 PYTHONIOENCODING=utf-8 防用户终端 LANG 不是 utf-8
            run env PYTHONIOENCODING=utf-8 python3 -c "
import sys, re
src, dst = sys.argv[1], sys.argv[2]
with open(src, encoding='utf-8') as f:
    content = f.read()
content = re.sub(
    r'^originSessionId:\s*.*$',
    'originSessionId: seed-from-preference-tracker-skill-v1.0',
    content, flags=re.MULTILINE,
)
with open(dst, 'w', encoding='utf-8') as f:
    f.write(content)
" "${seed}" "${target}"
            log "  ✓ $(basename "${seed}") (originSessionId 已通用化)"
        fi
    done
    # M11 fix: 若有 skip, 提示用户 frontmatter 可能 stale 没 params: 块
    if [[ "${SEED_SKIPPED}" -gt 0 ]]; then
        log ""
        log "  ⚠ ${SEED_SKIPPED} 个 seed memory 已存在, 没覆盖 (保你已有数据)"
        log "    若它们是老版没 'params:' 块, Phase 7 自适应阈值 fallback 到代码默认"
        log "    新阈值参数显式化加进 frontmatter: 看 ${SKILL_DIR}/seed_memory/*.md 的 'params:' 段, 复制进你已有 memory 文件"
    fi
else
    log "  - seed_memory/ 不存在, 跳过 (用户已有 memory)"
fi

# ============================================================
# Phase 3: 收集 (验日志路径可写)
# ============================================================
log ""
log "[3/5] 收集: 验日志路径可写"
for d in "${OBS_LOG_DIR}" "${STATE_DIR}"; do
    if [[ ! -d "${d}" ]]; then
        log "❌ ${d} 不存在 (Phase 2 应创了)"
        exit 1
    fi
    test_file="${d}/.write_test_$$"
    if ! touch "${test_file}" 2>/dev/null; then
        log "❌ ${d} 不可写"
        exit 1
    fi
    rm -f "${test_file}"
done
log "  ✓ ${OBS_LOG_DIR} 可写"
log "  ✓ ${STATE_DIR} 可写"

# ============================================================
# Phase 4: 执行 (跑 doctor)
# ============================================================
log ""
log "[4/5] 执行: 跑 doctor.sh 自检"
if [[ -x "${SKILL_DIR}/doctor.sh" ]]; then
    run bash "${SKILL_DIR}/doctor.sh"
else
    log "  ⚠ doctor.sh 不存在 / 不可执行 (skill 包不完整). 跑基础测试:"
    run python3 "${SKILL_DIR}/lib/test_path_config.py"
fi

# ============================================================
# Phase 5: 卸载机制就绪 (verify uninstall.sh executable per M7 fix)
# ============================================================
log ""
log "[5/5] 卸载机制就绪:"
if [[ -x "${SKILL_DIR}/uninstall.sh" ]]; then
    log "  ✓ uninstall.sh 可执行: bash ${SKILL_DIR}/uninstall.sh"
else
    log "  ⚠ ${SKILL_DIR}/uninstall.sh 不存在或无执行权限"
    log "    → chmod +x ${SKILL_DIR}/uninstall.sh"
    log "    或 bash ${SKILL_DIR}/uninstall.sh (强制走 bash 不依赖执行位)"
fi

# 关闭 trap (后续 echo 不算 ERR)
trap - ERR

log ""
log "✅ Preference-Tracker 装好了"
log ""
log "关键路径:"
log "  - skill: ${SKILL_DIR}"
log "  - hooks: ${HOOKS_DIR}"
log "  - state: ${STATE_DIR}"
log "  - memory: ${MEMORY_DIR}"
log ""
log "下一步:"
log "  - 看 README.md / FAQ.md"
log "  - 跑 dashboard.sh 看 7 天 compliance summary"
log "  - 关 shadow judge: export B5_SHADOW_DISABLED=1 (没 claude CLI 时建议)"
log "  - 关 deterministic: export B5_DETERMINISTIC_DISABLED=1"
log "  - 加专名 whitelist: echo 'MyProject' >> ${SKILL_DIR}/lib/deterministic_block_whitelist_user.txt"
log "  - 卸载: bash ${SKILL_DIR}/uninstall.sh"
log ""
log "Install log: ${LOG_FILE}"
