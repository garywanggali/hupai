from collections import Counter

from flask import Flask, session, render_template, request, redirect, url_for
import random

app = Flask(__name__)
app.secret_key = "your_secret_key_here"  # 必须设置，用于 session

# 普通牌：4 套；督牌：每种整局只出现 1 张
BASE_TILES = [
    "一万", "二万", "三万", "四万", "五万", "六万", "七万", "八万", "九万",
    "一筒", "二筒", "三筒", "四筒", "五筒", "六筒", "七筒", "八筒", "九筒",
    "一条", "二条", "三条", "四条", "五条", "六条", "七条", "八条", "九条",
    "乌龟", "毛", "千万",
]
DU_TILES_UNIQUE = ["万督", "筒督", "条督", "妖督", "总督"]

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


def new_shuffled_draw_pile():
    pile = BASE_TILES * 4 + list(DU_TILES_UNIQUE)
    random.shuffle(pile)
    return pile

# 手牌排序：万 → 筒 → 条 → 杂（乌龟、毛、千万）→ 督；类内按点数一二…九
_RANK_CHARS = "一二三四五六七八九"
_MISC_ORDER = ("乌龟", "毛", "千万")
_DU_ORDER = ("万督", "筒督", "条督", "妖督", "总督")


def _tile_sort_key(tile):
    if not tile:
        return (99, 99, tile)
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


def _parse_suit_rank_tile(tile):
    """万/筒/条 数牌 → (花色后缀, 点数下标 0..8)。"""
    if not tile or len(tile) != 2:
        return None
    r, suf = tile[0], tile[1]
    if suf not in ("万", "筒", "条") or r not in _RANK_CHARS:
        return None
    return suf, _RANK_CHARS.index(r)


def _tile_at_rank(suf, ri):
    return _RANK_CHARS[ri] + suf


def _chi_triplet_valid(discard_tile, h1, h2):
    """河牌 + 手牌两张（可为总督）能否组成同花色顺子。"""
    pr = _parse_suit_rank_tile(discard_tile)
    if not pr:
        return False
    suf_d, rd = pr
    gov = 0
    fixed_ranks = []
    for t in (h1, h2):
        if t == "总督":
            gov += 1
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
        if sum(tmp.values()) == gov:
            return True
    return False


def _chi_find_start(discard_tile, h1, h2):
    """返回构成顺子的起始点数下标，无效则 None。"""
    pr = _parse_suit_rank_tile(discard_tile)
    if not pr:
        return None
    suf_d, rd = pr
    gov = 0
    fixed_ranks = []
    for t in (h1, h2):
        if t == "总督":
            gov += 1
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
        if sum(tmp.values()) == gov:
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
        for pair in candidates:
            if pair in seen:
                continue
            seen.add(pair)
            out.append({"hand_pair": pair, "sequence": seq})
    return out


def _virtual_counter_for_win(hand, melds):
    """手牌 + 桌上明面（碰/磕/拢）合并为 multiset，用于胡牌形判断。"""
    c = Counter(hand)
    for m in melds or []:
        if len(m) == 4 and m[3] == "chi":
            c[m[0]] += 1
            c[m[1]] += 1
            c[m[2]] += 1
        elif len(m) == 3 and m[2] == "pong":
            c[m[0]] += 3
        elif len(m) == 3 and m[2] == "long":
            c[m[0]] += 4
        elif len(m) == 2:
            t, sub = m[0], m[1]
            if sub:
                c[t] += 2
                c[sub] += 1
            else:
                c[t] += 3
    return c


def _suit_only_melds(arr9, jokers):
    """万/筒/条 9 点计数 + 该花色督牌，能否全部组成顺子或刻子（每组 3 张）。"""
    def dfs(a, j):
        i = 0
        while i < 9 and a[i] == 0:
            i += 1
        if i >= 9:
            return j == 0
        if i <= 6:
            na, nj = list(a), j
            ok = True
            for k in range(3):
                if na[i + k] > 0:
                    na[i + k] -= 1
                elif nj > 0:
                    nj -= 1
                else:
                    ok = False
                    break
            if ok and dfs(na, nj):
                return True
        na, nj = list(a), j
        take = min(na[i], 3)
        na[i] -= take
        need = 3 - take
        if need <= nj and dfs(na, nj - need):
            return True
        return False

    return dfs(list(arr9), jokers)


