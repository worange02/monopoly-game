# server_ws.py - 修复房间号回收和释放问题，修复旅游路线逻辑

import asyncio
import websockets
import json
import random
import time
from datetime import datetime
from maps_config import *
from avatars_config import AVATARS, CHAT_EMOJIS
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

# ========== 全局状态 ==========
rooms = {}
next_room_number = 1
AVAILABLE_ROOM_NUMBERS = []

def generate_room_id():
    global next_room_number
    if AVAILABLE_ROOM_NUMBERS:
        return AVAILABLE_ROOM_NUMBERS.pop(0)
    else:
        room_id = next_room_number
        next_room_number += 1
        return room_id

def recycle_room_id(room_id):
    if room_id not in AVAILABLE_ROOM_NUMBERS:
        AVAILABLE_ROOM_NUMBERS.append(room_id)
        AVAILABLE_ROOM_NUMBERS.sort()
    print(f"[房间回收] 房间号 {room_id} 已回收，当前可用房间号: {AVAILABLE_ROOM_NUMBERS}")

async def clean_empty_rooms():
    """每5分钟清理一次空房间"""
    while True:
        await asyncio.sleep(300)  # 300秒 = 5分钟
        for room_id, room in list(rooms.items()):
            # 统计真实玩家（非观战、非破产、还在线）
            real_players = [
                n for n, p in room.game_state["players"].items()
                if not p.get("spectator", False) 
                and not p.get("bankrupt", False)
                and n in room.players  # 还在WebSocket连接中
            ]
            if len(real_players) == 0:
                del rooms[room_id]
                recycle_room_id(room_id)
                print(f"[定时清理] 房间号 {room_id} 已删除，当前房间数: {len(rooms)}")

class Room:
    def __init__(self, room_id, owner):
        self.room_id = room_id
        self.owner = owner
        self.players = {}
        self.started = False
        self.game_state = {
            "players": {},
            "turn_order": [],
            "current_turn": 0,
            "properties": {},
            "prop_levels": {},
            "current_map": "中国之旅",
            "tour_mode": {},
        }
        
    def to_dict(self):
        return {
            "room_id": self.room_id,
            "owner": self.owner,
            "player_count": len(self.players),
            "players": list(self.players.keys()),
            "started": self.started
        }
    
    def reset_game_state(self):
        """重置游戏状态（用于新一局）"""
        current_map_name = self.game_state["current_map"]
        self.game_state = {
            "players": {},
            "turn_order": [],
            "current_turn": 0,
            "properties": {},
            "prop_levels": {},
            "current_map": current_map_name,
            "tour_mode": {},
        }
        # 保留玩家列表但重置状态
        for name in list(self.players.keys()):
            self.game_state["players"][name] = {
                "money": 0,
                "position": 0,
                "properties": [],
                "jail_turns": 0,
                "bankrupt": False,
                "disconnected": False,
                "spectator": False,
                "avatar": self.game_state["players"].get(name, {}).get("avatar", "xiaotu"),
                "auto_turn": False,
                "custom_avatar": self.game_state["players"].get(name, {}).get("custom_avatar", None),
            }

rooms = {}

def get_avatar_info(avatar_id):
    for av in AVATARS:
        if av["id"] == avatar_id:
            return av
    return AVATARS[0]

def get_map_data(map_name):
    return ALL_MAPS.get(map_name, CHINA_MAP)

def get_start_money(map_name):
    return MAP_START_MONEY.get(map_name, 40000)

def get_rent_by_level(cell, level):
    if "rents" in cell and level <= 5:
        return cell["rents"][level]
    multiplier = {0: 1, 1: 1, 2: 2, 3: 3, 4: 4, 5: 5}.get(level, 1)
    return cell.get("rent", 0) * multiplier

def get_level_name(level):
    names = {0: "空地", 1: "🏠一房", 2: "🏠🏠二房", 3: "🏠🏠🏠三房", 4: "🏠🏠🏠🏠四房", 5: "🏨旅馆"}
    return names.get(level, "空地")

async def broadcast_to_room(room, msg, msg_type="log"):
    data = json.dumps({"type": msg_type, "msg": msg}, ensure_ascii=False)
    to_remove = []
    for name, ws in room.players.items():
        try:
            await ws.send(data)
        except:
            to_remove.append(name)
    for name in to_remove:
        if name in room.players:
            del room.players[name]

async def broadcast_room_state(room):
    state = build_state_snapshot(room)
    data = json.dumps({"type": "state", "state": state}, ensure_ascii=False)
    to_remove = []
    for name, ws in room.players.items():
        try:
            await ws.send(data)
        except:
            to_remove.append(name)
    for name in to_remove:
        if name in room.players:
            del room.players[name]

async def broadcast_room_chat(room, name, msg):
    chat_data = json.dumps({
        "type": "chat",
        "name": name,
        "msg": msg,
        "time": datetime.now().strftime("%H:%M:%S")
    }, ensure_ascii=False)
    to_remove = []
    for n, ws in room.players.items():
        try:
            await ws.send(chat_data)
        except:
            to_remove.append(n)
    for n in to_remove:
        if n in room.players:
            del room.players[n]

def build_state_snapshot(room):
    current_map = get_map_data(room.game_state["current_map"])
    players_info = {}
    for name, p in room.game_state["players"].items():
        prop_value = 0
        for prop_id in p.get("properties", []):
            found = False
            for cell in current_map:
                if str(cell["id"]) == str(prop_id):
                    prop_value += cell.get("price", 0)
                    found = True
                    break
            if not found:
                for cell in TOURIST_ROUTE:
                    if str(cell["id"]) == str(prop_id):
                        prop_value += cell.get("price", 0)
                        break
        
        position = p["position"]
        if room.game_state["tour_mode"].get(name, {}).get("active", False):
            tour_data = room.game_state["tour_mode"][name]
            position = f"tour_{tour_data['position']}"
        
        players_info[name] = {
            "money": p["money"],
            "position": position,
            "properties": p.get("properties", []),
            "jail_turns": p.get("jail_turns", 0),
            "bankrupt": p.get("bankrupt", False),
            "disconnected": p.get("disconnected", False),
            "total": p["money"] + prop_value,
            "avatar": p.get("avatar", "xiaotu"),
            "custom_avatar": p.get("custom_avatar", None),
            "spectator": p.get("spectator", False),
            "auto_turn": p.get("auto_turn", False),
        }
    
    return {
        "started": room.started,
        "players": players_info,
        "turn_order": room.game_state["turn_order"],
        "current_turn": room.game_state["current_turn"],
        "current_round": room.game_state.get("current_round", 0),  # 添加回合数
        "properties": room.game_state["properties"],
        "prop_levels": room.game_state["prop_levels"],
        "player_list": list(room.players.keys()),
        "current_map": room.game_state["current_map"],
        "room_id": str(room.room_id),
        "owner": room.owner
    }

