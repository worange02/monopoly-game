import socket
import threading
import json
import random

# ========== 加载地图数据 ==========
with open("game_data.json", "r", encoding="utf-8") as f:
    GAME_DATA = json.load(f)

MAP = GAME_DATA["map"]
CHANCE_CARDS = GAME_DATA["chance_cards"]

# ========== 服务端全局状态 ==========
clients = {}       # {conn: player_name}
game_state = {
    "started": False,
    "players": {},     # {name: {money, position, properties, jail_turns, bankrupt}}
    "turn_order": [],  # 回合顺序
    "current_turn": 0, # 当前轮到第几个玩家
    "properties": {},    # {格子id: 玩家名}
    "prop_levels": {},   # {格子id: 等级 1/2/3}
}
lock = threading.Lock()

# ========== 广播消息给所有人 ==========
def broadcast(msg):
    data = (msg + "\n").encode("utf-8")
    for conn in list(clients.keys()):
        try:
            conn.sendall(data)
        except:
            pass

# ========== 发消息给某一个人 ==========
def send_to(conn, msg):
    try:
        conn.sendall((msg + "\n").encode("utf-8"))
    except:
        pass

# ========== 根据玩家名找到对应的conn ==========
def get_conn(name):
    for conn, n in clients.items():
        if n == name:
            return conn
    return None

# ========== 保存游戏数据到JSON ==========
def save_game():
    with open("save_data.json", "w", encoding="utf-8") as f:
        json.dump(game_state, f, ensure_ascii=False, indent=2)

# ========== 处理玩家落地事件 ==========
def handle_land(name, position):
    cell = MAP[position]
    conn = get_conn(name)
    player = game_state["players"][name]

    broadcast(f"[移动] {name} 移动到 {cell['name']}（格子{position}）")

    if cell["type"] == "start":
        broadcast(f"[起点] {name} 经过起点，获得200元！")
        player["money"] += 200


    elif cell["type"] in ("property", "station"):

        owner = game_state["properties"].get(str(position))

        if owner is None:

            send_to(conn,
                    f"[购买] {cell['name']} 无人拥有，售价{cell['price']}元，你有{player['money']}元。输入 buy 购买，其他键跳过")

            player["waiting_buy"] = position

        elif owner == name:

            # 自己的地，可以升级

            level = game_state["prop_levels"].get(str(position), 1)

            level_names = {1: "茅房", 2: "房子", 3: "别墅"}

            if level < 3:

                upgrade_cost = int(cell["price"] * 0.5)

                send_to(conn,
                        f"[升级] {cell['name']} 当前{level_names[level]}，升级到{level_names[level + 1]}需要{upgrade_cost}元，你有{player['money']}元。输入 upgrade 升级，其他键跳过")

                player["waiting_upgrade"] = position

            else:

                send_to(conn, f"[提示] {cell['name']} 已是最高级别墅，租金{cell['rent'] * 4}元")

        else:

            level = game_state["prop_levels"].get(str(position), 1)

            multiplier = {1: 1, 2: 2, 3: 4}[level]

            rent = cell["rent"] * multiplier

            player["money"] -= rent

            game_state["players"][owner]["money"] += rent

            level_names = {1: "茅房", 2: "房子", 3: "别墅"}

            broadcast(f"[租金] {name} 踩到 {owner} 的{level_names[level]}，支付租金{rent}元")

            if player["money"] <= 0:
                player["bankrupt"] = True

                broadcast(f"[破产] {name} 破产了！")

                check_game_over()

    elif cell["type"] == "chance":
        card = random.choice(CHANCE_CARDS)
        player["money"] += card["money"]
        broadcast(f"[机会] {name} 抽到机会卡：{card['text']}")
        if player["money"] <= 0:
            player["bankrupt"] = True
            broadcast(f"[破产] {name} 破产了！")
            check_game_over()

    elif cell["type"] == "tax":
        player["money"] -= cell["rent"]
        broadcast(f"[税务] {name} 缴纳税款{cell['rent']}元")
        if player["money"] <= 0:
            player["bankrupt"] = True
            broadcast(f"[破产] {name} 破产了！")
            check_game_over()

    elif cell["type"] == "jail":
        player["jail_turns"] = 2
        broadcast(f"[监狱] {name} 进入监狱，暂停2回合！")

    elif cell["type"] == "station":
        owner = game_state["properties"].get(str(position))
        if owner is None:
            send_to(conn, f"[购买] 火车站 售价{cell['price']}元，你有{player['money']}元。输入 buy 购买，其他键跳过")
            player["waiting_buy"] = position
        elif owner != name:
            rent = cell["rent"]
            player["money"] -= rent
            game_state["players"][owner]["money"] += rent
            broadcast(f"[租金] {name} 踩到 {owner} 的火车站，支付{rent}元")

    save_game()