def _iter_nonneg_splits4(total):
    """非负整数四元组，分量之和为 total。"""
    for a in range(total + 1):
        for b in range(total + 1 - a):
            for c in range(total + 1 - a - b):
                yield (a, b, c, total - a - b - c)


def _misc_only_melds(misc_counts, yao_wild):
    """杂牌（乌龟、毛、千万）仅允许刻子；妖督作杂牌百搭。余下妖督须能三三组成刻。"""
    misc = [misc_counts.get(x, 0) for x in _MISC_ORDER]

    def dfs(idx, w, m):
        while idx < 3 and m[idx] == 0:
            idx += 1
        if idx >= 3:
            return w % 3 == 0
        if m[idx] >= 3:
            nm = m[:]
            nm[idx] -= 3
            if dfs(idx, w, nm):
                return True
        if m[idx] > 0:
            need = 3 - m[idx]
            if need <= w:
                nm = m[:]
                nm[idx] = 0
                if dfs(idx, w - need, nm):
                    return True
        return False

    return dfs(0, yao_wild, misc)


def _split_counter_for_win(c):
    """拆成 万/筒/条 各 9 维 + 杂 + 花色督 + 总督（万能张数，自行分摊到四类）。"""
    wan = [0] * 9
    tong = [0] * 9
    tiao = [0] * 9
    misc = Counter()
    w_wan = w_tong = w_tiao = w_yao = u_gov = 0
    for t, n in c.items():
        if not n:
            continue
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
        return (
            _suit_only_melds(wan, w_wan)
            and _suit_only_melds(tong, w_tong)
            and _suit_only_melds(tiao, w_tiao)
            and _misc_only_melds(misc, w_yao)
        )
    for uw, uto, uti, um in _iter_nonneg_splits4(u_gov):
        if (
            _suit_only_melds(wan, w_wan + uw)
            and _suit_only_melds(tong, w_tong + uto)
            and _suit_only_melds(tiao, w_tiao + uti)
            and _misc_only_melds(misc, w_yao + um)
        ):
            return True
    return False


def _try_remove_pair(counter):
    """枚举将牌（含 数牌+对应督 作将），余牌全为刻/顺。"""
    c = counter
    total = sum(c.values())
    if total % 3 != 2:
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


def evaluate_hand(hand, melds=None):
    """
    胡牌：手牌 + 桌上明面 合并后，须为「若干组 3 张的顺/刻（万筒条）或杂刻」+「一对将」。
    万督/筒督/条督 仅作对应花色百搭；妖督 仅作杂牌百搭；总督可作任意牌参与面子与将。
    """
    c = _virtual_counter_for_win(hand, melds)
    if sum(c.values()) < 2:
        return {"can_win": False, "score": 0}
    if sum(c.values()) % 3 != 2:
        return {"can_win": False, "score": 0}
    ok = _try_remove_pair(c)
    return {"can_win": ok, "score": 100 if ok else 0}


def can_ronghu(hand, melds, tile):
    """他人打出的牌是否与本家手牌 + 明面组成胡牌形（荣胡 / 点炮）。"""
    if not tile:
        return False
    return bool(evaluate_hand(list(hand) + [tile], melds)["can_win"])


def _meld_score_sum(melds):
    s = 0
    for meld in melds or []:
        if len(meld) == 4 and meld[3] == "chi":
            s += 0
        elif len(meld) == 3 and meld[2] == "long":
            s += 70
        elif len(meld) == 3 and meld[2] == "pong":
            s += 10
        else:
            s += 20
    return s


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


