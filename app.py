from collections import Counter

from flask import Flask, flash, session, render_template, request, redirect, url_for
import logging
import os
import random

app = Flask(__name__)
app.secret_key = "your_secret_key_here"  # 必须设置，用于 session
logger = logging.getLogger(__name__)

# 普通牌：4 套；督牌：每种整局只出现 1 张
BASE_TILES = [
    "一万", "二万", "三万", "四万", "五万", "六万", "七万", "八万", "九万",
    "一筒", "二筒", "三筒", "四筒", "五筒", "六筒", "七筒", "八筒", "九筒",
    "一条", "二条", "三条", "四条", "五条", "六条", "七条", "八条", "九条",
    "乌龟", "毛", "千万",
]
DU_TILES_UNIQUE = ["万督", "筒督", "条督", "妖督", "总督"]

# 碰 / 磕 / 拢时该牌面得分 ×2（含开牌再加一倍基数；以明面主牌 meld[0] 为准）
SPECIAL_DOUBLE_TILES = frozenset({"两筒", "七万", "八万", "九条", "千万"})


def _is_special_double_tile(tile):
    return _normalize_tile_name(tile) in SPECIAL_DOUBLE_TILES


def _special_meld_score_multiplier(tile):
    return 2 if _is_special_double_tile(tile) else 1


def _ke_long_score_multiplier(tile):
    return _special_meld_score_multiplier(tile)


def _pong_immediate_score(tile):
    return 10 * _special_meld_score_multiplier(tile)


def _is_du_tile(tile):
    return bool(tile) and "督" in tile

def _suit_du_for(tile):
    """数牌对应花色督：六万->万督，三筒->筒督，九条->条督；非数牌返回 None。"""
    if not tile:
        return None
    if tile.endswith("万"):
        return "万督"
    if tile.endswith("筒"):
        return "筒督"
    if tile.endswith("条"):
        return "条督"
    return None


def _valid_substitute_for_tile(tile, substitute):
    """督牌能否代替该牌面（磕、碰共用）：总督任意；花色督同门；妖督仅杂牌或一万/一筒/一条。"""
    tile = _normalize_tile_name(tile)
    substitute = _normalize_tile_name(substitute)
    if substitute not in DU_TILES_UNIQUE:
        return False
    if substitute == "总督":
        return True
    if substitute == "妖督":
        return tile in _MISC_ORDER or _is_yi_rank_tile(tile)
    suit_du = _suit_du_for(tile)
    return suit_du is not None and substitute == suit_du


def _pick_pong_substitute(hand, tile):
    """手牌仅一张同点时，选一张可用的督牌凑碰。"""
    for du in _DU_ORDER:
        if _hand_count(hand, du) >= 1 and _valid_substitute_for_tile(tile, du):
            return du
    return None


def _can_ke(hand, tile, substitute=None):
    """磕：3 张同点，或 2 张同点 + 1 张可用督（总督可代任意牌）。"""
    tile = _normalize_tile_name(tile)
    if not tile:
        return False
    if substitute:
        sub = _normalize_tile_name(substitute)
        return (
            _hand_count(hand, tile) >= 2
            and _hand_count(hand, sub) >= 1
            and _valid_substitute_for_tile(tile, sub)
        )
    if _hand_count(hand, tile) >= 3:
        return True
    return _hand_count(hand, tile) >= 2 and _pick_pong_substitute(hand, tile) is not None


def _resolve_ke_substitute(hand, tile, substitute_raw=""):
    """
    确定磕牌用的督。
    返回 None = 无刻督（纯三张同点）；否则为所用督牌名；False = 不能磕。
    优先级：手牌 ≥3 张同点 → 一律不用督；否则才用表单督或自动 2+督。
    """
    tile = _normalize_tile_name(tile)
    if _hand_count(hand, tile) >= 3:
        return None
    raw = (substitute_raw or "").strip()
    if raw:
        sub = _normalize_tile_name(raw)
        if _can_ke(hand, tile, sub):
            return sub
        return False
    auto = _pick_pong_substitute(hand, tile)
    if _hand_count(hand, tile) >= 2 and auto:
        return auto
    return False


def _ke_failure_message(hand, tile):
    tile = _normalize_tile_name(tile)
    n = _hand_count(hand, tile)
    if n >= 3:
        return "无法磕牌，条件不满足"
    if n == 2:
        auto = _pick_pong_substitute(hand, tile)
        if auto:
            return (
                f"你有 2 张「{tile}」：磕牌须再配一张督"
                f"（已可自动用「{auto}」，请再点一次「磕牌」或在「督牌」里选「{auto}」）"
            )
        return (
            f"你有 2 张「{tile}」，磕牌还需一张督"
            f"（总督可代任意牌；杂牌常用妖督，数牌用对应花色督）"
        )
    if n == 1 and _pick_pong_substitute(hand, tile):
        return (
            f"只有 1 张「{tile}」时，须在「督牌」里选一张督"
            f"（如总督）凑成刻子再磕"
        )
    return f"手牌须 3 张「{tile}」，或 2 张「{tile}」+ 一张可用督，才能磕牌"


def _can_pong(hand, tile):
    """河牌 + 手牌两张同点（第二张可为督）能否碰。"""
    tile = _normalize_tile_name(tile)
    if _hand_count(hand, tile) >= 2:
        return True
    return _hand_count(hand, tile) >= 1 and _pick_pong_substitute(hand, tile) is not None


def new_shuffled_draw_pile():
    pile = BASE_TILES * 4 + list(DU_TILES_UNIQUE)
    random.shuffle(pile)
    return pile

# 手牌排序：万 → 筒 → 条 → 杂（乌龟、毛、千万）→ 督；类内按点数一二…九
_RANK_CHARS = "一二三四五六七八九"
_MISC_ORDER = ("乌龟", "毛", "千万")
_DU_ORDER = ("万督", "筒督", "条督", "妖督", "总督")

# 游戏内牌名 → static/切分 扫描图（条=索、妖督图文件名幺督、总督=总）
_TILE_SCAN_PATHS = {
    "乌龟": "切分/杂/乌龟.jpeg",
    "毛": "切分/杂/毛.jpeg",
    "千万": "切分/杂/千万.jpeg",
    "万督": "切分/督/万督.jpeg",
    "筒督": "切分/督/筒督.jpeg",
    "条督": "切分/督/索督.jpeg",
    "妖督": "切分/督/幺督.jpeg",
    "总督": "切分/督/总.jpeg",
}


def tile_scan_static_path(tile):
    """返回 static 目录下切分 JPEG 的相对路径；未知牌面返回 None。"""
    tile = _normalize_tile_name(tile)
    if not tile:
        return None
    if tile in _TILE_SCAN_PATHS:
        return _TILE_SCAN_PATHS[tile]
    if tile.endswith("万"):
        return f"切分/万/{tile}.jpeg"
    if tile.endswith("筒"):
        return f"切分/筒/{tile}.jpeg"
    if tile.endswith("条"):
        rank = tile[:-1]
        if rank in _RANK_CHARS:
            return f"切分/索/{rank}索.jpeg"
    return None


@app.template_filter("tile_image_url")
def tile_image_url_filter(tile):
    rel = tile_scan_static_path(tile)
    if not rel:
        return ""
    return url_for("static", filename=rel)


def _tile_sort_key(tile):
    if not tile:
        return (99, 99, tile)
    tile = _normalize_tile_name(tile)
    # 先判杂牌、督牌，避免「千万」被当成数牌万子
    if tile in _MISC_ORDER:
        return (3, _MISC_ORDER.index(tile), tile)
    if tile in _DU_ORDER:
        return (4, _DU_ORDER.index(tile), tile)
    if tile.endswith("万"):
        r = tile[:-1]
        ri = _RANK_CHARS.index(r) if r in _RANK_CHARS else 99
        return (0, ri, tile)
    if tile.endswith("筒"):
        r = tile[:-1]
        ri = _RANK_CHARS.index(r) if r in _RANK_CHARS else 99
        return (1, ri, tile)
    if tile.endswith("条"):
        r = tile[:-1]
        ri = _RANK_CHARS.index(r) if r in _RANK_CHARS else 99
        return (2, ri, tile)
    return (5, 0, tile)