def get_ws_by_name(room, name):
    return room.players.get(name)

def total_assets(room, player):
    current_map = get_map_data(room.game_state["current_map"])
    prop_value = 0
    for prop_id in player.get("properties", []):
        for cell in current_map:
            if str(cell["id"]) == str(prop_id):
                prop_value += cell.get("price", 0)
                break
        for cell in TOURIST_ROUTE:
            if str(cell["id"]) == str(prop_id):
                prop_value += cell.get("price", 0)
                break
    return player["money"] + prop_value

WIN_MONEY = 50000

async def check_game_over(room):
    for name, p in room.game_state["players"].items():
        if not p.get("bankrupt", False) and not p.get("spectator", False) and total_assets(room, p) >= WIN_MONEY:
            await broadcast_to_room(room, f"[游戏结束] 🎉 恭喜 {name} 总资产达到 {WIN_MONEY} 元，获得胜利！")
            rank = sorted(room.game_state["players"].items(),
                          key=lambda x: total_assets(room, x[1]), reverse=True)
            for i, (n, p2) in enumerate(rank):
                if p2.get("spectator"):
                    status = "观战"
                else:
                    status = "💀破产" if p2.get("bankrupt") else f"总资产{total_assets(room, p2)}元"
                if p2.get("disconnected"):
                    status += " (离线)"
                await broadcast_to_room(room, f"  第{i+1}名：{n} — {status}")
            room.started = False
            room.reset_game_state()
            await broadcast_to_room(room, "[系统] 🎮 游戏结束，可以开始新的一局！")
            await broadcast_room_state(room)
            return True

    alive = [n for n, p in room.game_state["players"].items() if not p.get("bankrupt", False) and not p.get("spectator", False)]
    if len(alive) <= 1 and len([n for n, p in room.game_state["players"].items() if not p.get("spectator", False)]) > 0:
        winner = alive[0] if alive else None
        if winner:
            await broadcast_to_room(room, f"[游戏结束] 🎉 其他玩家全部破产，{winner} 获得胜利！")
        else:
            await broadcast_to_room(room, f"[游戏结束] 🎉 游戏结束！")
        room.started = False
        room.reset_game_state()
        await broadcast_to_room(room, "[系统] 🎮 游戏结束，可以开始新的一局！")
        await broadcast_room_state(room)
        return True
    return False

async def auto_roll_and_move(room, name):
    """托管玩家自动掷骰子并移动"""
    player = room.game_state["players"][name]
    current_map = get_map_data(room.game_state["current_map"])

    # 检查是否破产或观战
    if player.get("bankrupt", False) or player.get("spectator", False):
        await next_turn(room)
        return
    
    # 检查是否被免费停车场冻结
    skip_until = player.get("skip_turn_until")
    current_round = room.game_state.get("current_round", 0)
    if skip_until is not None and skip_until > current_round:
        await broadcast_to_room(room, f"[免费停车场] {name} 正在休息，跳过本回合")
        await next_turn(room)
        return
    elif skip_until is not None and skip_until <= current_round:
        player["skip_turn_until"] = None
        await broadcast_to_room(room, f"[免费停车场] {name} 休息结束，可以继续行动")

    # 检查监狱
    if player.get("jail_turns", 0) > 0:
        player["jail_turns"] -= 1
        await broadcast_to_room(room, f"[托管] {name} 在监狱中，剩余{player['jail_turns']}回合")
        await next_turn(room)
        return
    
    # 检查旅游模式
    if room.game_state["tour_mode"].get(name, {}).get("active", False):
        await auto_tour_roll(room, name)
        return

    # 正常掷骰子
    dice = random.randint(1, 6)
    dice_gif = f"{dice}.gif"  # 添加这行
    old_pos = player["position"]
    total_cells = len(current_map)
    new_pos = (old_pos + dice) % total_cells
    passed_start = old_pos + dice >= total_cells

    await broadcast_to_room(room, f"[托管] {name} 自动掷骰子: {dice} 点")

    # 广播骰子结果
    dice_data = json.dumps({
        "type": "dice_result_public",
        "name": name,
        "dice": dice,
        "old_pos": old_pos,
        "new_pos": new_pos,
        "total_cells": total_cells,
        "passed_start": passed_start
    }, ensure_ascii=False)
    for ws in room.players.values():
        try:
            await ws.send(dice_data)
        except:
            pass

    await process_move(room, name, dice, old_pos, new_pos, passed_start)

async def auto_tour_roll(room, name):
    """旅游模式下的自动掷骰子"""
    player = room.game_state["players"][name]
    tour_data = room.game_state["tour_mode"][name]
    route = TOURIST_ROUTE
    current_pos = tour_data["position"]
    
    dice = random.randint(1, 3)
    dice_gif = f"{dice}.gif"  # 添加这行
    new_pos = current_pos + dice
    
    await broadcast_to_room(room, f"[托管] {name} 在旅游路线中掷出 {dice} 步")
    
    dice_data = json.dumps({
        "type": "dice_result_public",
        "name": name,
        "dice": dice,
        "dice_gif": dice_gif,  # 添加这行
        "is_tour": True,
        "tour_old_pos": current_pos,
        "tour_new_pos": new_pos
    }, ensure_ascii=False)
    for ws in room.players.values():
        try:
            await ws.send(dice_data)
        except:
            pass
    
    tour_data["position"] = new_pos
    await broadcast_room_state(room)
    await asyncio.sleep(0.5)
    await handle_tour_land(room, name, new_pos)

async def process_move(room, name, dice, old_pos, new_pos, passed_start=False):
    """处理移动逻辑"""
    player = room.game_state["players"][name]
    current_map = get_map_data(room.game_state["current_map"])
    total_cells = len(current_map)

    # 经过起点奖励3000元
    if passed_start:
        start_bonus = current_map[0].get("pass_bonus", 3000)
        player["money"] += start_bonus
        await broadcast_to_room(room, f"[起点] {name} 经过起点，获得{start_bonus}元！")

    player["position"] = new_pos
    await broadcast_room_state(room)
    await asyncio.sleep(0.5)
    await handle_land(room, name, new_pos, passed_start)

async def enter_tour_mode(room, name, original_pos):
    """进入旅游模式"""
    room.game_state["tour_mode"][name] = {
        "active": True,
        "position": 0,
        "original_pos": original_pos
    }
    await broadcast_to_room(room, f"[旅游] {name} 来到旅游出发点，开始旅游路线！")
    ws = get_ws_by_name(room, name)
    if ws:
        await ws.send(json.dumps({"type": "your_turn", "msg": "旅游模式：请掷骰子(1-3步)前进"}, ensure_ascii=False))

