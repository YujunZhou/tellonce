#!/usr/bin/env python3
"""pt_mode — dead-simple on/off switch for preference-tracker.

No env vars, no hand-editing JSON. One command flips the mode by writing
`enforce` / `shadow` into ~/.preference-tracker.config.json (preserving every
other key). path_config reads those keys, so the change takes effect next run.

Usage (run with `python pt_mode.py <mode>`):
    status      show current mode (default when no arg)
    observe     SAFE default — record + remind only; no hard block, no LLM
    enforce     turn ON hard blocking (still no LLM judge)
    full        turn ON hard blocking AND the shadow LLM judge
    block on|off    granular: just the hard-blocking switch
    shadow on|off   granular: just the LLM-judge switch

Examples:
    python pt_mode.py            -> shows current mode
    python pt_mode.py enforce    -> hard blocking on
    python pt_mode.py observe    -> back to safe default
"""
import json
import os
import sys

_LIB_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _LIB_DIR)
import path_config  # single source of truth for CONFIG_PATH

CONFIG_PATH = path_config.CONFIG_PATH


def _load():
    if not os.path.exists(CONFIG_PATH):
        return {}
    try:
        with open(CONFIG_PATH, encoding='utf-8-sig') as f:
            return json.load(f)
    except Exception:
        return {}


def _save(cfg):
    # Atomic write (tmp + replace) so an interrupted write can't corrupt the
    # config. Write without BOM (utf-8) — readers use utf-8-sig and tolerate both.
    import tempfile
    d = os.path.dirname(CONFIG_PATH) or '.'
    fd, tmp = tempfile.mkstemp(prefix='.pt-config-', suffix='.tmp', dir=d)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
            f.write('\n')
        os.replace(tmp, CONFIG_PATH)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _on(v):
    return str(v).strip().lower() in ('1', 'true', 'yes', 'on')


def _print_status(cfg):
    enforce = bool(cfg.get('enforce', False))
    shadow = bool(cfg.get('shadow', False))
    if enforce and shadow:
        mode = 'full     (硬拦截 + AI 判官)'
    elif enforce:
        mode = 'enforce  (硬拦截，无 AI 判官)'
    else:
        mode = 'observe  (安全默认：只记录+提醒，不拦不调 LLM)'
    print(f'preference-tracker 当前模式: {mode}')
    print(f'  enforce(硬拦截) = {enforce}')
    print(f'  shadow(AI判官)  = {shadow}')
    print(f'  配置文件: {CONFIG_PATH}')


def _apply(cfg, enforce=None, shadow=None):
    if enforce is not None:
        cfg['enforce'] = bool(enforce)
    if shadow is not None:
        cfg['shadow'] = bool(shadow)
    _save(cfg)
    return cfg


def main(argv):
    cfg = _load()
    args = [a.lower() for a in argv]

    if not args or args[0] == 'status':
        _print_status(cfg)
        return 0

    cmd = args[0]
    if cmd == 'observe':
        cfg = _apply(cfg, enforce=False, shadow=False)
        print('✅ 已切到 observe（安全默认：只记录+提醒，不拦截、不调用 LLM）。')
    elif cmd == 'enforce':
        cfg = _apply(cfg, enforce=True, shadow=False)
        print('✅ 已开启 enforce（硬拦截）。AI 判官仍关闭。')
    elif cmd == 'full':
        cfg = _apply(cfg, enforce=True, shadow=True)
        print('✅ 已开启 full（硬拦截 + AI 判官）。注意：AI 判官会把对话发给 copilot -p 判分。')
    elif cmd in ('block', 'shadow') and len(args) >= 2 and args[1] in ('on', 'off', 'true', 'false', '1', '0', 'yes', 'no'):
        val = _on(args[1])
        if cmd == 'block':
            cfg = _apply(cfg, enforce=val)
            print(f'✅ 硬拦截(enforce) = {val}')
        else:
            cfg = _apply(cfg, shadow=val)
            print(f'✅ AI 判官(shadow) = {val}')
    else:
        print(__doc__)
        print(f'\n无法识别的命令: {" ".join(argv)}')
        return 2

    print()
    _print_status(cfg)
    return 0


if __name__ == '__main__':
    try:
        for _s in (sys.stdout, sys.stderr):
            try:
                _s.reconfigure(encoding='utf-8')
            except Exception:
                pass
        sys.exit(main(sys.argv[1:]))
    except Exception as e:
        sys.stderr.write(f'pt_mode error: {e}\n')
        sys.exit(1)
