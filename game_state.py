"""联机/单机共用的牌局状态容器。"""


class GameState(dict):
    """可变牌局 dict；兼容 helpers 里的 sess.get / sess['k'] / sess.modified。"""

    def __init__(self, initial=None):
        super().__init__(initial or {})
        self.modified = False

    def pop(self, key, *args):
        self.modified = True
        return super().pop(key, *args)

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        self.modified = True


def melds_lists_to_tuples(melds):
    """JSON 读回后把明面 list 还原为 tuple。"""
    out = []
    for player in melds or []:
        row = []
        for m in player or []:
            row.append(tuple(m) if isinstance(m, list) else m)
        out.append(row)
    return out


def state_from_storage(raw):
    """SQLite JSON → GameState。"""
    gs = GameState(dict(raw or {}))
    if "melds" in gs:
        gs["melds"] = melds_lists_to_tuples(gs["melds"])
    gs.modified = False
    return gs


def _json_safe(value):
    if isinstance(value, tuple):
        return [_json_safe(x) for x in value]
    if isinstance(value, list):
        return [_json_safe(x) for x in value]
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    return value


def state_to_storage(state):
    """GameState / session mapping → 可 JSON 序列化的 dict。"""
    raw = dict(state) if isinstance(state, GameState) else dict(state)
    return _json_safe(raw)