async def handle_tour_land(room, name, tour_pos):
    """处理旅游路线落地"""
    tour_data = room.game_state["tour_mode"].get(name)
    if not tour_data or not tour_data["active"]:
        return
    
    route = TOURIST_ROUTE
    if tour_pos >= len(route):
        await exit_tour_mode(room, name)
        return
    
    cell = route[tour_pos]
    player = room.game_state["players"][name]
    ws = get_ws_by_name(room, name)
    
    await broadcast_to_room(room, f"[旅游] {name} 到达景点：{cell['name']}")
    
    if cell["type"] == "travel_end":
        await exit_tour_mode(room, name)
        return
    
    is_auto = player.get("auto_turn", False)
    
    if cell["type"] == "scenic":
        prop_id = str(cell["id"])
        owner = room.game_state["properties"].get(prop_id)
        level = room.game_state["prop_levels"].get(prop_id, 0)
        
        if owner is None:
            if is_auto:
                # 托管模式：自动判断是否购买
                if player["money"] >= cell["price"]:
                    player["money"] -= cell["price"]
                    room.game_state["properties"][prop_id] = name
                    room.game_state["prop_levels"][prop_id] = 0
                    if cell["id"] not in player.get("properties", []):
                        player["properties"].append(cell["id"])
                    await broadcast_to_room(room, f"[托管购买] {name} 自动购买了景点 {cell['name']}，花费{cell['price']}元")
                else:
                    await broadcast_to_room(room, f"[托管] {name} 资金不足({player['money']}<{cell['price']})，放弃购买景点 {cell['name']}")
                await next_turn(room)
                return
            elif ws and not player.get("disconnected") and not player.get("spectator"):
                await ws.send(json.dumps({
                    "type": "ask_buy",
                    "msg": f"景点 {cell['name']} 无人拥有，售价{cell['price']}元，你有{player['money']}元。是否购买？",
                    "cell": cell["id"],
                    "is_tour": True,
                    "tour_pos": tour_pos,
                    "prop_id": prop_id
                }, ensure_ascii=False))
                player["waiting_buy"] = prop_id
                player["waiting_buy_tour_pos"] = tour_pos
                player["waiting_buy_cell"] = cell
                return
            else:
                await next_turn(room)
                return
        elif owner == name:
            if level < 5:
                upgrade_cost = cell.get("build_cost", cell["price"] * 0.5)
                if is_auto:
                    # 托管模式：自动判断是否升级
                    if player["money"] >= upgrade_cost:
                        player["money"] -= upgrade_cost
                        room.game_state["prop_levels"][prop_id] = level + 1
                        await broadcast_to_room(room, f"[托管升级] {name} 自动将{cell['name']}升级为{get_level_name(level + 1)}")
                    else:
                        await broadcast_to_room(room, f"[托管] {name} 资金不足({player['money']}<{upgrade_cost})，放弃升级景点 {cell['name']}")
                    await next_turn(room)
                    return
                elif ws and not player.get("disconnected") and not player.get("spectator"):
                    await ws.send(json.dumps({
                        "type": "ask_upgrade",
                        "msg": f"{cell['name']} 当前{get_level_name(level)}，升级需{upgrade_cost}元，是否升级？",
                        "cell": cell["id"],
                        "is_tour": True,
                        "tour_pos": tour_pos,
                        "prop_id": prop_id
                    }, ensure_ascii=False))
                    player["waiting_upgrade"] = prop_id
                    player["waiting_upgrade_tour_pos"] = tour_pos
                    player["waiting_upgrade_cell"] = cell
                    return
                else:
                    await next_turn(room)
                    return
            else:
                await next_turn(room)
                return
        else:
            rent = get_rent_by_level(cell, level)
            if player["money"] >= rent:
                player["money"] -= rent
                room.game_state["players"][owner]["money"] += rent
                await broadcast_to_room(room, f"[租金] {name} 在{cell['name']}支付给{owner}租金{rent}元")
            else:
                player["bankrupt"] = True
                await broadcast_to_room(room, f"[破产] {name} 资金不足，破产了！")
            await next_turn(room)
            return
    
    await next_turn(room)

async def exit_tour_mode(room, name):
    """退出旅游模式，返回主地图的旅游结束点（格子48）"""
    tour_data = room.game_state["tour_mode"].pop(name, None)
    if tour_data:
        # 旅游结束后回到格子48（济南/旅游结束点）
        room.game_state["players"][name]["position"] = 48
        await broadcast_to_room(room, f"[旅游] {name} 完成旅游，回到主地图的旅游结束点")
    await broadcast_room_state(room)
    await next_turn(room)