def _execute_pong(sess, responder, D, tile):
    """碰：河牌一张 + 手牌两张同牌，+10 分，清空询价，轮到碰牌者出牌（不摸牌）。"""
    hands = sess["hands"]
    melds = sess.get("melds") or [[] for _ in range(len(hands))]
    while len(melds) < len(hands):
        melds.append([])
    discarded = list(sess.get("discarded") or [])
    if hands[responder].count(tile) < 2:
        return False
    removed = False
    for i in range(len(discarded) - 1, -1, -1):
        if discarded[i][0] == D and discarded[i][1] == tile:
            discarded.pop(i)
            removed = True
            break
    if not removed:
        return False
    hands[responder].remove(tile)
    hands[responder].remove(tile)
    melds[responder].append((tile, None, "pong"))
    scores = list(sess.get("scores") or [0] * len(hands))
    while len(scores) < len(hands):
        scores.append(0)
    scores[responder] += 10
    sess.pop("claim_state", None)
    sess.pop("pong_offer", None)
    sess["hands"] = hands
    sess["melds"] = melds
    sess["discarded"] = discarded
    sess["scores"] = scores
    sess["current_player"] = responder
    sess["has_drawn_wall_this_turn"] = True
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

        if hands[resp].count(tile) >= 2 and random.random() < 0.12:
            _execute_pong(sess, resp, D, tile)
            return
        if opts and random.random() < 0.11:
            a, b = opts[0]["hand_pair"]
            _execute_chi(sess, resp, D, tile, a, b)
            return
        if hands[resp].count(tile) < 2 and not opts:
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


