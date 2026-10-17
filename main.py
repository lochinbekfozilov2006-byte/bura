"""Bura (Burkozel) - 2 kishilik karta o'yini, veb-sayt.

Rejimlar:
  * Botga qarshi: server bot rolini o'ynaydi (qoidalar bitta joyda, bir xil).
  * Onlayn: biri "baza" ochadi, tizim 4 xonali kod beradi (masalan 0224),
    ikkinchisi shu kodni kiritadi va o'yin boshlanadi.

Muhit o'zgaruvchilari:
  ADMIN_CODE  - profil kodi; kiritgan o'yinchiga eng kuchli kartalar tushadi
                (sayt ochiq bo'lgach albatta maxfiy so'zga almashtiring)
  HAND_SIZE   - qo'ldagi kartalar soni (default 4)
  WIN_POINTS  - shuncha ochko to'plagan darrov yutadi (default 61)
  PORT        - hosting o'zi beradi
"""
import asyncio
import itertools
import json
import logging
import os
import random

from aiohttp import web, WSMsgType

ADMIN_CODE = os.getenv("ADMIN_CODE", "admin")
HAND_SIZE = int(os.getenv("HAND_SIZE", "4"))
WIN_POINTS = int(os.getenv("WIN_POINTS", "61"))
PORT = int(os.getenv("PORT", "8080"))
BASE = os.path.dirname(os.path.abspath(__file__))

# Karta id: 0..35. mast = id // 9, kuch = id % 9  (6 7 8 9 J Q K 10 A)
POINTS = [0, 0, 0, 0, 2, 3, 4, 10, 11]


def suit(c): return c // 9
def strength(c): return c % 9
def pts(c): return POINTS[c % 9]


def beats(d, a, trump):
    if suit(d) == suit(a):
        return strength(d) > strength(a)
    return suit(d) == trump and suit(a) != trump


def can_beat(att, dfn, trump):
    """Himoyachi har bir hujum kartasini alohida karta bilan yopa oladimi."""
    for perm in itertools.permutations(dfn):
        if all(beats(d, a, trump) for d, a in zip(perm, att)):
            return True
    return False


class Player:
    def __init__(self, ws):
        self.ws = ws
        self.name = "Mehmon"
        self.code = ""
        self.game = None
        self.seat = None
        self.authed = False

    @property
    def is_admin(self):
        return bool(ADMIN_CODE) and self.code == ADMIN_CODE


class Bot:
    is_admin = False
    code = ""

    def __init__(self, avoid=""):
        names = [n for n in ["Rustam", "Dilshod", "Jasur", "Sardor", "Aziz", "Bobur"] if n != avoid]
        self.name = random.choice(names) + " (bot)"
        self.game = None
        self.seat = None