async def handle_land(room, name, position, passed_start=False):
    current_map = get_map_data(room.game_state["current_map"])
    cell = current_map[position]
    ws = get_ws_by_name(room, name)
    player = room.game_state["players"][name]
    
    await broadcast_to_room(room, f"[移动] {name} 来到 {cell['name']}")
    await broadcast_room_state(room)
    
    is_auto = player.get("auto_turn", False)
    
    # 停留在起点，额外获得3000元
    if cell["type"] == "start":
        bonus = cell.get("pass_bonus", 3000)
        player["money"] += bonus
        await broadcast_to_room(room, f"[起点] {name} 停留在起点，额外获得{bonus}元！")
        await next_turn(room)
        return
    
    # ========== 免费停车场逻辑 ==========
    if cell["type"] == "free_parking":
        current_round = room.game_state.get("current_round", 0)
        player["skip_turn_until"] = current_round + 1
        await broadcast_to_room(room, f"[免费停车场] {name} 在此休息，下一轮自动跳过")
        await next_turn(room)
        return

    if cell["type"] in ("chance", "fate", "stock"):
        card_type_map = {"chance": "机会", "fate": "命运", "stock": "股票分析"}
        card_type = card_type_map[cell["type"]]
        
        if card_type == "机会":
            card = random.choice(CHANCE_CARDS)
        elif card_type == "命运":
            card = random.choice(FATE_CARDS)
        else:
            card = random.choice(STOCK_CARDS)
        
        if is_auto:
            await handle_card_effect(room, name, card, card_type)
            return
        elif ws and not player.get("disconnected") and not player.get("spectator"):
            await ws.send(json.dumps({
                "type": "show_card",
                "card": card,
                "card_type": card_type,
                "msg": f"🎴 {card_type}卡：{card['text']}"
            }, ensure_ascii=False))
            player["waiting_card"] = card
            player["waiting_card_type"] = card_type
            return
        else:
            await handle_card_effect(room, name, card, card_type)
            return
    
    if cell["type"] == "tax":
        player["money"] -= cell["rent"]
        await broadcast_to_room(room, f"[税务] {name} 缴纳税款{cell['rent']}元")
        if player["money"] <= 0 and not player.get("spectator"):
            player["bankrupt"] = True
            await broadcast_to_room(room, f"[破产] {name} 破产了！")
        await next_turn(room)
        return
    
    if cell["type"] == "travel_start":
        await enter_tour_mode(room, name, position)
        return
    
    if cell["type"] in ("property", "railway"):
        prop_id = str(cell["id"])
        owner = room.game_state["properties"].get(prop_id)
        
        if owner is None:
            # 无人拥有，可以购买
            if is_auto:
                # 托管模式：自动判断是否购买
                if player["money"] >= cell["price"]:
                    player["money"] -= cell["price"]
                    room.game_state["properties"][prop_id] = name
                    room.game_state["prop_levels"][prop_id] = 0
                    if cell["id"] not in player.get("properties", []):
                        player["properties"].append(cell["id"])
                    await broadcast_to_room(room, f"[托管购买] {name} 自动购买了 {cell['name']}，花费{cell['price']}元")
                else:
                    await broadcast_to_room(room, f"[托管] {name} 资金不足({player['money']}<{cell['price']})，放弃购买 {cell['name']}")
                await next_turn(room)
                return
            elif ws and not player.get("disconnected") and not player.get("spectator"):
                await ws.send(json.dumps({
                    "type": "ask_buy",
                    "msg": f"{cell['name']} 无人拥有，售价{cell['price']}元，你有{player['money']}元。是否购买？",
                    "cell": position,
                    "is_tour": False,
                    "prop_id": prop_id
                }, ensure_ascii=False))
                player["waiting_buy"] = prop_id
                player["waiting_buy_cell"] = cell
                return
            else:
                await next_turn(room)
                return
        elif owner == name:
            # 自己拥有，可以升级
            level = room.game_state["prop_levels"].get(prop_id, 0)
            if level < 5:
                upgrade_cost = cell.get("build_cost", cell["price"] * 0.5)
                if is_auto:
                    # 托管模式：自动判断是否升级
                    if player["money"] >= upgrade_cost:
                        player["money"] -= upgrade_cost
                        room.game_state["prop_levels"][prop_id] = level + 1
                        await broadcast_to_room(room, f"[托管升级] {name} 自动将{cell['name']}升级为{get_level_name(level + 1)}")
                    else:
                        await broadcast_to_room(room, f"[托管] {name} 资金不足({player['money']}<{upgrade_cost})，放弃升级 {cell['name']}")
                    await next_turn(room)
                    return
                elif ws and not player.get("disconnected") and not player.get("spectator"):
                    await ws.send(json.dumps({
                        "type": "ask_upgrade",
                        "msg": f"{cell['name']} 当前{get_level_name(level)}，升级需{upgrade_cost}元，是否升级？",
                        "cell": position,
                        "is_tour": False,
                        "prop_id": prop_id
                    }, ensure_ascii=False))
                    player["waiting_upgrade"] = prop_id
                    player["waiting_upgrade_cell"] = cell
                    return
                else:
                    await next_turn(room)
                    return
            else:
                await next_turn(room)
                return
        else:
            # 其他人拥有，支付租金
            level = room.game_state["prop_levels"].get(prop_id, 0)
            if cell["type"] == "railway":
                railway_count = sum(1 for p in room.game_state["players"][owner].get("properties", [])
                                   if str(p) in current_map and current_map[int(p)]["type"] == "railway")
                rent = cell.get("rent", 100) * max(1, railway_count)
            else:
                rent = get_rent_by_level(cell, level)
            
            if player["money"] >= rent:
                player["money"] -= rent
                room.game_state["players"][owner]["money"] += rent
                await broadcast_to_room(room, f"[租金] {name} 支付给{owner}租金{rent}元")
            else:
                player["bankrupt"] = True
                await broadcast_to_room(room, f"[破产] {name} 资金不足，破产了！")
            await next_turn(room)
            return
    
    await next_turn(room)

async def handle_card_effect(room, name, card, card_type):
    player = room.game_state["players"][name]
    current_map = get_map_data(room.game_state["current_map"])
    
    await broadcast_to_room(room, f"[{card_type}] {name} 抽到：{card['text']}")
    
    if "money" in card:
        player["money"] += card["money"]
        if card["money"] > 0:
            await broadcast_to_room(room, f"[{card_type}] {name} +{card['money']}元")
        else:
            await broadcast_to_room(room, f"[{card_type}] {name} {card['money']}元")
    
    elif card.get("action") == "goto_start":
        player["money"] += card.get("money", 0)
        old_pos = player["position"]
        player["position"] = 0
        await broadcast_to_room(room, f"[{card_type}] {name} 移动到起点，获得{card.get('money', 0)}元")
        # 触发起点停留事件（获得额外奖励）
        await handle_land(room, name, 0, False)
        return
    
    elif card.get("action") == "goto_beijing":
        beijing_pos = 42
        old_pos = player["position"]
        player["position"] = beijing_pos
        await broadcast_to_room(room, f"[{card_type}] {name} 移动到北京")
        # 触发落地事件（会检查是否被占有并支付租金）
        await handle_land(room, name, beijing_pos, False)
        return
    
    elif card.get("action") == "collect_from_all":
        for other_name, other_p in room.game_state["players"].items():
            if other_name != name and not other_p.get("bankrupt") and not other_p.get("spectator"):
                other_p["money"] -= card["money"]
                player["money"] += card["money"]
        await broadcast_to_room(room, f"[{card_type}] {name} 从每位玩家收取{card['money']}元")
    
    elif card.get("action") == "move_forward":
        new_pos = (player["position"] + card["steps"]) % len(current_map)
        passed_start = player["position"] + card["steps"] >= len(current_map)
        player["position"] = new_pos
        await broadcast_to_room(room, f"[{card_type}] {name} 前进{card['steps']}步")
        await handle_land(room, name, new_pos, passed_start)
        return
    
    elif card.get("action") == "move_back":
        new_pos = (player["position"] - card["steps"]) % len(current_map)
        player["position"] = new_pos
        await broadcast_to_room(room, f"[{card_type}] {name} 后退{card['steps']}步")
        await handle_land(room, name, new_pos, False)
        return
    
    elif card.get("action") == "free_property":
        for cell in current_map:
            if cell["type"] in ("property", "railway"):
                prop_id = str(cell["id"])
                if prop_id not in room.game_state["properties"]:
                    room.game_state["properties"][prop_id] = name
                    room.game_state["prop_levels"][prop_id] = 0
                    if cell["id"] not in player.get("properties", []):
                        player["properties"].append(cell["id"])
                    await broadcast_to_room(room, f"[{card_type}] {name} 免费获得 {cell['name']}！")
                    break
    
    elif card.get("action") == "auction_property":
        # 紧急支出：拍卖一块地产获得现金
        my_properties = player.get("properties", [])
        if my_properties:
            # 随机选择一块地产拍卖
            import random
            prop_id = random.choice(my_properties)
            
            # 获取地产价格
            prop_price = 0
            prop_name = ""
            for cell in current_map:
                if cell["id"] == prop_id and cell["type"] in ("property", "railway"):
                    prop_price = cell.get("price", 2000)
                    prop_name = cell.get("name", f"格子{prop_id}")
                    break
            
            half_price = prop_price // 2
            
            # 移除地产
            del room.game_state["properties"][str(prop_id)]
            if str(prop_id) in room.game_state["prop_levels"]:
                del room.game_state["prop_levels"][str(prop_id)]
            player["properties"].remove(prop_id)
            
            # 获得一半现金
            player["money"] += half_price
            
            await broadcast_to_room(room, f"[{card_type}] {name} 拍卖了 {prop_name}，获得 {half_price} 元")
        else:
            await broadcast_to_room(room, f"[{card_type}] {name} 没有地产可拍卖，跳过")

    if player["money"] <= 0 and not player.get("spectator"):
        player["bankrupt"] = True
        await broadcast_to_room(room, f"[破产] {name} 破产了！")

    await broadcast_room_state(room)

    has_pending = (player.get("waiting_buy") or 
                   player.get("waiting_upgrade") or
                   player.get("waiting_card"))

    if not has_pending:
        if room.game_state["tour_mode"].get(name, {}).get("active", False):
            ws = get_ws_by_name(room, name)
            if ws:
                await ws.send(json.dumps({"type": "your_turn", "msg": "旅游模式：请继续掷骰子(1-3步)前进"}, ensure_ascii=False))
        else:
            await next_turn(room)