def sort_tiles(hand):
    """按类别（万筒条杂督）再按点数排序；千万归入杂牌。"""
    return sorted(hand, key=_tile_sort_key)


def _normalize_tile_name(tile):
    """统一牌名：索→条，幺督→妖督（同一张督牌）。"""
    if not tile:
        return tile
    if tile == "幺督":
        return "妖督"
    if tile.endswith("索"):
        return tile[:-1] + "条"
    return tile


def _hand_count(hand, tile):
    """手牌中某牌张数（牌名归一化后匹配，幺督=妖督）。"""
    if not tile:
        return 0
    want = _normalize_tile_name(tile)
    return sum(1 for t in hand if _normalize_tile_name(t) == want)


def _hand_remove_one(hand, tile):
    """从手牌移除一张（归一化匹配），成功返回 True。"""
    want = _normalize_tile_name(tile)
    for i, t in enumerate(hand):
        if _normalize_tile_name(t) == want:
            hand.pop(i)
            return True
    return False


def _play_post_error(message, *, status=400):
    """记录 POST 失败原因；询价/行牌错误用 flash 跳回牌桌，避免空白 400 页。"""
    logger.warning(
        "play POST %s: %s | action=%r claim_idx=%r form=%r",
        status,
        message,
        request.form.get("action"),
        (session.get("claim_state") or {}).get("idx"),
        dict(request.form),
    )
    flash(message, "error")
    return redirect(url_for("play"))


_MELD_META_TAGS = frozenset({"chi", "pong", "long", "long_open", "ke_open"})


def _normalize_meld(meld):
    if not meld:
        return meld
    m = list(meld)
    for i, x in enumerate(m):
        if x is not None and isinstance(x, str) and x not in _MELD_META_TAGS:
            m[i] = _normalize_tile_name(x)
    return tuple(m)


def _parse_suit_rank_tile(tile):
    """万/筒/条 数牌 → (花色后缀, 点数下标 0..8)。"""
    tile = _normalize_tile_name(tile)
    if not tile or len(tile) != 2:
        return None
    r, suf = tile[0], tile[1]
    if suf not in ("万", "筒", "条") or r not in _RANK_CHARS:
        return None
    return suf, _RANK_CHARS.index(r)


def _tile_at_rank(suf, ri):
    return _RANK_CHARS[ri] + suf


def _chi_fill_remaining(tmp, yao_rank0, gov):
    """顺子缺张：妖督仅补「一」，总督补任意缺张。"""
    rem = sum(tmp.values())
    if tmp.get(0, 0) > 0:
        use = min(tmp[0], yao_rank0)
        tmp[0] -= use
        rem -= use
        yao_rank0 -= use
    return rem == gov


def _chi_triplet_valid(discard_tile, h1, h2):
    """河牌 + 手牌两张（妖督代「一」、总督代任意）能否组成同花色顺子。"""
    pr = _parse_suit_rank_tile(discard_tile)
    if not pr:
        return False
    suf_d, rd = pr
    gov = 0
    yao_rank0 = 0
    fixed_ranks = []
    for t in (h1, h2):
        t = _normalize_tile_name(t)
        if t == "总督":
            gov += 1
        elif t == "妖督":
            yao_rank0 += 1
        else:
            pt = _parse_suit_rank_tile(t)
            if not pt or pt[0] != suf_d:
                return False
            fixed_ranks.append(pt[1])
    for start in range(0, 7):
        S = (start, start + 1, start + 2)
        if rd not in S:
            continue
        need = Counter({start: 1, start + 1: 1, start + 2: 1})
        need[rd] -= 1
        if need[rd] < 0:
            continue
        tmp = Counter(need)
        ok = True
        for r in fixed_ranks:
            if tmp[r] <= 0:
                ok = False
                break
            tmp[r] -= 1
        if not ok:
            continue
        if _chi_fill_remaining(tmp, yao_rank0, gov):
            return True
    return False


def _chi_find_start(discard_tile, h1, h2):
    """返回构成顺子的起始点数下标，无效则 None。"""
    pr = _parse_suit_rank_tile(discard_tile)
    if not pr:
        return None
    suf_d, rd = pr
    gov = 0
    yao_rank0 = 0
    fixed_ranks = []
    for t in (h1, h2):
        t = _normalize_tile_name(t)
        if t == "总督":
            gov += 1
        elif t == "妖督":
            yao_rank0 += 1
        else:
            pt = _parse_suit_rank_tile(t)
            if not pt or pt[0] != suf_d:
                return None
            fixed_ranks.append(pt[1])
    for start in range(0, 7):
        S = (start, start + 1, start + 2)
        if rd not in S:
            continue
        need = Counter({start: 1, start + 1: 1, start + 2: 1})
        need[rd] -= 1
        if need[rd] < 0:
            continue
        tmp = Counter(need)
        ok = True
        for r in fixed_ranks:
            if tmp[r] <= 0:
                ok = False
                break
            tmp[r] -= 1
        if not ok:
            continue
        if _chi_fill_remaining(tmp, yao_rank0, gov):
            return start
    return None


def _chi_options(hand, discard_tile):
    """可吃的手牌组合（含总督代缺张）。hand_pair 为排序后的两张手牌。"""
    pr = _parse_suit_rank_tile(discard_tile)
    if not pr:
        return []
    suf_d, rd = pr
    hc = Counter(hand)
    seen = set()
    out = []
    for start in range(0, 7):
        S = [start, start + 1, start + 2]
        if rd not in S:
            continue
        others = [x for x in S if x != rd]
        r1, r2 = others[0], others[1]
        t1, t2 = _tile_at_rank(suf_d, r1), _tile_at_rank(suf_d, r2)
        seq = tuple(
            sorted(
                (_tile_at_rank(suf_d, start), _tile_at_rank(suf_d, start + 1), _tile_at_rank(suf_d, start + 2)),
                key=_tile_sort_key,
            )
        )
        candidates = []
        if hc[t1] >= 1 and hc[t2] >= 1:
            candidates.append(tuple(sorted((t1, t2))))
        if hc[t1] >= 1 and hc.get("总督", 0) >= 1:
            candidates.append(tuple(sorted((t1, "总督"))))
        if hc[t2] >= 1 and hc.get("总督", 0) >= 1:
            candidates.append(tuple(sorted((t2, "总督"))))
        if hc.get("总督", 0) >= 2:
            candidates.append(tuple(sorted(("总督", "总督"))))
        if 0 in S and hc.get("妖督", 0) >= 1:
            if hc[t1] >= 1:
                candidates.append(tuple(sorted((t1, "妖督"))))
            if hc[t2] >= 1:
                candidates.append(tuple(sorted((t2, "妖督"))))
        for pair in candidates:
            if pair in seen:
                continue
            seen.add(pair)
            out.append({"hand_pair": pair, "sequence": seq})
    return out


def _hand_has_concealed_triplet_or_long(hand):
    """手牌里同点 ≥3 张（未碰/磕/拢亮出）则不能胡。"""
    hc = Counter(_normalize_tile_name(t) for t in hand)
    return any(n >= 3 for n in hc.values())