# ========== 检查游戏是否结束 ==========
WIN_MONEY = 6000   # 先达到3000元获胜
BANKRUPT_MONEY = 0  # 资金归零即破产

def check_game_over():
    # 计算总资产（现金 + 所有地产当前价值）
    def total_assets(player):
        prop_value = sum(MAP[i]["price"] for i in player["properties"])
        return player["money"] + prop_value

    # 检查是否有人总资产达到获胜金额
    for name, p in game_state["players"].items():
        if not p["bankrupt"] and total_assets(p) >= WIN_MONEY:
            assets = total_assets(p)
            broadcast(f"[游戏结束] 🎉 恭喜 {name} 总资产达到 {assets} 元，获得胜利！")
            broadcast(f"[结算] 最终资产排名：")
            rank = sorted(
                game_state["players"].items(),
                key=lambda x: total_assets(x[1]),
                reverse=True
            )
            for i, (n, p2) in enumerate(rank):
                if p2["bankrupt"]:
                    status = "💀破产"
                else:
                    status = f"现金{p2['money']}元 + 地产{sum(MAP[j]['price'] for j in p2['properties'])}元 = 总资产{total_assets(p2)}元"
                broadcast(f"  第{i+1}名：{n} — {status}")
            game_state["started"] = False
            return

    # 检查破产情况
    alive = [n for n, p in game_state["players"].items() if not p["bankrupt"]]
    if len(alive) == 1:
        winner = alive[0]
        assets = total_assets(game_state["players"][winner])
        broadcast(f"[游戏结束] 🎉 其他玩家全部破产，{winner} 获得胜利！总资产：{assets}元")
        broadcast(f"[结算] 最终资产排名：")
        rank = sorted(
            game_state["players"].items(),
            key=lambda x: total_assets(x[1]),
            reverse=True
        )
        for i, (n, p2) in enumerate(rank):
            if p2["bankrupt"]:
                status = "💀破产"
            else:
                status = f"现金{p2['money']}元 + 地产{sum(MAP[j]['price'] for j in p2['properties'])}元 = 总资产{total_assets(p2)}元"
            broadcast(f"  第{i+1}名：{n} — {status}")
        game_state["started"] = False

# ========== 推进到下一个回合 ==========
def next_turn():
    check_game_over()  # 每次推进回合前先检查是否有人获胜
    if not game_state["started"]:  # 如果游戏已结束就不继续
        return
    order = game_state["turn_order"]
    # ... 后面不变
    order = game_state["turn_order"]
    total = len(order)
    for _ in range(total):
        game_state["current_turn"] = (game_state["current_turn"] + 1) % total
        name = order[game_state["current_turn"]]
        if not game_state["players"][name]["bankrupt"]:
            conn = get_conn(name)
            broadcast(f"\n[回合] 现在轮到 {name} 的回合")
            send_to(conn, "[操作] 输入 roll 掷骰子")
            return