async def next_turn(room):
    if await check_game_over(room):
        return
    if not room.started:
        return

    # 更新全局回合计数器
    room.game_state["current_round"] = room.game_state.get("current_round", 0) + 1
    current_round = room.game_state["current_round"]
    
    order = room.game_state["turn_order"]
    active_players = [n for n in order if not room.game_state["players"][n].get("bankrupt", False) 
                      and not room.game_state["players"][n].get("spectator", False)]
    
    if len(active_players) == 0:
        await broadcast_to_room(room, "[游戏结束] 没有存活的玩家，游戏结束")
        room.started = False
        room.reset_game_state()
        await broadcast_to_room(room, "[系统] 🎮 游戏结束，可以开始新的一局！")
        await broadcast_room_state(room)
        return
    
    total = len(order)
    
    # 查找下一个可以行动的玩家
    for _ in range(total):
        room.game_state["current_turn"] = (room.game_state["current_turn"] + 1) % total
        name = order[room.game_state["current_turn"]]
        player = room.game_state["players"].get(name)
        
        if not player:
            continue
        
        # 检查是否破产或观战
        if player.get("bankrupt", False) or player.get("spectator", False):
            continue
        
        # ========== 免费停车场冻结检查 ==========
        skip_until = player.get("skip_turn_until")
        if skip_until is not None and skip_until > current_round:
            # 还在冻结期内，跳过该玩家
            await broadcast_to_room(room, f"[免费停车场] {name} 正在休息，跳过本回合")
            # 清除等待状态
            player["waiting_buy"] = None
            player["waiting_upgrade"] = None
            player["waiting_card"] = None
            # 继续查找下一个玩家
            continue
        elif skip_until is not None and skip_until <= current_round:
            # 冻结期结束，清除标记
            player["skip_turn_until"] = None
            await broadcast_to_room(room, f"[免费停车场] {name} 休息结束，可以继续行动")
        
        # 找到可以行动的玩家
        if room.game_state["tour_mode"].get(name, {}).get("active", False):
            tour_data = room.game_state["tour_mode"][name]
            ws = get_ws_by_name(room, name)
            if ws:
                await ws.send(json.dumps({
                    "type": "your_turn", 
                    "msg": f"旅游模式：当前位置 {tour_data['position']}/11，请掷骰子(1-3步)前进"
                }, ensure_ascii=False))
            await broadcast_room_state(room)
            return
        
        await broadcast_to_room(room, f"[回合] 现在轮到 {name} 的回合")
        ws = get_ws_by_name(room, name)
        
        if player.get("auto_turn", False) or player.get("disconnected", False) or not ws:
            await broadcast_to_room(room, f"[托管] {name} 处于托管状态，自动操作")
            await auto_roll_and_move(room, name)
            return
        
        if ws and not player.get("disconnected"):
            await ws.send(json.dumps({"type": "your_turn", "msg": "轮到你了！请掷骰子"}, ensure_ascii=False))
        await broadcast_room_state(room)
        return
    
    # 没有找到可行动的玩家，游戏结束
    await broadcast_to_room(room, "[游戏结束] 没有存活的玩家，游戏结束")
    room.started = False
    room.reset_game_state()
    await broadcast_to_room(room, "[系统] 🎮 游戏结束，可以开始新的一局！")
    await broadcast_room_state(room)

    order = room.game_state["turn_order"]
    active_players = [n for n in order if not room.game_state["players"][n].get("bankrupt", False) 
                      and not room.game_state["players"][n].get("spectator", False)]
    
    if len(active_players) == 0:
        await broadcast_to_room(room, "[游戏结束] 没有存活的玩家，游戏结束")
        room.started = False
        room.reset_game_state()
        await broadcast_to_room(room, "[系统] 🎮 游戏结束，可以开始新的一局！")
        await broadcast_room_state(room)
        return
    
    total = len(order)
    
    for _ in range(total):
        room.game_state["current_turn"] = (room.game_state["current_turn"] + 1) % total
        name = order[room.game_state["current_turn"]]
        player = room.game_state["players"].get(name)
        if player and not player.get("bankrupt", False) and not player.get("spectator", False):
            if room.game_state["tour_mode"].get(name, {}).get("active", False):
                tour_data = room.game_state["tour_mode"][name]
                ws = get_ws_by_name(room, name)
                if ws:
                    await ws.send(json.dumps({"type": "your_turn", "msg": f"旅游模式：当前位置 {tour_data['position']}/11，请掷骰子(1-3步)前进"}, ensure_ascii=False))
                await broadcast_room_state(room)
                return
            
            await broadcast_to_room(room, f"[回合] 现在轮到 {name} 的回合")
            ws = get_ws_by_name(room, name)
            
            if player.get("auto_turn", False) or player.get("disconnected", False) or not ws:
                await broadcast_to_room(room, f"[托管] {name} 处于托管状态，自动操作")
                await auto_roll_and_move(room, name)
                return
            
            if ws and not player.get("disconnected"):
                await ws.send(json.dumps({"type": "your_turn", "msg": "轮到你了！请掷骰子"}, ensure_ascii=False))
            await broadcast_room_state(room)
            return
    
    await broadcast_to_room(room, "[游戏结束] 没有存活的玩家，游戏结束")
    room.started = False
    room.reset_game_state()
    await broadcast_to_room(room, "[系统] 🎮 游戏结束，可以开始新的一局！")
    await broadcast_room_state(room)