def _virtual_counter_for_win(hand, melds):
    """手牌 + 桌上「吃」的顺子明面；碰/磕/拢已亮出，不参与拆牌。"""
    c = Counter(_normalize_tile_name(t) for t in hand)
    for m in melds or []:
        m = _normalize_meld(m)
        if len(m) == 4 and m[3] == "chi":
            c[m[0]] += 1
            c[m[1]] += 1
            c[m[2]] += 1
        elif len(m) == 3 and m[2] not in ("pong", "long", "long_open", "ke_open"):
            # 兼容旧数据：无 "chi" 标记的三张顺子明牌
            c[m[0]] += 1
            c[m[1]] += 1
            c[m[2]] += 1
    return c


def _suit_only_sequences(arr9, jokers=0, yi_jokers=0):
    """万/筒/条 9 点计数；余牌须全部拆成顺子（禁止刻子）。督作百搭。"""
    def dfs(a, j, yi):
        i = 0
        while i < 9 and a[i] == 0:
            i += 1
        if i >= 9:
            return j == 0 and yi == 0
        if i > 6:
            return False
        na, nj, nyi = list(a), j, yi
        ok = True
        for k in range(3):
            ri = i + k
            if na[ri] > 0:
                na[ri] -= 1
            elif ri == 0 and nyi > 0:
                nyi -= 1
            elif nj > 0:
                nj -= 1
            else:
                ok = False
                break
        if ok and dfs(na, nj, nyi):
            return True
        return False

    return dfs(list(arr9), jokers, yi_jokers)


def _misc_only_sequences(misc_counts, yao_wild):
    """妖牌（乌龟、毛、千万）：仅允许 乌龟→毛→千万 这一顺；妖督可代缺张。"""
    m = [misc_counts.get(x, 0) for x in _MISC_ORDER]
    if sum(m) == 0:
        return yao_wild == 0
    y = yao_wild
    for i in range(3):
        if m[i] > 0:
            m[i] -= 1
        elif y > 0:
            y -= 1
        else:
            return False
    return sum(m) == 0 and y == 0


def _rest_melds_with_yao(wan, tong, tiao, misc, w_wan, w_tong, w_tiao, w_yao):
    """妖督分摊到万/筒/条作「一」；余量给妖牌顺子（乌龟-毛-千万）。"""
    for ym in range(w_yao + 1):
        rem = w_yao - ym
        for ya, yo, yi in _iter_nonneg_splits3(rem):
            if (
                _suit_only_sequences(wan, w_wan, yi_jokers=ya)
                and _suit_only_sequences(tong, w_tong, yi_jokers=yo)
                and _suit_only_sequences(tiao, w_tiao, yi_jokers=yi)
                and _misc_only_sequences(misc, ym)
            ):
                return True
    return False


def _iter_nonneg_splits4(total):
    """非负整数四元组，分量之和为 total。"""
    for a in range(total + 1):
        for b in range(total + 1 - a):
            for c in range(total + 1 - a - b):
                yield (a, b, c, total - a - b - c)


def _iter_nonneg_splits3(total):
    """非负整数三元组，分量之和为 total。"""
    for a in range(total + 1):
        for b in range(total + 1 - a):
            yield a, b, total - a - b


def _is_yi_rank_tile(tile):
    """是否一万 / 一筒 / 一条（点数「一」）。"""
    pr = _parse_suit_rank_tile(tile)
    return pr is not None and pr[1] == 0


def _split_counter_for_win(c):
    """拆成 万/筒/条 各 9 维 + 杂 + 花色督 + 总督（万能张数，自行分摊到四类）。"""
    wan = [0] * 9
    tong = [0] * 9
    tiao = [0] * 9
    misc = Counter()
    w_wan = w_tong = w_tiao = w_yao = u_gov = 0
    for t, n in list(c.items()):
        if not n:
            continue
        t = _normalize_tile_name(t)
        if t == "万督":
            w_wan += n
        elif t == "筒督":
            w_tong += n
        elif t == "条督":
            w_tiao += n
        elif t == "妖督":
            w_yao += n
        elif t == "总督":
            u_gov += n
        elif t in _MISC_ORDER:
            misc[t] += n
        elif t.endswith("索") and len(t) == 2:
            r = t[0]
            if r in _RANK_CHARS:
                tiao[_RANK_CHARS.index(r)] += n
        elif t.endswith("万") and len(t) == 2:
            r = t[0]
            if r in _RANK_CHARS:
                wan[_RANK_CHARS.index(r)] += n
        elif t.endswith("筒") and len(t) == 2:
            r = t[0]
            if r in _RANK_CHARS:
                tong[_RANK_CHARS.index(r)] += n
        elif t.endswith("条") and len(t) == 2:
            r = t[0]
            if r in _RANK_CHARS:
                tiao[_RANK_CHARS.index(r)] += n
        else:
            return None
    return wan, tong, tiao, misc, w_wan, w_tong, w_tiao, w_yao, u_gov


def _rest_all_melds_no_pair(c):
    if not c or sum(c.values()) == 0:
        return True
    if sum(c.values()) % 3 != 0:
        return False
    sp = _split_counter_for_win(c)
    if sp is None:
        return False
    wan, tong, tiao, misc, w_wan, w_tong, w_tiao, w_yao, u_gov = sp
    if u_gov == 0:
        return _rest_melds_with_yao(wan, tong, tiao, misc, w_wan, w_tong, w_tiao, w_yao)
    for uw, uto, uti, um in _iter_nonneg_splits4(u_gov):
        for ym in range(w_yao + 1):
            rem = w_yao - ym
            for ya, yo, yi in _iter_nonneg_splits3(rem):
                if (
                    _suit_only_sequences(wan, w_wan + uw, yi_jokers=ya)
                    and _suit_only_sequences(tong, w_tong + uto, yi_jokers=yo)
                    and _suit_only_sequences(tiao, w_tiao + uti, yi_jokers=yi)
                    and _misc_only_sequences(misc, ym + um)
                ):
                    return True
    return False


def _try_remove_pair(counter):
    """枚举将牌（含 数牌+对应督 作将），余牌全部拆成顺子（禁止刻子）。"""
    c = counter
    if sum(c.values()) < 2:
        return False
    keys = [k for k in c if c[k] > 0]
    for t in keys:
        if c[t] >= 2:
            c2 = Counter(c)
            c2[t] -= 2
            if c2[t] == 0:
                del c2[t]
            if _rest_all_melds_no_pair(c2):
                return True
    if c.get("万督", 0) >= 1:
        for r in _RANK_CHARS:
            tw = r + "万"
            if c.get(tw, 0) >= 1:
                c2 = Counter(c)
                c2[tw] -= 1
                if c2[tw] == 0:
                    del c2[tw]
                c2["万督"] -= 1
                if c2["万督"] == 0:
                    del c2["万督"]
                if _rest_all_melds_no_pair(c2):
                    return True
    if c.get("筒督", 0) >= 1:
        for r in _RANK_CHARS:
            tt = r + "筒"
            if c.get(tt, 0) >= 1:
                c2 = Counter(c)
                c2[tt] -= 1
                if c2[tt] == 0:
                    del c2[tt]
                c2["筒督"] -= 1
                if c2["筒督"] == 0:
                    del c2["筒督"]
                if _rest_all_melds_no_pair(c2):
                    return True
    if c.get("条督", 0) >= 1:
        for r in _RANK_CHARS:
            tx = r + "条"
            if c.get(tx, 0) >= 1:
                c2 = Counter(c)
                c2[tx] -= 1
                if c2[tx] == 0:
                    del c2[tx]
                c2["条督"] -= 1
                if c2["条督"] == 0:
                    del c2["条督"]
                if _rest_all_melds_no_pair(c2):
                    return True
    if c.get("妖督", 0) >= 1:
        for suf in ("万", "筒", "条"):
            t1 = "一" + suf
            if c.get(t1, 0) >= 1:
                c2 = Counter(c)
                c2[t1] -= 1
                if c2[t1] == 0:
                    del c2[t1]
                c2["妖督"] -= 1
                if c2["妖督"] == 0:
                    del c2["妖督"]
                if _rest_all_melds_no_pair(c2):
                    return True
        for mx in _MISC_ORDER:
            if c.get(mx, 0) >= 1:
                c2 = Counter(c)
                c2[mx] -= 1
                if c2[mx] == 0:
                    del c2[mx]
                c2["妖督"] -= 1
                if c2["妖督"] == 0:
                    del c2["妖督"]
                if _rest_all_melds_no_pair(c2):
                    return True
    if c.get("万督", 0) >= 2:
        c2 = Counter(c)
        c2["万督"] -= 2
        if c2["万督"] == 0:
            del c2["万督"]
        if _rest_all_melds_no_pair(c2):
            return True
    if c.get("筒督", 0) >= 2:
        c2 = Counter(c)
        c2["筒督"] -= 2
        if c2["筒督"] == 0:
            del c2["筒督"]
        if _rest_all_melds_no_pair(c2):
            return True
    if c.get("条督", 0) >= 2:
        c2 = Counter(c)
        c2["条督"] -= 2
        if c2["条督"] == 0:
            del c2["条督"]
        if _rest_all_melds_no_pair(c2):
            return True
    if c.get("妖督", 0) >= 2:
        c2 = Counter(c)
        c2["妖督"] -= 2
        if c2["妖督"] == 0:
            del c2["妖督"]
        if _rest_all_melds_no_pair(c2):
            return True
    if c.get("总督", 0) >= 2:
        c2 = Counter(c)
        c2["总督"] -= 2
        if c2["总督"] == 0:
            del c2["总督"]
        if _rest_all_melds_no_pair(c2):
            return True
    if c.get("总督", 0) >= 1:
        for t in list(c.keys()):
            if t == "总督":
                continue
            if c.get(t, 0) >= 1:
                c2 = Counter(c)
                c2[t] -= 1
                if c2[t] == 0:
                    del c2[t]
                c2["总督"] -= 1
                if c2["总督"] == 0:
                    del c2["总督"]
                if _rest_all_melds_no_pair(c2):
                    return True
    return False