class Game:
    def __init__(self, a, b):
        self.players = [a, b]
        self.dead = False
        self.busy = False
        self.new_round(random.randint(0, 1))

    def new_round(self, first):
        deck = list(range(36))
        random.shuffle(deck)
        self.deck = deck
        self.trump_card = deck[-1]          # pastki karta kozir, oxirida olinadi
        self.trump = suit(self.trump_card)
        self.hands = [[], []]
        self.scores = [0, 0]
        self.table = []
        self.last = None
        self.result = None
        self.again = [False, False]
        a, b = self.players[0].is_admin, self.players[1].is_admin
        self.boost = [a and not b, b and not a]   # ikkalasi admin bo'lsa oddiy o'yin
        self.turn = first
        self.phase = "attack"
        self.refill(first)
        self.refill(1 - first)
        for s in (0, 1):
            h = self.hands[s]
            if len(h) == HAND_SIZE and all(suit(c) == self.trump for c in h):
                self.finish(s, "Bura: qo'lda faqat kozirlar")
                break

    def refill(self, seat):
        h = self.hands[seat]
        while len(h) < HAND_SIZE and self.deck:
            if self.boost[seat] and len(self.deck) > 1:
                cands = self.deck[:-1]
                if len(h) == HAND_SIZE - 1 and all(suit(c) == self.trump for c in h):
                    cands = [c for c in cands if suit(c) != self.trump] or cands
                best = max(cands, key=lambda c: (suit(c) == self.trump) * 100 + strength(c))
                self.deck.remove(best)
                h.append(best)
            else:
                h.append(self.deck.pop(0))

    def actor(self):
        return self.turn if self.phase == "attack" else 1 - self.turn

    def finish(self, winner, why):
        self.result = {"winner": winner, "why": why}

    def play(self, seat, cards):
        if self.result:
            return "O'yin tugagan"
        if seat != self.actor():
            return "Navbat sizda emas"
        if (not cards or len(set(cards)) != len(cards)
                or any(c not in self.hands[seat] for c in cards)):
            return "Noto'g'ri kartalar"
        if self.phase == "attack":
            if len({suit(c) for c in cards}) != 1:
                return "Bir xil mastdagi kartalarni tanlang"
            if len(cards) > len(self.hands[1 - seat]):
                return "Raqibda bunchalik karta yo'q"
            for c in cards:
                self.hands[seat].remove(c)
            self.table = list(cards)
            self.phase = "defend"
            return None
        if len(cards) != len(self.table):
            return f"Aynan {len(self.table)} ta karta tashlang"
        att, atk = self.table, self.turn
        for c in cards:
            self.hands[seat].remove(c)
        winner = seat if can_beat(att, cards, self.trump) else atk
        total = sum(pts(c) for c in att + list(cards))
        self.scores[winner] += total
        self.last = {"att": att, "def": list(cards), "winner": winner, "pts": total}
        self.table = []
        self.refill(winner)
        self.refill(1 - winner)
        self.turn = winner
        self.phase = "attack"
        if self.scores[winner] >= WIN_POINTS:
            self.finish(winner, f"{self.scores[winner]} ochko to'pladi")
        elif not self.hands[winner] or not self.hands[1 - winner]:
            a, b = self.scores
            if a == b:
                self.finish(None, f"Durang, {a}:{b}")
            else:
                self.finish(0 if a > b else 1, f"Hisob {max(a, b)}:{min(a, b)}")
        return None

    def state_for(self, seat):
        over = self.result is not None
        return {
            "t": "state",
            "mode": "bot" if isinstance(self.players[1 - seat], Bot) else "online",
            "me": seat,
            "opp": self.players[1 - seat].name,
            "hand": self.hands[seat],
            "oppN": len(self.hands[1 - seat]),
            "deckN": len(self.deck),
            "trump": self.trump,
            "trumpCard": self.trump_card if self.trump_card in self.deck else None,
            "actor": self.actor(),
            "phase": self.phase,
            "table": self.table,
            "myScore": self.scores[seat],
            "scores": self.scores if over else None,
            "last": self.last,
            "result": self.result,
        }


# ------------------------------------------------------------ bot aqli

def cost(c, trump):
    return strength(c) + (10 if suit(c) == trump else 0)


def bot_move(g, seat):
    hand, trump = g.hands[seat], g.trump
    if g.phase == "attack":
        opp_n = len(g.hands[1 - seat])
        by = {}
        for c in hand:
            by.setdefault(suit(c), []).append(c)
        order = sorted(by, key=lambda s: (s == trump, min(strength(c) for c in by[s])))
        s = order[0]
        cards = sorted(by[s], key=strength)
        strong = [c for c in cards if strength(c) >= 6]
        if s != trump and 2 <= len(strong) <= opp_n and random.random() < 0.6:
            return strong
        return cards[:1]
    att, n = g.table, len(g.table)
    in_play = sum(pts(c) for c in att)
    combos = list(itertools.combinations(hand, n))
    winning = [cb for cb in combos if can_beat(att, cb, trump)]
    cheapest = min(combos, key=lambda cb: sum(pts(c) * 2 + cost(c, trump) for c in cb))
    if winning:
        best = min(winning, key=lambda cb: sum(cost(c, trump) for c in cb))
        if in_play >= 10 or sum(cost(c, trump) for c in best) <= 8 or random.random() < 0.5:
            return list(best)
    return list(cheapest)