@app.route("/start")
def start():
    """初始化游戏"""
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
    session.pop("last_wall_draw_tile", None)
    session.pop("drawn_tile", None)
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
            tile_c = cs["tile"]
            if _is_du_tile(tile_c):
                _finish_all_claims_passed(session)
                return redirect(url_for("play"))
            if request.form.get("action") == "rong_hu":
                hw = player_hand + [tile_c]
                result = evaluate_hand(hw, player_melds)
                if not result["can_win"]:
                    return "不能荣胡！", 400
                if not _execute_rong_hu(session, human_ix, cs["discarder"], tile_c):
                    return "荣胡失败（河牌状态异常）", 400
                ms = _meld_score_sum(player_melds)
                tot = result["score"] + ms
                return (
                    f"荣胡成功！点和「{tile_c}」。基础分：{result['score']}，"
                    f"磕/拢/碰加分：{ms}，总分：{tot}"
                )
            if request.form.get("action") == "chi_claim":
                npl = int(session.get("num_players", 4))
                Dc = cs["discarder"]
                if (human_ix + npl - 1) % npl != Dc:
                    return "只有上家打出的牌才能吃", 400
                a = request.form.get("chi_tile_a")
                b = request.form.get("chi_tile_b")
                if not a or not b:
                    return "吃牌须指定两张手牌", 400
                pc = Counter(player_hand)
                if pc[a] < 1 or pc[b] < 1:
                    return "吃牌须指定两张手牌", 400
                if a == b and pc[a] < 2:
                    return "吃牌须指定两张手牌", 400
                if not _execute_chi(session, human_ix, Dc, tile_c, a, b):
                    return "不能吃！", 400
                return redirect(url_for("play"))
            if request.form.get("action") == "pong_claim":
                if player_hand.count(tile_c) < 2:
                    return (
                        f"不符合碰牌条件：手牌里至少要有两张「{tile_c}」才能碰 "
                        f"（河牌也是「{tile_c}」）。请点「过」或换牌后再试。",
                        400,
                    )
                _execute_pong(session, human_ix, cs["discarder"], tile_c)
                return redirect(url_for("play"))
            if request.form.get("action") == "pong_pass":
                cs["idx"] = idx + 1
                session.modified = True
                if cs["idx"] >= len(q):
                    _finish_all_claims_passed(session)
                return redirect(url_for("play"))
            return "请选「胡」「吃」「碰」或「过」", 400

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

            # 补拢已磕的
            for i, meld in enumerate(player_melds):
                if len(meld) >= 2 and meld[0] == tile_to_long and meld[1] is None and "long" not in meld:
                    du_sub = _suit_du_for(tile_to_long)
                    if player_hand.count(tile_to_long) >= 1:
                        player_hand.remove(tile_to_long)
                        player_melds[i] = (tile_to_long, None, "long")
                        scores[human_ix] += 70
                        session["hands"] = hands
                        session["melds"] = melds
                        session["scores"] = scores
                        if draw_pile:
                            player_hand.append(draw_pile.pop())
                            session["draw_pile"] = draw_pile
                        return redirect(url_for("play"))
                    elif du_sub and player_hand.count(du_sub) >= 1:
                        player_hand.remove(du_sub)
                        player_melds[i] = (tile_to_long, None, "long")
                        scores[human_ix] += 70
                        session["hands"] = hands
                        session["melds"] = melds
                        session["scores"] = scores
                        # 摸一张牌以保证胡牌结构
                        if draw_pile:
                            player_hand.append(draw_pile.pop())
                            session["draw_pile"] = draw_pile
                        return redirect(url_for("play"))
                    else:
                        need_desc = f"「{tile_to_long}」"
                        if du_sub:
                            need_desc = f"「{tile_to_long}」或「{du_sub}」"
                        return f"你没有 {need_desc} 用于补拢", 400

            # 直接拢
            if player_hand.count(tile_to_long) >= 4:
                for _ in range(4):
                    player_hand.remove(tile_to_long)
                player_melds.append((tile_to_long, None, "long"))
                scores[human_ix] += 70
                session["hands"] = hands
                session["melds"] = melds
                session["scores"] = scores
                if draw_pile:
                    player_hand.append(draw_pile.pop())
                    session["draw_pile"] = draw_pile
                return redirect(url_for("play"))

            return "无法拢牌，条件不满足", 400

        # === 处理磕牌 ===
        elif phase == "turn" and "meld_tile" in request.form:
            if session.get("claim_state"):
                return "请先完成碰牌选择", 400
            if current_player != human_ix:
                return "未轮到你操作", 400
            if not has_drawn:
                return "请先抓牌", 400
            meld_tile = request.form.get("meld_tile")
            substitute = request.form.get("substitute")

            can_meld = False
            if substitute:
                if substitute not in player_hand:
                    return "你没有这张督牌！", 400
                if substitute == "妖督" and meld_tile not in miscellaneous:
                    return "妖督只能用来磕杂牌！", 400
                if player_hand.count(meld_tile) >= 2:
                    can_meld = True
                else:
                    return "你手牌数量不足2张，无法用督牌磕牌", 400
            else:
                if player_hand.count(meld_tile) >= 3:
                    can_meld = True
                else:
                    return "你手牌中没有3张相同的牌，无法磕牌", 400

            if can_meld:
                if substitute:
                    for _ in range(2):
                        player_hand.remove(meld_tile)
                    player_hand.remove(substitute)
                    player_melds.append((meld_tile, substitute))
                else:
                    for _ in range(3):
                        player_hand.remove(meld_tile)
                    player_melds.append((meld_tile, None))
                session["hands"] = hands
                session["melds"] = melds
            else:
                return "无法磕牌，条件不满足", 400

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
                _finish_discard(session, human_ix, tile)
                session["hands"] = hands
                session["draw_pile"] = draw_pile
                session.pop("drawn_tile", None)
            else:
                return "出牌无效", 400

        # === 胡牌 ===
        elif phase == "turn" and request.form.get("action") == "hu":
            if session.get("claim_state"):
                return "请先完成碰牌选择", 400
            if current_player != human_ix:
                return "未轮到你操作", 400
            if not has_drawn:
                return "请先抓牌", 400
            hand_copy = player_hand.copy()

            result = evaluate_hand(hand_copy, player_melds)
            if result["can_win"]:
                base_score = result["score"]

                meld_score = 0
                for meld in player_melds:
                    if len(meld) == 3 and meld[2] == "long":
                        meld_score += 70
                    elif len(meld) == 3 and meld[2] == "pong":
                        meld_score += 10
                    else:
                        meld_score += 20

                total_score = base_score + meld_score
                return f"玩家 {human_ix + 1} 胡牌成功！基础分：{base_score}，磕/拢牌加分：{meld_score}，总分：{total_score}"
            else:
                return "不能胡牌！", 400

        return redirect(url_for("play"))

    # === GET 请求：渲染页面 ===
    if hands is None:
        return redirect(url_for("start"))
    if not draw_pile:
        draw_pile = new_shuffled_draw_pile()
        session["draw_pile"] = draw_pile
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
    if claim_waiting_human and claim_state:
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
    needs_wall_draw = (
        not has_drawn_wall
        and not claim_state
        and current_player == human_player_index
    )
    player_melds = melds[human_player_index]
    player_du_pai = [tile for tile in hands[human_player_index] if "督" in tile]

    # 计算可拢的牌
    long_candidates = []
    for tile in set(hand):
        if hand.count(tile) >= 4:
            long_candidates.append(tile)
        for meld in player_melds:
            if len(meld) >= 2 and meld[0] == tile and meld[1] is None and hand.count(tile) >= 1:
                long_candidates.append(tile)
    long_candidates = list(set(long_candidates))

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
    )


if __name__ == "__main__":
    app.run(debug=True)