def _evaluate_counter(c):
    """一对将 + 其余全部拆成顺子（万筒条；督牌百搭；禁止刻子）。"""
    if sum(c.values()) < 2:
        return False
    return _try_remove_pair(c)


def _win_check_failure_reason_from_counter(c):
    if sum(c.values()) < 2:
        return "不能胡牌：牌张过少"
    return "不能胡牌：须一对将 + 手牌其余全部为顺子（手牌同点≥3须先碰/磕/拢；桌上已亮碰/磕/拢不限）"


def _win_check_failure_reason(hand, melds=None):
    """返回不能胡牌时的说明（供界面提示）。"""
    return _win_check_failure_reason_from_counter(_virtual_counter_for_win(hand, melds))


def _rong_hand_counter(hand, melds, win_tile):
    """荣胡：手牌 + 明面 + 河牌再多一张（不能把手里一张「换成」河牌）。"""
    wt = _normalize_tile_name(win_tile)
    if not wt:
        return Counter()
    c = _virtual_counter_for_win(hand, melds)
    c[wt] += 1
    return c


def evaluate_rong_hand(hand, melds, win_tile):
    """荣胡：手牌 + 吃 + 河牌多一张，能拆成一对将 + 若干顺子即可。"""
    if _hand_has_concealed_triplet_or_long(hand):
        return {"can_win": False, "score": 0}
    c = _rong_hand_counter(hand, melds, win_tile)
    if _evaluate_counter(c):
        return {"can_win": True, "score": 100}
    return {"can_win": False, "score": 0}


def evaluate_hand(hand, melds=None):
    """
    胡牌形：手牌 + 桌上「吃」，能拆成「一对将 + 其余全部为顺子」。
    妖牌仅 乌龟→毛→千万 一顺，妖督可代缺张；手牌同点 ≥3 不能胡；桌上碰/磕/拢不参与拆牌。
    """
    if _hand_has_concealed_triplet_or_long(hand):
        return {"can_win": False, "score": 0}
    c = _virtual_counter_for_win(hand, melds)
    ok = _evaluate_counter(c)
    return {"can_win": ok, "score": 100 if ok else 0}


def can_zimo_hu(hand, melds=None):
    """自摸胡：手牌 + 明面能否拆成一对将 + 若干顺子。"""
    return bool(evaluate_hand(hand, melds)["can_win"])


def _simulate_pong_on_hand(hand, melds, tile):
    """模拟碰牌后的手牌与明面（不改动原列表）。"""
    tile = _normalize_tile_name(tile)
    h = list(hand)
    ms = list(melds or [])
    if _hand_count(h, tile) < 2:
        return None, None
    _hand_remove_one(h, tile)
    _hand_remove_one(h, tile)
    ms.append((tile, None, "pong"))
    return h, ms


def would_win_after_pong(hand, melds, tile):
    """碰该河牌后，是否立即满足自摸胡牌形。"""
    sim = _simulate_pong_on_hand(hand, melds, tile)
    if not sim[0]:
        return False
    return can_zimo_hu(sim[0], sim[1])


def _zimo_hu_failure_message(hand, melds=None):
    """不能胡牌时的说明。"""
    return _win_check_failure_reason(hand, melds)


def can_ronghu(hand, melds, tile):
    """荣胡：手牌 + 明面 + 河牌一张，能否拆成一对将 + 若干顺子。"""
    if not tile or _is_du_tile(tile):
        return False
    return bool(evaluate_rong_hand(hand, melds, tile)["can_win"])


def _meld_item_score(meld):
    m = _normalize_meld(meld)
    if len(m) == 4 and m[3] == "chi":
        return 0
    if len(m) == 3:
        tag = m[2]
        tile = m[0]
        mul = _special_meld_score_multiplier(tile)
        if tag == "long_open":
            return 140 * mul
        if tag == "long":
            return 70 * mul
        if tag == "ke_open":
            return 40 * mul
        if tag == "pong":
            return _pong_immediate_score(tile)
    if len(m) == 2:
        return 20 * _special_meld_score_multiplier(m[0])
    return 20


@app.template_filter("meld_display_score")
def meld_display_score_filter(meld):
    return _meld_item_score(meld)


@app.template_filter("pong_display_score")
def pong_display_score_filter(tile):
    return _pong_immediate_score(tile)


def _meld_score_sum(melds):
    return sum(_meld_item_score(m) for m in (melds or []))


def _is_concealed_ke_or_long(meld):
    if not meld:
        return False
    if len(meld) == 2:
        return True
    if len(meld) == 3 and meld[2] == "long":
        return True
    return False


def _find_concealed_ke_or_long_index(melds, tile):
    for i, m in enumerate(melds or []):
        if m[0] == tile and _is_concealed_ke_or_long(m):
            return i
    return None


def _can_kai(melds, tile):
    return _find_concealed_ke_or_long_index(melds, tile) is not None


def _meld_to_open(meld):
    if len(meld) == 2:
        return (meld[0], meld[1], "ke_open")
    if len(meld) == 3 and meld[2] == "long":
        return (meld[0], None, "long_open")
    return meld


def _kai_bonus_for_meld(meld):
    """开：暗磕/暗拢翻明时，立即再加一倍该组分（特殊牌基数已翻倍）。"""
    m = _normalize_meld(meld)
    if len(m) == 3 and m[2] == "long":
        return 70 * _ke_long_score_multiplier(m[0])
    if len(m) == 2:
        return 20 * _ke_long_score_multiplier(m[0])
    return 20


def _long_immediate_score(tile):
    """直接拢或补拢时立即入账的分。"""
    return 70 * _ke_long_score_multiplier(tile)


def _meld_can_upgrade_to_long(meld):
    """明面是否可补拢：磕（含带督）或碰，且尚未是拢。"""
    if not meld:
        return False
    if len(meld) == 3 and meld[2] in ("long", "long_open"):
        return False
    if len(meld) == 2:
        return True
    if len(meld) == 3 and meld[2] == "pong":
        return True
    return False