# ------------------------------------------------------------ server

rooms = {}   # "0224" -> host Player


async def send(p, obj):
    if isinstance(p, Bot):
        return
    try:
        await p.ws.send_json(obj)
    except Exception:
        pass


async def push(g):
    for seat, p in enumerate(g.players):
        await send(p, g.state_for(seat))


async def bot_run(g):
    try:
        while not g.dead and not g.result and isinstance(g.players[g.actor()], Bot):
            await asyncio.sleep(random.uniform(0.9, 1.8))
            if g.dead:
                break
            s = g.actor()
            err = g.play(s, bot_move(g, s))
            if err:
                logging.error("bot xatosi: %s", err)
                break
            await push(g)
    finally:
        g.busy = False


def kick(g):
    if g.dead or g.busy or g.result:
        return
    if isinstance(g.players[g.actor()], Bot):
        g.busy = True
        asyncio.create_task(bot_run(g))


async def start_game(a, b):
    g = Game(a, b)
    a.game, b.game = g, g
    a.seat, b.seat = 0, 1
    await push(g)
    kick(g)


async def leave(p, notify=True):
    for code, host in list(rooms.items()):
        if host is p:
            del rooms[code]
    g = p.game
    if g:
        g.dead = True
        opp = g.players[1 - p.seat]
        if opp.game is g:
            opp.game = None
            if notify:
                await send(opp, {"t": "opp_left"})
        p.game = None


def new_room_code():
    while True:
        code = f"{random.randint(0, 9999):04d}"
        if code not in rooms:
            return code


async def handle(p, d):
    t = d.get("t")
    if t == "auth":
        p.name = (str(d.get("name") or "Mehmon").strip() or "Mehmon")[:20]
        p.authed = True
        return
    if not p.authed:
        return
    if t == "code":
        p.code = str(d.get("code", ""))[:64]
    elif t == "bot":
        if not p.game:
            await start_game(p, Bot(p.name))
    elif t == "create":
        if p.game:
            return
        for c, h in list(rooms.items()):
            if h is p:
                del rooms[c]
        code = new_room_code()
        rooms[code] = p
        await send(p, {"t": "room", "code": code})
    elif t == "join":
        code = str(d.get("room", "")).strip()
        host = rooms.get(code)
        if p.game:
            return
        if not host or host is p or host.game:
            await send(p, {"t": "error", "msg": "Bunday baza topilmadi"})
            return
        del rooms[code]
        await start_game(host, p)
    elif t == "play":
        g = p.game
        if not g:
            return
        try:
            cards = [int(x) for x in d.get("cards", [])]
        except (TypeError, ValueError):
            return
        err = g.play(p.seat, cards)
        if err:
            await send(p, {"t": "error", "msg": err})
        else:
            await push(g)
            kick(g)
    elif t == "again":
        g = p.game
        if g and g.result:
            g.again[p.seat] = True
            if isinstance(g.players[1 - p.seat], Bot):
                g.again[1 - p.seat] = True
            if all(g.again):
                g.new_round(random.randint(0, 1))
                await push(g)
                kick(g)
            else:
                await send(p, {"t": "waiting"})
    elif t in ("leave", "cancel"):
        await leave(p, notify=(t == "leave"))
        await send(p, {"t": "left"})


async def ws_handler(request):
    ws = web.WebSocketResponse(heartbeat=25)
    await ws.prepare(request)
    p = Player(ws)
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            try:
                d = json.loads(msg.data)
            except ValueError:
                continue
            await handle(p, d)
    finally:
        await leave(p)
    return ws


async def index(request):
    return web.FileResponse(os.path.join(BASE, "static", "index.html"))


def make_app():
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/ws", ws_handler)
    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    web.run_app(make_app(), host="0.0.0.0", port=PORT)