async def handle_message(room, ws, name, data):
    msg = data.get("action", "").strip()
    player = room.game_state["players"].get(name)
    if not player:
        return
    
    if player.get("spectator", False) and msg not in ("/chat", "/emoji", "/spectate", "/auto", "/map"):
        await ws.send(json.dumps({"type": "log", "msg": "[提示] 观战模式不能执行游戏操作"}, ensure_ascii=False))
        return
    
    if msg.startswith("/chat "):
        chat_content = msg[6:].strip()
        if chat_content:
            await broadcast_room_chat(room, name, chat_content)
        return
    
    if msg.startswith("/emoji "):
        emoji_num = msg[7:].strip()
        if emoji_num.isdigit() and 1 <= int(emoji_num) <= 19:
            await broadcast_room_chat(room, name, f"[表情] {emoji_num}")
        return
    
    if msg.startswith("/spectate "):
        chat_content = msg[10:].strip()
        if chat_content:
            await broadcast_room_chat(room, name, f"[观战] {chat_content}")
        return
    
    if msg == "/auto" or msg == "auto":
        if not room.started:
            await ws.send(json.dumps({"type": "log", "msg": "[提示] 游戏开始后才能设置托管"}, ensure_ascii=False))
            return
        if player.get("spectator", False):
            await ws.send(json.dumps({"type": "log", "msg": "[提示] 观战者不能设置托管"}, ensure_ascii=False))
            return
        
        player["auto_turn"] = not player.get("auto_turn", False)
        status = "开启" if player["auto_turn"] else "关闭"
        await broadcast_to_room(room, f"[系统] {name} {status}了托管模式")
        
        if player["auto_turn"] and room.started:
            current_name = room.game_state["turn_order"][room.game_state["current_turn"]]
            if current_name == name and not player.get("waiting_buy") and not player.get("waiting_upgrade") and not player.get("waiting_card"):
                await auto_roll_and_move(room, name)
        return
    
    if msg.startswith("/kick "):
        if not room.started and name == room.owner:
            target_name = msg[6:].strip()
            if target_name in room.game_state["players"]:
                if target_name == name:
                    await ws.send(json.dumps({"type": "log", "msg": "[提示] 不能踢出自己"}, ensure_ascii=False))
                else:
                    target_ws = room.players.get(target_name)
                    if target_ws:
                        try:
                            await target_ws.send(json.dumps({"type": "log", "msg": "[系统] 你已被房主踢出房间"}, ensure_ascii=False))
                            await target_ws.close()
                        except:
                            pass

                    if target_name in room.players:
                        del room.players[target_name]
                    if target_name in room.game_state["players"]:
                        del room.game_state["players"][target_name]

                    await broadcast_to_room(room, f"[系统] {target_name} 被房主 {name} 踢出房间")
                    await broadcast_room_state(room)
            else:
                await ws.send(json.dumps({"type": "log", "msg": f"[提示] 玩家 {target_name} 不存在"}, ensure_ascii=False))
        elif room.started:
            await ws.send(json.dumps({"type": "log", "msg": "[提示] 游戏开始后不能踢人"}, ensure_ascii=False))
        else:
            await ws.send(json.dumps({"type": "log", "msg": "[提示] 只有房主可以踢人"}, ensure_ascii=False))
        return
    
    if msg == "/players":
        players_list = list(room.game_state["players"].keys())
        await ws.send(json.dumps({"type": "players", "players": players_list, "owner": room.owner}, ensure_ascii=False))
        return

    if msg.startswith("/map "):
        if not room.started and name == room.owner:
            map_name = msg[5:].strip()
            if map_name in ALL_MAPS:
                room.game_state["current_map"] = map_name
                await broadcast_to_room(room, f"[系统] 房主将地图切换为【{map_name}】")
                await broadcast_room_state(room)
            else:
                await ws.send(json.dumps({"type": "log", "msg": f"[提示] 地图 {map_name} 不存在"}, ensure_ascii=False))
        elif name != room.owner:
            await ws.send(json.dumps({"type": "log", "msg": "[提示] 只有房主可以切换地图"}, ensure_ascii=False))
        else:
            await ws.send(json.dumps({"type": "log", "msg": "[提示] 游戏进行中不能切换地图"}, ensure_ascii=False))
        return
    
    if msg == "start":
        if not room.started and name == room.owner:
            # 清理空房间 - 只统计还在线的玩家
            real_players = [n for n, p in room.game_state["players"].items() 
                           if not p.get("spectator", False) 
                           and not p.get("bankrupt", False)
                           and n in room.players]  # 只统计还在 WebSocket 连接中的玩家
            if len(real_players) >= 2:
                room.started = True
                room.game_state["turn_order"] = real_players
                room.game_state["current_turn"] = 0
                room.game_state["current_round"] = 0  # 添加回合计数器
                start_money = get_start_money(room.game_state["current_map"])
                for n in room.game_state["turn_order"]:
                    if n in room.game_state["players"]:
                        room.game_state["players"][n].update({
                            "money": start_money,
                            "position": 0,
                            "properties": [],
                            "jail_turns": 0,
                            "bankrupt": False,
                            "disconnected": False,
                            "spectator": False,
                            "auto_turn": False,
                            "skip_turn_until": None,  # 免费停车场冻结回合数
                            "waiting_buy": None,
                            "waiting_upgrade": None,
                            "waiting_card": None,
                            "waiting_card_type": None,
                            "waiting_buy_cell": None,
                            "waiting_upgrade_cell": None,
                            "waiting_buy_tour_pos": None,
                            "waiting_upgrade_tour_pos": None,
                        })
                room.game_state["properties"] = {}
                room.game_state["prop_levels"] = {}
                room.game_state["tour_mode"] = {}
                
                await broadcast_to_room(room, f"[游戏] 游戏开始！每人初始资金{start_money}元")
                first = room.game_state["turn_order"][0]
                await broadcast_to_room(room, f"[回合] 首先由 {first} 开始")
                await broadcast_room_state(room)
                first_ws = get_ws_by_name(room, first)
                if first_ws:
                    await first_ws.send(json.dumps({"type": "your_turn", "msg": "轮到你了！请掷骰子"}, ensure_ascii=False))
            else:
                await ws.send(json.dumps({"type": "log", "msg": "[提示] 至少需要2名真实玩家才能开始（观战者不计入）"}, ensure_ascii=False))
        elif name != room.owner:
            await ws.send(json.dumps({"type": "log", "msg": "[提示] 只有房主可以开始游戏"}, ensure_ascii=False))
        else:
            await ws.send(json.dumps({"type": "log", "msg": "[提示] 游戏已开始"}, ensure_ascii=False))
        return
    
    if not room.started:
        return
    
    if msg == "move_complete":
        pending = player.get("pending_roll")
        if pending:
            dice = pending["dice"]
            old_pos = pending["old_pos"]
            new_pos = pending["new_pos"]
            total_cells = pending["total_cells"]
            passed_start = pending["passed_start"]
            player["pending_roll"] = None
            await process_move(room, name, dice, old_pos, new_pos, passed_start)
        return

    if msg == "confirm_card" and player.get("waiting_card") is not None:
        card = player["waiting_card"]
        card_type = player["waiting_card_type"]
        player["waiting_card"] = None
        player["waiting_card_type"] = None
        await handle_card_effect(room, name, card, card_type)
        return
    
    if player.get("waiting_buy") is not None:
        prop_id = player["waiting_buy"]
        is_tour = data.get("is_tour", False)
        tour_pos = data.get("tour_pos")
        cell = player.get("waiting_buy_cell")
        
        if cell is None:
            if is_tour and tour_pos is not None:
                cell = TOURIST_ROUTE[tour_pos]
            else:
                current_map = get_map_data(room.game_state["current_map"])
                cell = current_map[data.get("cell", 0)]
        
        if msg == "buy":
            if player["money"] >= cell["price"]:
                player["money"] -= cell["price"]
                room.game_state["properties"][prop_id] = name
                room.game_state["prop_levels"][prop_id] = 0
                if cell["id"] not in player.get("properties", []):
                    player["properties"].append(cell["id"])
                await broadcast_to_room(room, f"[购买] {name} 购买了 {cell['name']}，花费{cell['price']}元")
            else:
                await ws.send(json.dumps({"type": "log", "msg": f"[提示] {name} 资金不足，无法购买 {cell['name']}"}, ensure_ascii=False))
        else:
            await ws.send(json.dumps({"type": "log", "msg": f"[提示] {name} 放弃购买 {cell['name']}"}, ensure_ascii=False))
        
        player["waiting_buy"] = None
        player["waiting_buy_cell"] = None
        await broadcast_room_state(room)
        
        if is_tour and tour_pos is not None:
            await next_turn(room)
        else:
            await next_turn(room)
        return
    
    if player.get("waiting_upgrade") is not None:
        prop_id = player["waiting_upgrade"]
        is_tour = data.get("is_tour", False)
        tour_pos = data.get("tour_pos")
        cell = player.get("waiting_upgrade_cell")
        
        if cell is None:
            if is_tour and tour_pos is not None:
                cell = TOURIST_ROUTE[tour_pos]
            else:
                current_map = get_map_data(room.game_state["current_map"])
                cell = current_map[data.get("cell", 0)]
        
        level = room.game_state["prop_levels"].get(prop_id, 0)
        upgrade_cost = cell.get("build_cost", cell["price"] * 0.5)
        
        if msg == "upgrade":
            if player["money"] >= upgrade_cost:
                player["money"] -= upgrade_cost
                room.game_state["prop_levels"][prop_id] = level + 1
                await broadcast_to_room(room, f"[升级] {name} 将{cell['name']}升级为{get_level_name(level + 1)}")
            else:
                await ws.send(json.dumps({"type": "log", "msg": f"[提示] {name} 资金不足，无法升级"}, ensure_ascii=False))
        else:
            await ws.send(json.dumps({"type": "log", "msg": f"[提示] {name} 放弃升级 {cell['name']}"}, ensure_ascii=False))
        
        player["waiting_upgrade"] = None
        player["waiting_upgrade_cell"] = None
        await broadcast_room_state(room)
        
        if is_tour and tour_pos is not None:
            await next_turn(room)
        else:
            await next_turn(room)
        return
    
    if msg == "roll":
        current_name = room.game_state["turn_order"][room.game_state["current_turn"]]
        if name != current_name:
            await ws.send(json.dumps({"type": "log", "msg": "[提示] 还没轮到你"}, ensure_ascii=False))
            return
        
        if room.game_state["tour_mode"].get(name, {}).get("active", False):
            tour_data = room.game_state["tour_mode"][name]
            dice = random.randint(1, 3)
            dice_gif = f"{dice}.gif"  # 添加这行
            new_pos = tour_data["position"] + dice
            
            await broadcast_to_room(room, f"[旅游] {name} 掷出 {dice} 步")
            
            dice_data = json.dumps({
                "type": "dice_result_public",
                "name": name,
                "dice": dice,
                "dice_gif": dice_gif,  # 添加这行
                "is_tour": True,
                "tour_old_pos": tour_data["position"],
                "tour_new_pos": new_pos
            }, ensure_ascii=False)
            for ws_client in room.players.values():
                try:
                    await ws_client.send(dice_data)
                except:
                    pass
            
            tour_data["position"] = new_pos
            await broadcast_room_state(room)
            await asyncio.sleep(0.5)
            await handle_tour_land(room, name, new_pos)
            return
        
        if player.get("jail_turns", 0) > 0:
            player["jail_turns"] -= 1
            await broadcast_to_room(room, f"[监狱] {name} 在监狱中，剩余{player['jail_turns']}回合")
            await next_turn(room)
            return
        
        dice = random.randint(1, 6)
        dice_gif = f"{dice}.gif"  # 添加这行
        current_map = get_map_data(room.game_state["current_map"])
        old_pos = player["position"]
        total_cells = len(current_map)
        new_pos = (old_pos + dice) % total_cells
        passed_start = old_pos + dice >= total_cells

        player["pending_roll"] = {
            "dice": dice,
            "old_pos": old_pos,
            "new_pos": new_pos,
            "total_cells": total_cells,
            "passed_start": passed_start
        }

        await ws.send(json.dumps({
            "type": "dice_result", 
            "dice": dice,
            "dice_gif": dice_gif,  # 添加这行
            "old_pos": old_pos,
            "new_pos": new_pos,
            "total_cells": total_cells,
            "passed_start": passed_start
        }, ensure_ascii=False))
        
        await broadcast_to_room(room, f"[骰子] {name} 掷出了 {dice} 点")
        return