def _hand_tile_for_supplement_long(hand, tile):
    """补拢时从手牌支付的一张：同点牌，或数牌对应花色督。"""
    if hand.count(tile) >= 1:
        return tile
    du = _suit_du_for(tile)
    if du and hand.count(du) >= 1:
        return du
    return None


def _long_candidate_tiles(hand, player_melds):
    """可拢的牌面集合（手牌四张 或 明面磕/碰 + 手牌补一张/督）。"""
    out = set()
    for tile in set(hand):
        if hand.count(tile) >= 4:
            out.add(tile)
    for meld in player_melds or []:
        if not _meld_can_upgrade_to_long(meld):
            continue
        t = meld[0]
        if _hand_tile_for_supplement_long(hand, t):
            out.add(t)
    return sort_tiles(list(out))


def _record_win_and_redirect(sess, kind, base_score, meld_score, *, win_tile=None, from_player=None):
    """写入结算并跳转牌桌页展示结算弹层。"""
    human_ix = int(sess.get("human_player_index", 0))
    total = base_score + meld_score
    scores = list(sess.get("scores") or [0] * int(sess.get("num_players") or 4))
    while len(scores) <= human_ix:
        scores.append(0)
    scores[human_ix] += total
    sess["scores"] = scores
    sess["game_result"] = {
        "kind": kind,
        "player_num": human_ix + 1,
        "win_tile": win_tile,
        "from_player": from_player,
        "base_score": base_score,
        "meld_score": meld_score,
        "total_score": total,
    }
    sess.modified = True
    return redirect(url_for("play"))


def _execute_chi(sess, responder, D, discard, h1, h2):
    """吃：上家打出；河牌 + 手牌两张（可含总督）成顺。"""
    hands = sess["hands"]
    n = int(sess.get("num_players", 4))
    if (responder + n - 1) % n != D:
        return False
    if not _chi_triplet_valid(discard, h1, h2):
        return False
    hc = Counter(hands[responder])
    if hc[h1] < 1 or hc[h2] < 1:
        return False
    if h1 == h2 and hc[h1] < 2:
        return False
    start = _chi_find_start(discard, h1, h2)
    if start is None:
        return False
    pr = _parse_suit_rank_tile(discard)
    suf_d = pr[0]
    seq = tuple(
        sorted(
            (
                _tile_at_rank(suf_d, start),
                _tile_at_rank(suf_d, start + 1),
                _tile_at_rank(suf_d, start + 2),
            ),
            key=_tile_sort_key,
        )
    )
    melds = sess.get("melds") or [[] for _ in range(len(hands))]
    while len(melds) < len(hands):
        melds.append([])
    discarded = list(sess.get("discarded") or [])
    removed = False
    for i in range(len(discarded) - 1, -1, -1):
        if discarded[i][0] == D and discarded[i][1] == discard:
            discarded.pop(i)
            removed = True
            break
    if not removed:
        return False
    hands[responder].remove(h1)
    hands[responder].remove(h2)
    melds[responder].append((seq[0], seq[1], seq[2], "chi"))
    sess.pop("claim_state", None)
    sess.pop("pong_offer", None)
    sess["hands"] = hands
    sess["melds"] = melds
    sess["discarded"] = discarded
    sess["current_player"] = responder
    sess["has_drawn_wall_this_turn"] = True
    sess["after_claim_meld_turn"] = True
    sess["phase"] = "turn"
    sess.pop("last_wall_draw_tile", None)
    sess.modified = True
    return True


def _execute_rong_hu(sess, winner, D, tile):
    """荣胡：从河牌取走所胡那张，结束询价，轮到胡牌者行牌。"""
    discarded = list(sess.get("discarded") or [])
    removed = False
    for i in range(len(discarded) - 1, -1, -1):
        if discarded[i][0] == D and discarded[i][1] == tile:
            discarded.pop(i)
            removed = True
            break
    if not removed:
        return False
    sess.pop("claim_state", None)
    sess.pop("pong_offer", None)
    sess["discarded"] = discarded
    sess["current_player"] = winner
    sess["has_drawn_wall_this_turn"] = True
    sess["phase"] = "turn"
    sess.pop("last_wall_draw_tile", None)
    sess.modified = True
    return True


def _execute_kai(sess, responder, D, tile):
    """开：暗磕/暗拢与河牌同点 → 翻为明牌、该组分数×2（立即再加一倍分），然后摸一张出牌。"""
    hands = sess["hands"]
    melds = sess.get("melds") or [[] for _ in range(len(hands))]
    while len(melds) < len(hands):
        melds.append([])
    mi = _find_concealed_ke_or_long_index(melds[responder], tile)
    if mi is None:
        return False
    old_meld = melds[responder][mi]
    bonus = _kai_bonus_for_meld(old_meld)
    melds[responder][mi] = _meld_to_open(old_meld)
    scores = list(sess.get("scores") or [0] * len(hands))
    while len(scores) < len(hands):
        scores.append(0)
    scores[responder] += bonus
    draw_pile = list(sess.get("draw_pile") or [])
    if not draw_pile:
        draw_pile = new_shuffled_draw_pile()
    drawn = draw_pile.pop()
    hands[responder].append(drawn)
    sess.pop("claim_state", None)
    sess.pop("pong_offer", None)
    sess["hands"] = hands
    sess["melds"] = melds
    sess["scores"] = scores
    sess["draw_pile"] = draw_pile
    sess["current_player"] = responder
    sess["has_drawn_wall_this_turn"] = True
    sess["after_claim_meld_turn"] = True
    sess["phase"] = "turn"
    sess["last_wall_draw_tile"] = drawn
    sess.modified = True
    return True


def _execute_pong(sess, responder, D, tile, substitute=None):
    """碰：河牌一张 + 手牌两张同点（可含一张督），+10 分，轮到碰牌者出牌（不摸牌）。"""
    tile = _normalize_tile_name(tile)
    if substitute:
        substitute = _normalize_tile_name(substitute)
    hands = sess["hands"]
    melds = sess.get("melds") or [[] for _ in range(len(hands))]
    while len(melds) < len(hands):
        melds.append([])
    hand = hands[responder]
    discarded = list(sess.get("discarded") or [])
    if not _can_pong(hand, tile):
        return False
    removed = False
    for i in range(len(discarded) - 1, -1, -1):
        if discarded[i][0] == D and _normalize_tile_name(discarded[i][1]) == tile:
            discarded.pop(i)
            removed = True
            break
    if not removed:
        return False
    sub = None
    if _hand_count(hand, tile) >= 2 and not substitute:
        _hand_remove_one(hand, tile)
        _hand_remove_one(hand, tile)
    elif substitute and _hand_count(hand, tile) >= 1 and _hand_count(hand, substitute) >= 1:
        if not _valid_substitute_for_tile(tile, substitute):
            return False
        sub = _normalize_tile_name(substitute)
        _hand_remove_one(hand, tile)
        _hand_remove_one(hand, sub)
    elif _hand_count(hand, tile) >= 1:
        sub = _pick_pong_substitute(hand, tile)
        if not sub:
            return False
        _hand_remove_one(hand, tile)
        _hand_remove_one(hand, sub)
    else:
        return False
    melds[responder].append((tile, sub, "pong"))
    scores = list(sess.get("scores") or [0] * len(hands))
    while len(scores) < len(hands):
        scores.append(0)
    scores[responder] += _pong_immediate_score(tile)
    sess.pop("claim_state", None)
    sess.pop("pong_offer", None)
    sess["hands"] = hands
    sess["melds"] = melds
    sess["discarded"] = discarded
    sess["scores"] = scores
    sess["current_player"] = responder
    sess["has_drawn_wall_this_turn"] = True
    sess["after_claim_meld_turn"] = True
    sess["phase"] = "turn"
    sess.pop("last_wall_draw_tile", None)
    sess.modified = True
    return True