# ========== 处理玩家消息 ==========
def handle_message(conn, name, msg):
    msg = msg.strip()

    if not game_state["started"]:
        if msg == "start" and len(clients) >= 2:
            with lock:
                game_state["started"] = True
                game_state["turn_order"] = list(clients.values())
                game_state["current_turn"] = 0
                for n in game_state["turn_order"]:
                    game_state["players"][n] = {
                        "money": 2000,
                        "position": 0,
                        "properties": [],
                        "jail_turns": 0,
                        "bankrupt": False,
                        "waiting_buy": None
                    }
            broadcast("[游戏] 游戏开始！每人初始资金2000元，经过起点获得200元")
            broadcast(f"[回合] 首先由 {game_state['turn_order'][0]} 开始")
            first_conn = get_conn(game_state["turn_order"][0])
            send_to(first_conn, "[操作] 输入 roll 掷骰子")
        elif msg == "start":
            send_to(conn, "[提示] 至少需要2名玩家才能开始")
        return

    player = game_state["players"].get(name)
    if not player:
        return

    # 处理购买决策
    if player.get("waiting_buy") is not None:
        pos = player["waiting_buy"]
        cell = MAP[pos]
        if msg == "buy":
            if player["money"] >= cell["price"]:
                player["money"] -= cell["price"]
                game_state["properties"][str(pos)] = name
                player["properties"].append(pos)
                broadcast(f"[购买] {name} 购买了 {cell['name']}，花费{cell['price']}元，剩余{player['money']}元")
            else:
                send_to(conn, f"[提示] 资金不足，无法购买")
        else:
            send_to(conn, f"[提示] 放弃购买 {cell['name']}")
        player["waiting_buy"] = None
        save_game()
        next_turn()
        return
        # 处理升级决策
    if player.get("waiting_upgrade") is not None:
            pos = player["waiting_upgrade"]
            cell = MAP[pos]
            upgrade_cost = int(cell["price"] * 0.5)
            if msg == "upgrade":
                if player["money"] >= upgrade_cost:
                    player["money"] -= upgrade_cost
                    current_level = game_state["prop_levels"].get(str(pos), 1)
                    new_level = current_level + 1
                    game_state["prop_levels"][str(pos)] = new_level
                    level_names = {1: "茅房", 2: "房子", 3: "别墅"}
                    new_rent = cell["rent"] * {1: 1, 2: 2, 3: 4}[new_level]
                    broadcast(
                        f"[升级] {name} 将 {cell['name']} 升级为{level_names[new_level]}！花费{upgrade_cost}元，新租金{new_rent}元")
                else:
                    send_to(conn, f"[提示] 资金不足，无法升级")
            else:
                send_to(conn, f"[提示] 放弃升级")
            player["waiting_upgrade"] = None
            save_game()
            next_turn()
            return

    # 掷骰子
    if msg == "roll":
        current_name = game_state["turn_order"][game_state["current_turn"]]
        if name != current_name:
            send_to(conn, "[提示] 还没轮到你")
            return

        if player["jail_turns"] > 0:
            player["jail_turns"] -= 1
            broadcast(f"[监狱] {name} 在监狱中，剩余{player['jail_turns']}回合")
            next_turn()
            return

        dice = random.randint(2, 12)
        old_pos = player["position"]
        new_pos = (old_pos + dice) % len(MAP)

        # 经过起点
        if new_pos < old_pos:
            player["money"] += 200
            broadcast(f"[起点] {name} 经过起点，获得200元！")

        player["position"] = new_pos
        broadcast(f"[骰子] {name} 掷出了 {dice} 点")
        handle_land(name, new_pos)

        # 如果没有等待购买决策，直接下一回合
        if player.get("waiting_buy") is None:
            next_turn()

    # 查看状态
    elif msg == "status":
        send_to(conn, "===== 当前状态 =====")
        for n, p in game_state["players"].items():
            props = [MAP[i]["name"] for i in p["properties"]]
            prop_str = "、".join(props) if props else "无"
            send_to(conn, f"{n}：{p['money']}元 | 位置：{MAP[p['position']]['name']} | 地产：{prop_str}")
        send_to(conn, "===================")

# ========== 每个客户端的处理线程 ==========
def client_thread(conn, addr):
    send_to(conn, "[连接] 已连接到大富翁服务器！请输入你的名字：")
    try:
        name = conn.recv(1024).decode("utf-8").strip()
        with lock:
            clients[conn] = name
            game_state["players"][name] = {
                "money": 2000, "position": 0, "properties": [],
                "jail_turns": 0, "bankrupt": False, "waiting_buy": None
            }
        broadcast(f"[加入] {name} 加入了游戏！当前人数：{len(clients)}")
        send_to(conn, f"[当前玩家] {','.join(clients.values())}")
        send_to(conn, f"[提示] 等待其他玩家加入，2人以上房主输入 start 开始游戏，随时输入 status 查看状态")

        while True:
            data = conn.recv(1024).decode("utf-8")
            if not data:
                break
            handle_message(conn, name, data)

    except Exception as e:
        print(f"[错误] {e}")
    finally:
        with lock:
            name = clients.pop(conn, "未知")
            broadcast(f"[离开] {name} 离开了游戏")
        conn.close()

# ========== 启动服务端 ==========
def main():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 9999))
    server.listen(4)
    print("=" * 40)
    print("  大富翁服务器启动，等待玩家连接...")
    print("=" * 40)
    while True:
        conn, addr = server.accept()
        print(f"[连接] 新玩家接入：{addr}")
        threading.Thread(target=client_thread, args=(conn, addr), daemon=True).start()

if __name__ == "__main__":
    main()