# ========== WebSocket 连接处理 ==========
# ========== WebSocket 连接处理 ==========
async def handler(ws, path):
    name = None
    room = None
    
    # ========== 添加健康检查（解决 Render 部署问题）==========
    try:
        raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
    except asyncio.TimeoutError:
        try:
            await ws.send("HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 2\r\n\r\nOK")
            await ws.close()
        except:
            pass
        return
    except websockets.exceptions.ConnectionClosedOK:
        return
    except Exception:
        return
    
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        try:
            await ws.send("HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 2\r\n\r\nOK")
            await ws.close()
        except:
            pass
        return
    
    name = data.get("name", "").strip()
    avatar = data.get("avatar", "xiaotu")
    custom_avatar = data.get("custom_avatar", None)
    room_id_input = data.get("room_id", None)
    
    if not name:
        return
    
    # 查找或创建房间（注意：这里没有额外缩进）
    if room_id_input:
        try:
            room_num = int(room_id_input)
            if room_num in rooms:
                room = rooms[room_num]
            else:
                await ws.send(json.dumps({"type": "log", "msg": "[错误] 房间不存在"}, ensure_ascii=False))
                return
        except ValueError:
            await ws.send(json.dumps({"type": "log", "msg": "[错误] 房间号格式错误"}, ensure_ascii=False))
            return
    else:
        room_num = generate_room_id()
        room = Room(room_num, name)
        rooms[room_num] = room
        print(f"[房间创建] 房间号: {room_num}, 房主: {name}")
    
    # 检查玩家是否已存在（重连）
    if name in room.game_state["players"]:
        old_player = room.game_state["players"][name]
        
        if old_player.get("bankrupt", False):
            await ws.send(json.dumps({"type": "log", "msg": "[系统] 你已破产，现在以观战模式重连"}, ensure_ascii=False))
            old_player["spectator"] = True
        
        if name in room.players:
            old_ws = room.players[name]
            try:
                await old_ws.close()
            except:
                pass
        
        room.players[name] = ws
        old_player["disconnected"] = False
        if not room.started and not old_player.get("bankrupt", False):
            old_player["spectator"] = False
        
        await broadcast_to_room(room, f"[系统] {name} 重新连接")
        
        await ws.send(json.dumps({
            "type": "room_info",
            "room_id": str(room.room_id),
            "is_owner": name == room.owner
        }, ensure_ascii=False))
        
        if room.started:
            player = room.game_state["players"][name]
            if player.get("bankrupt"):
                await ws.send(json.dumps({"type": "log", "msg": "[系统] 你已破产，现在为观战模式"}, ensure_ascii=False))
            
            if not player.get("bankrupt", False) and not player.get("spectator", False):
                current_turn = room.game_state["turn_order"][room.game_state["current_turn"]]
                if current_turn == name:
                    await ws.send(json.dumps({"type": "your_turn", "msg": "轮到你了！请掷骰子"}, ensure_ascii=False))
        
        await broadcast_room_state(room)
    else:
        # 新玩家加入
        if room.started:
            room.players[name] = ws
            room.game_state["players"][name] = {
                "money": 0,
                "position": 0,
                "properties": [],
                "jail_turns": 0,
                "bankrupt": True,
                "disconnected": False,
                "spectator": True,
                "avatar": avatar,
                "custom_avatar": custom_avatar,
                "auto_turn": False,
            }
            await ws.send(json.dumps({
                "type": "room_info",
                "room_id": str(room.room_id),
                "is_owner": False
            }, ensure_ascii=False))
            await ws.send(json.dumps({"type": "log", "msg": "[系统] 游戏已开始，你以观战模式加入"}, ensure_ascii=False))
            await broadcast_room_state(room)
        else:
            room.players[name] = ws
            room.game_state["players"][name] = {
                "money": 0,
                "position": 0,
                "properties": [],
                "jail_turns": 0,
                "bankrupt": False,
                "disconnected": False,
                "spectator": False,
                "avatar": avatar,
                "custom_avatar": custom_avatar,
                "auto_turn": False,
            }
            
            await ws.send(json.dumps({
                "type": "room_info",
                "room_id": str(room.room_id),
                "is_owner": name == room.owner
            }, ensure_ascii=False))
            
            real_count = len([p for p in room.game_state["players"].values() if not p.get("spectator", False)])
            await broadcast_to_room(room, f"[加入] {name} 加入了房间！当前人数：{real_count}")
            await broadcast_room_state(room)
            
            await ws.send(json.dumps({"type": "log", "msg": f"[提示] 房间号：{room.room_id}，房主：{room.owner}"}, ensure_ascii=False))
            if name == room.owner:
                await ws.send(json.dumps({"type": "log", "msg": "[提示] 你是房主，可使用 /map [地图名] 切换地图，2人以上可 start 开始游戏"}, ensure_ascii=False))
            await ws.send(json.dumps({"type": "log", "msg": "[提示] 输入 /auto 可开启/关闭托管模式（托管会自动掷骰子、自动购买/升级）"}, ensure_ascii=False))
    
    if name == room.owner:
        players_list = list(room.game_state["players"].keys())
        await ws.send(json.dumps({"type": "players", "players": players_list, "owner": room.owner}, ensure_ascii=False))
    
    try:
        async for raw_msg in ws:
            try:
                data = json.loads(raw_msg)
                await handle_message(room, ws, name, data)
            except Exception as e:
                print(f"[消息错误] {e}")
    except websockets.exceptions.ConnectionClosed:
        print(f"[断开] {name} 连接断开")
    except Exception as e:
        print(f"[连接错误] {e}")
    finally:
        if room and name:
            if name in room.players:
                del room.players[name]
            if name in room.game_state["players"]:
                if not room.game_state["players"][name].get("spectator", False) and not room.game_state["players"][name].get("bankrupt", False):
                    room.game_state["players"][name]["disconnected"] = True
                    room.game_state["players"][name]["auto_turn"] = True
                    await broadcast_to_room(room, f"[系统] {name} 退出房间，已开启托管")
                    if room.started:
                        current_name = room.game_state["turn_order"][room.game_state["current_turn"]]
                        if current_name == name:
                            await auto_roll_and_move(room, name)
                elif room.game_state["players"][name].get("bankrupt", False):
                    room.game_state["players"][name]["disconnected"] = True
                    await broadcast_to_room(room, f"[系统] {name} 退出房间（已破产）")
                else:
                    room.game_state["players"][name]["disconnected"] = True
                    await broadcast_to_room(room, f"[系统] {name} 退出房间")
            
            # 清理空房间
            real_players = [n for n, p in room.game_state["players"].items() if not p.get("spectator", False) and not p.get("bankrupt", False)]
            if len(real_players) == 0:
                if room.room_id in rooms:
                    del rooms[room.room_id]
                    recycle_room_id(room.room_id)
                    print(f"[房间删除] 房间号 {room.room_id} 已删除，当前房间数: {len(rooms)}")
            elif room.owner == name and len(real_players) > 0 and room.owner not in real_players:
                new_owner = real_players[0]
                room.owner = new_owner
                await broadcast_to_room(room, f"[系统] 房主变更为 {new_owner}")
            
            await broadcast_room_state(room)

# ========== 健康检查 HTTP 服务器（为 Render 部署添加）==========
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/healthz':
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'OK')
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        pass  # 禁用日志输出

def start_health_server():
    httpd = HTTPServer(('0.0.0.0', 10000), HealthHandler)
    httpd.serve_forever()
    
async def main():
    # 启动健康检查 HTTP 服务器（Render 需要）
    threading.Thread(target=start_health_server, daemon=True).start()
    
    print("=" * 50)
    print("  🎲 大富翁 WebSocket 服务器启动！")
    print("  WebSocket 端口: 10000")
    print("  健康检查端口: 8080")
    print("=" * 50)
    # 启动定时清理任务
    asyncio.create_task(clean_empty_rooms())
    async with websockets.serve(handler, "0.0.0.0", 10000):
        print("服务器运行在 ws://0.0.0.0:10000")
        print("按 Ctrl+C 停止服务器")
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            print("\n服务器正在关闭...")
            print("服务器已停止")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n收到退出信号，服务器已停止")