def _finish_all_claims_passed(sess):
    """三家都选择不碰（或不能碰）：轮到出牌者的下家摸牌。"""
    cs = sess.pop("claim_state", None)
    sess.pop("pong_offer", None)
    if not cs:
        return
    D = cs["discarder"]
    n = int(sess.get("num_players", 4))
    sess["current_player"] = (D + 1) % n
    sess["has_drawn_wall_this_turn"] = False
    sess["phase"] = "turn"
    sess.modified = True


def _finish_discard(sess, D, tile):
    """出牌进河；其余三家按 下家→对家→上家 依次决定是否碰（不立刻进入下一家行牌）。"""
    hands = sess["hands"]
    discarded = list(sess.get("discarded") or [])
    n = int(sess.get("num_players", 4))
    zhuang = int(sess.get("zhuang", 0))
    discarded.append((D, tile))
    if D == zhuang and not sess.get("dealer_opening_done", True):
        sess["dealer_opening_done"] = True
    sess["discarded"] = discarded
    sess.pop("last_wall_draw_tile", None)
    sess.pop("pong_offer", None)
    sess.pop("claim_state", None)
    if _is_du_tile(tile):
        sess["current_player"] = (D + 1) % n
        sess["has_drawn_wall_this_turn"] = False
        sess["phase"] = "turn"
        sess["hands"] = hands
        sess.modified = True
        return
    queue = [(D + 1) % n, (D + 2) % n, (D + 3) % n]
    sess["claim_state"] = {"discarder": D, "tile": tile, "queue": queue, "idx": 0}
    sess["phase"] = "claim"
    sess["hands"] = hands
    sess.modified = True


def _auto_resolve_claim_queue_until_human(sess):
    """人机：碰 > 吃（含总督顺），否则过。轮到真人则停。"""
    human = sess.get("human_player_index", 0)
    for _ in range(24):
        cs = sess.get("claim_state")
        if not cs:
            return
        q = cs["queue"]
        idx = cs["idx"]
        if idx >= len(q):
            _finish_all_claims_passed(sess)
            return
        resp = q[idx]
        tile, D = cs["tile"], cs["discarder"]
        if _is_du_tile(tile):
            _finish_all_claims_passed(sess)
            return
        hands = sess["hands"]
        n = int(sess.get("num_players", 4))
        chi_ok = (resp + n - 1) % n == D
        opts = _chi_options(hands[resp], tile) if chi_ok else []

        if resp == human:
            return

        resp_melds = (sess.get("melds") or [[] for _ in range(n)])[resp]
        if _can_kai(resp_melds, tile) and random.random() < 0.1:
            _execute_kai(sess, resp, D, tile)
            return

        if _can_pong(hands[resp], tile) and random.random() < 0.12:
            _execute_pong(sess, resp, D, tile)
            return
        if opts and random.random() < 0.11:
            a, b = opts[0]["hand_pair"]
            _execute_chi(sess, resp, D, tile, a, b)
            return
        if not _can_pong(hands[resp], tile) and not opts:
            cs["idx"] = idx + 1
            sess.modified = True
            continue
        cs["idx"] = idx + 1
        sess.modified = True


def _advance_ai_until_human_turn(sess):
    """非真人：先处理碰询价；再摸牌/出牌。轮到真人或等真人碰决定时停止。"""
    human = sess.get("human_player_index", 0)
    max_ops = 400
    for _ in range(max_ops):
        _auto_resolve_claim_queue_until_human(sess)
        cs = sess.get("claim_state")
        if cs:
            q = cs["queue"]
            idx = cs["idx"]
            if idx < len(q) and q[idx] == human:
                break
        cp = sess.get("current_player")
        if cp is None or cp == human:
            break
        hands = sess.get("hands")
        if not hands:
            break
        draw_pile = list(sess.get("draw_pile") or [])
        num_players = int(sess.get("num_players", 4))
        discarded = list(sess.get("discarded") or [])
        zhuang = int(sess.get("zhuang", 0))
        has_drawn = bool(sess.get("has_drawn_wall_this_turn", True))

        if not draw_pile:
            draw_pile = new_shuffled_draw_pile()

        if not has_drawn:
            if not draw_pile:
                break
            drawn = draw_pile.pop()
            hands[cp].append(drawn)
            sess["draw_pile"] = draw_pile
            sess["hands"] = hands
            sess["has_drawn_wall_this_turn"] = True
            sess["last_wall_draw_tile"] = drawn
            sess.modified = True
            continue

        ph = hands[cp]
        if not ph:
            break
        tile = random.choice(ph)
        ph.remove(tile)
        _finish_discard(sess, cp, tile)
        if not sess.get("draw_pile"):
            sess["draw_pile"] = new_shuffled_draw_pile()
            sess.modified = True


@app.route("/")
def index():
    return redirect(url_for("start"))


@app.route("/start")
def start():
    """初始化游戏（清空旧 session，避免脏数据）"""
    session.clear()
    num_players = 4
    hands = [[] for _ in range(num_players)]
    
    draw_pile = new_shuffled_draw_pile()

    # 庄家（先抓者，开局为玩家 0）17 张，其余三家各 16 张
    zhuang = 0
    for i in range(num_players):
        n = 17 if i == zhuang else 16
        for _ in range(n):
            hands[i].append(draw_pile.pop())

    session["hands"] = hands
    session["num_players"] = num_players
    session["current_player"] = 0
    session["draw_pile"] = draw_pile
    session["discarded"] = []
    session["melds"] = [[] for _ in range(num_players)]
    session["scores"] = [0] * num_players
    session.pop("pong_offer", None)
    session.pop("claim_state", None)
    session["phase"] = "turn"
    # 庄家：17 张起手，第一手出牌前不必从牌墙再摸
    session["zhuang"] = zhuang
    session["dealer_opening_done"] = False
    session["has_drawn_wall_this_turn"] = True
    session.pop("after_claim_meld_turn", None)
    session.pop("last_wall_draw_tile", None)
    session.pop("drawn_tile", None)
    session.pop("game_result", None)
    # 玩家 1（下标 0）为真人，其余为人机（GET /play 时自动摸牌 + 随机出牌）
    session["human_player_index"] = 0

    return redirect(url_for("play"))



@app.route("/play", methods=["GET", "POST"])
def play():
    hands = session.get("hands")
    num_players = int(session.get("num_players") or 4)
    current_player = session.get("current_player")
    discarded = session.get("discarded", [])
    draw_pile = session.get("draw_pile", [])
    melds = session.get("melds") or [[] for _ in range(num_players)]
    if len(melds) < num_players:
        melds = melds + [[] for _ in range(num_players - len(melds))]
    phase = session.get("phase", "turn")
    miscellaneous = {"乌龟", "毛", "千万"}

    if not draw_pile:
        draw_pile = new_shuffled_draw_pile()
        session["draw_pile"] = draw_pile

    if request.method == "POST":
        if hands is None or current_player is None:
            return redirect(url_for("start"))
        # 继续对局时清掉上一局胡牌弹层，避免挡住操作且冻结人机推进
        session.pop("game_result", None)
        human_ix = session.get("human_player_index", 0)
        player_hand = hands[human_ix]
        player_melds = melds[human_ix]
        zhuang = session.get("zhuang", 0)
        has_drawn = session.get("has_drawn_wall_this_turn", True)

        # === 碰牌：轮到真人询价时，碰 / 过 ===
        cs = session.get("claim_state")
        if cs:
            q, idx = cs["queue"], cs["idx"]
            if idx >= len(q):
                return redirect(url_for("play"))
            if q[idx] != human_ix:
                return redirect(url_for("play"))
            tile_c = _normalize_tile_name(cs["tile"])
            action = request.form.get("action") or ""
            if action == "hu":
                if can_zimo_hu(player_hand, player_melds):
                    if current_player != human_ix:
                        return _play_post_error("未轮到你操作")
                    result = evaluate_hand(player_hand, player_melds)
                    ms = _meld_score_sum(player_melds)
                    return _record_win_and_redirect(
                        session, "zimo", result["score"], ms
                    )
                action = "rong_hu"
            if _is_du_tile(tile_c):
                _finish_all_claims_passed(session)
                return redirect(url_for("play"))
            if action == "rong_hu":
                if not can_ronghu(player_hand, player_melds, tile_c):
                    c_try = _rong_hand_counter(player_hand, player_melds, tile_c)
                    reason = _win_check_failure_reason_from_counter(c_try).replace(
                        "不能胡牌", "不能荣胡", 1
                    )
                    return _play_post_error(reason)
                result = evaluate_rong_hand(player_hand, player_melds, tile_c)
                if not _execute_rong_hu(session, human_ix, cs["discarder"], tile_c):
                    return _play_post_error("荣胡失败（河牌状态异常）")
                ms = _meld_score_sum(player_melds)
                return _record_win_and_redirect(
                    session,
                    "rong",
                    result["score"],
                    ms,
                    win_tile=tile_c,
                    from_player=cs["discarder"] + 1,
                )
            if action == "chi_claim":
                npl = int(session.get("num_players", 4))
                Dc = cs["discarder"]
                if (human_ix + npl - 1) % npl != Dc:
                    return _play_post_error("只有上家打出的牌才能吃")
                a = request.form.get("chi_tile_a")
                b = request.form.get("chi_tile_b")
                if not a or not b:
                    return _play_post_error("吃牌须指定两张手牌")
                if _hand_count(player_hand, a) < 1 or _hand_count(player_hand, b) < 1:
                    return _play_post_error("吃牌须指定两张手牌")
                if a == b and _hand_count(player_hand, a) < 2:
                    return _play_post_error("吃牌须指定两张手牌")
                if not _execute_chi(session, human_ix, Dc, tile_c, a, b):
                    return _play_post_error("不能吃！")
                return redirect(url_for("play"))
            if action == "pong_claim":
                if not _can_pong(player_hand, tile_c):
                    return _play_post_error(
                        f"不符合碰牌条件：手牌须有两张「{tile_c}」，或一张「{tile_c}」"
                        f"加可用督牌（如杂牌配妖督/幺督）。"
                    )
                pong_sub = _normalize_tile_name(
                    request.form.get("pong_substitute") or ""
                )
                if not pong_sub:
                    pong_sub = None
                if not _execute_pong(
                    session, human_ix, cs["discarder"], tile_c, substitute=pong_sub
                ):
                    return _play_post_error(
                        f"碰牌失败：河牌「{tile_c}」可能已被处理，或手牌与督牌不匹配。"
                    )
                return redirect(url_for("play"))
            if action == "kai_claim":
                if not _can_kai(player_melds, tile_c):
                    return _play_post_error(
                        f"不能开：你没有与「{tile_c}」相同的暗磕或暗拢。"
                    )
                if not _execute_kai(session, human_ix, cs["discarder"], tile_c):
                    return _play_post_error("开牌失败")
                return redirect(url_for("play"))
            if action == "pong_pass":
                cs["idx"] = idx + 1
                session.modified = True
                if cs["idx"] >= len(q):
                    _finish_all_claims_passed(session)
                return redirect(url_for("play"))
            if request.form.get("tile") or request.form.get("meld_tile"):
                return _play_post_error(
                    "当前为询价阶段，请先点「胡（荣）」「碰」「吃」「开」或「过」，不能出牌/磕牌。"
                )
            return _play_post_error(
                f"请选「胡（荣）」「吃」「碰」「开」或「过」（收到 action={action!r}）"
            )

        # === 从牌墙摸牌（轮到本家且尚未摸时）===
        if request.form.get("action") == "draw_wall":
            if session.get("claim_state"):
                return redirect(url_for("play"))
            if phase != "turn":
                return redirect(url_for("play"))
            if current_player != human_ix:
                return "未轮到你摸牌", 400
            if has_drawn:
                return "本回合已摸过牌", 400
            if not draw_pile:
                return "牌墙已空", 400
            drawn = draw_pile.pop()
            player_hand.append(drawn)
            session["hands"] = hands
            session["draw_pile"] = draw_pile
            session["has_drawn_wall_this_turn"] = True
            session.pop("after_claim_meld_turn", None)
            session["last_wall_draw_tile"] = drawn
            return redirect(url_for("play"))

        # === 处理拢牌 ===
        if request.form.get("action") == "long":
            if session.get("claim_state"):
                return "请先完成碰牌选择", 400
            if current_player != human_ix:
                return "未轮到你操作", 400
            if not has_drawn:
                return "请先抓牌", 400
            tile_to_long = request.form.get("long_tile")
            scores = list(session.get("scores") or [0] * num_players)
            while len(scores) < num_players:
                scores.append(0)

            # 补拢：明面磕/碰 + 手牌再一张同点（或花色督）
            for i, meld in enumerate(player_melds):
                if not _meld_can_upgrade_to_long(meld) or meld[0] != tile_to_long:
                    continue
                pay_tile = _hand_tile_for_supplement_long(player_hand, tile_to_long)
                if not pay_tile:
                    du_sub = _suit_du_for(tile_to_long)
                    need_desc = f"「{tile_to_long}」"
                    if du_sub:
                        need_desc = f"「{tile_to_long}」或「{du_sub}」"
                    return f"你没有 {need_desc} 用于补拢", 400
                player_hand.remove(pay_tile)
                player_melds[i] = (tile_to_long, None, "long")
                scores[human_ix] += _long_immediate_score(tile_to_long)
                session["hands"] = hands
                session["melds"] = melds
                session["scores"] = scores
                session.modified = True
                if draw_pile:
                    player_hand.append(draw_pile.pop())
                    session["draw_pile"] = draw_pile
                return redirect(url_for("play"))

            # 直接拢
            if player_hand.count(tile_to_long) >= 4:
                for _ in range(4):
                    player_hand.remove(tile_to_long)
                player_melds.append((tile_to_long, None, "long"))
                scores[human_ix] += _long_immediate_score(tile_to_long)
                session["hands"] = hands
                session["melds"] = melds
                session["scores"] = scores
                session.modified = True
                if draw_pile:
                    player_hand.append(draw_pile.pop())
                    session["draw_pile"] = draw_pile
                return redirect(url_for("play"))

            return "无法拢牌，条件不满足", 400

        # === 处理磕牌 ===
        elif phase == "turn" and "meld_tile" in request.form:
            if session.get("claim_state"):
                return _play_post_error("请先完成碰牌选择")
            if current_player != human_ix:
                return _play_post_error("未轮到你操作")
            if not has_drawn:
                return _play_post_error("请先抓牌")
            meld_tile = _normalize_tile_name(request.form.get("meld_tile") or "")
            if not meld_tile:
                return _play_post_error("请选择要磕的牌")
            sub = _resolve_ke_substitute(
                player_hand, meld_tile, request.form.get("substitute") or ""
            )
            if sub is False:
                return _play_post_error(_ke_failure_message(player_hand, meld_tile))
            if sub:
                for _ in range(2):
                    if not _hand_remove_one(player_hand, meld_tile):
                        return _play_post_error("磕牌失败：手牌张数异常")
                if not _hand_remove_one(player_hand, sub):
                    return _play_post_error("你没有这张督牌！")
                player_melds.append((meld_tile, sub))
            else:
                if _hand_count(player_hand, meld_tile) < 3:
                    return _play_post_error(_ke_failure_message(player_hand, meld_tile))
                for _ in range(3):
                    if not _hand_remove_one(player_hand, meld_tile):
                        return _play_post_error("磕牌失败：手牌张数异常")
                player_melds.append((meld_tile, None))
            session["hands"] = hands
            session["melds"] = melds
            session.modified = True
            return redirect(url_for("play"))

        # === 出牌 ===
        elif phase == "turn" and "tile" in request.form:
            if session.get("claim_state"):
                return "请先完成碰牌选择", 400
            if current_player != human_ix:
                return "未轮到你出牌", 400
            if not has_drawn:
                return "请先抓牌后再出牌", 400
            tile = request.form.get("tile")
            if tile and tile in player_hand:
                player_hand.remove(tile)
                session.pop("after_claim_meld_turn", None)
                _finish_discard(session, human_ix, tile)
                session["hands"] = hands
                session["draw_pile"] = draw_pile
                session.pop("drawn_tile", None)
            else:
                return "出牌无效", 400

        # === 胡牌（自摸；碰/吃/开后无需再摸牌也可胡）===
        elif phase == "turn" and request.form.get("action") == "hu":
            if session.get("claim_state"):
                return _play_post_error("请先完成碰牌选择")
            if current_player != human_ix:
                return _play_post_error("未轮到你操作")
            hand_copy = player_hand.copy()
            if not can_zimo_hu(hand_copy, player_melds):
                return _play_post_error(_zimo_hu_failure_message(hand_copy, player_melds))
            result = evaluate_hand(hand_copy, player_melds)
            after_claim = bool(session.get("after_claim_meld_turn"))
            if not has_drawn and not after_claim:
                return _play_post_error("请先摸牌后再胡")
            ms = _meld_score_sum(player_melds)
            session.pop("after_claim_meld_turn", None)
            return _record_win_and_redirect(session, "zimo", result["score"], ms)

        return redirect(url_for("play"))

    # === GET 请求：渲染页面 ===
    if hands is None:
        return redirect(url_for("start"))
    if request.args.get("dismiss_win"):
        session.pop("game_result", None)
        session.modified = True
        return redirect(url_for("play"))
    if not draw_pile:
        draw_pile = new_shuffled_draw_pile()
        session["draw_pile"] = draw_pile
    if not session.get("game_result"):
        _advance_ai_until_human_turn(session)
    hands = session.get("hands")
    current_player = session.get("current_player")
    num_players = int(session.get("num_players") or 4)
    discarded = session.get("discarded", [])
    draw_pile = session.get("draw_pile", [])
    melds = session.get("melds", [[] for _ in range(num_players)])
    phase = session.get("phase", "turn")

    human_player_index = session.get("human_player_index", 0)
    claim_state = session.get("claim_state")
    if claim_state and claim_state.get("idx", 0) < len(claim_state.get("queue", [])):
        seat_highlight_index = claim_state["queue"][claim_state["idx"]]
    else:
        seat_highlight_index = current_player
    claim_waiting_human = bool(
        claim_state
        and claim_state["idx"] < len(claim_state["queue"])
        and claim_state["queue"][claim_state["idx"]] == human_player_index
    )
    claim_chi_eligible = False
    claim_chi_options = []
    chi_hand_choices = []
    claim_kai_eligible = False
    claim_pong_eligible = False
    claim_pong_substitutes = []
    if claim_waiting_human and claim_state:
        claim_tile = claim_state.get("tile")
        claim_pong_eligible = _can_pong(hands[human_player_index], claim_tile)
        h_hand = hands[human_player_index]
        if claim_pong_eligible and _hand_count(h_hand, claim_tile) < 2:
            claim_pong_substitutes = [
                du
                for du in _DU_ORDER
                if _hand_count(h_hand, du) >= 1
                and _valid_substitute_for_tile(claim_tile, du)
            ]
        claim_kai_eligible = _can_kai(
            melds[human_player_index], claim_tile
        )
        dc = int(claim_state["discarder"])
        claim_chi_eligible = human_player_index == (dc + 1) % num_players
        if claim_chi_eligible:
            claim_chi_options = _chi_options(
                hands[human_player_index], claim_state.get("tile")
            )

    hand = sort_tiles(hands[human_player_index])
    if claim_chi_eligible:
        chi_hand_choices = sorted(set(hand), key=_tile_sort_key)
    has_drawn_wall = session.get("has_drawn_wall_this_turn", True)
    last_wall_draw = session.get("last_wall_draw_tile")
    zhuang = session.get("zhuang", 0)
    dealer_opening_done = session.get("dealer_opening_done", True)
    player_melds = melds[human_player_index]
    can_zimo = can_zimo_hu(hand, player_melds)
    can_rong = False
    claim_pong_then_hu = False
    if claim_waiting_human and claim_state:
        claim_tile = claim_state.get("tile")
        can_rong = bool(claim_tile and can_ronghu(hand, player_melds, claim_tile))
        if claim_pong_eligible and claim_tile:
            claim_pong_then_hu = would_win_after_pong(hand, player_melds, claim_tile)
    human_turn_controls = claim_waiting_human or (
        phase == "turn"
        and current_player == human_player_index
        and not claim_state
    )
    needs_wall_draw = (
        human_turn_controls
        and not has_drawn_wall
        and not claim_state
        and current_player == human_player_index
        and not can_zimo
    )
    player_du_pai = [tile for tile in hands[human_player_index] if "督" in tile]

    long_candidates = _long_candidate_tiles(hand, player_melds)

    # 四人桌：相对「当前行牌/碰询价」座位高亮
    cp = seat_highlight_index
    seat_left = (cp + num_players - 1) % num_players
    seat_top = (cp + 2) % num_players
    seat_right = (cp + 1) % num_players
    seat_counts = [len(hands[i]) for i in range(num_players)]
    seat_meld_counts = [len(melds[i]) for i in range(num_players)]
    draw_pile_count = len(draw_pile)
    player_scores = list(session.get("scores") or [0] * num_players)
    while len(player_scores) < num_players:
        player_scores.append(0)

    return render_template(
        "play.html",
        hand=hand,
        current_player=current_player + 1,
        current_player_index=cp,
        human_player_index=human_player_index,
        clock_player_index=current_player,
        discarded=discarded,
        needs_wall_draw=needs_wall_draw,
        last_wall_draw_tile=last_wall_draw,
        zhuang_player=zhuang + 1,
        is_dealer_opening=(
            human_player_index == zhuang
            and not dealer_opening_done
            and not claim_state
            and current_player == human_player_index
        ),
        phase=phase,
        claim_state=claim_state,
        claim_waiting_human=claim_waiting_human,
        claim_chi_eligible=claim_chi_eligible,
        claim_kai_eligible=claim_kai_eligible,
        claim_pong_eligible=claim_pong_eligible,
        claim_pong_substitutes=claim_pong_substitutes,
        claim_chi_options=claim_chi_options,
        chi_hand_choices=chi_hand_choices,
        player_scores=player_scores,
        melds=player_melds,
        player_du_pai=player_du_pai,
        miscellaneous=list(miscellaneous),
        long_candidates=long_candidates,
        num_players=num_players,
        seat_left=seat_left,
        seat_top=seat_top,
        seat_right=seat_right,
        seat_counts=seat_counts,
        seat_meld_counts=seat_meld_counts,
        draw_pile_count=draw_pile_count,
        game_result=session.get("game_result"),
        human_turn_controls=human_turn_controls,
        can_zimo_hu=can_zimo,
        can_rong_hu=can_rong,
        claim_pong_then_hu=claim_pong_then_hu,
        after_claim_meld_turn=bool(session.get("after_claim_meld_turn")),
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # macOS 的「隔空播放接收器」常占用 5000 并返回 HTTP 403，勿用 5000
    port = int(os.environ.get("PORT", "5001"))
    print(f"虎牌: http://127.0.0.1:{port}/start")
    app.run(debug=True, host="127.0.0.1", port=port)
