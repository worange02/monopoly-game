import socket
import threading
import tkinter as tk
from tkinter import simpledialog, messagebox
import re

# ========== 明亮卡通风配色 ==========
BG_MAIN    = "#FFF9F0"
BG_BOARD   = "#FFFBF5"
BG_PANEL   = "#FFFFFF"
BG_LOG     = "#FFF3E0"

ACCENT1    = "#FF6B6B"   # 红
ACCENT2    = "#4ECDC4"   # 青
ACCENT3    = "#FFE66D"   # 黄
ACCENT4    = "#A8E6CF"   # 绿
ACCENT5    = "#FF8B94"   # 粉

TEXT_DARK  = "#2D3436"
TEXT_MID   = "#636E72"
TEXT_LIGHT = "#B2BEC3"

CELL_COLORS = {
    "start":    "#6BCB77",
    "property": "#4D96FF",
    "chance":   "#FFD93D",
    "tax":      "#FF6B6B",
    "jail":     "#C77DFF",
    "station":  "#4ECDC4"
}

PLAYER_COLORS  = ["#FF6B6B", "#4ECDC4", "#FFD93D", "#A8E6CF"]
PLAYER_EMOJIS  = ["🔴", "🔵", "🟡", "🟢"]

MAP_LAYOUT = [
    (4,4),(3,4),(2,4),(1,4),(0,4),
    (0,3),(0,2),(0,1),(0,0),
    (1,0),(2,0),(3,0),(4,0),
    (4,1),(4,2),(4,3),
    (3,3),(2,3),(1,3),(1,2)
]

CELL_NAMES = [
    "起点","阳光大道","机会","朝阳路","税务局","火车站",
    "长安街","机会","建国路","监狱","复兴路","机会",
    "中关村","税务局","南京路","火车站","外滩","机会",
    "淮海路","陆家嘴"
]
CELL_TYPES = [
    "start","property","chance","property","tax","station",
    "property","chance","property","jail","property","chance",
    "property","tax","property","station","property","chance",
    "property","property"
]
CELL_PRICES = [0,100,0,150,50,200,180,0,160,0,220,0,240,50,260,200,300,0,320,400]
CELL_RENTS  = [0,20,0,30,50,40,35,0,32,0,44,0,48,50,52,40,60,0,64,80]


class MonopolyClient:
    def __init__(self):
        self.sock = None
        self.name = ""
        self.player_positions = {}
        self.player_colors    = {}
        self.player_money     = {}
        self.player_bankrupt  = {}
        self.cell_owners      = {}   # {cell_id: player_name}
        self.cell_levels = {}  # {cell_id: 1/2/3}

        self.root = tk.Tk()
        self.root.title("🎲 大富翁 · 多人联机版")
        self.root.configure(bg=BG_MAIN)
        self.root.resizable(True, True)

        self.build_ui()
        self.connect_to_server()
        self.root.mainloop()

    # ==================== UI构建 ====================
    def build_ui(self):
        # 顶部标题栏
        title_bar = tk.Frame(self.root, bg=ACCENT1, height=52)
        title_bar.pack(fill=tk.X)
        title_bar.pack_propagate(False)
        tk.Label(title_bar, text="🎲  大 富 翁  多 人 联 机 版",
                 font=("Microsoft YaHei", 16, "bold"),
                 bg=ACCENT1, fg="white").pack(side=tk.LEFT, padx=20)
        self.status_label = tk.Label(title_bar, text="连接中...",
                 font=("Microsoft YaHei", 10),
                 bg=ACCENT1, fg="#FFE9E9")
        self.status_label.pack(side=tk.RIGHT, padx=20)

        # 主体区域
        body = tk.Frame(self.root, bg=BG_MAIN)
        body.pack(fill=tk.BOTH, expand=True, padx=12, pady=10)

        # 左：棋盘
        board_frame = tk.Frame(body, bg=BG_MAIN)
        board_frame.pack(side=tk.LEFT, anchor="n")

        tk.Label(board_frame, text="🗺  游戏棋盘",
                 font=("Microsoft YaHei", 11, "bold"),
                 bg=BG_MAIN, fg=TEXT_DARK).pack(anchor="w", pady=(0,4))

        board_bg = tk.Frame(board_frame, bg="#E8F4FD", bd=0,
                            highlightbackground="#BDE0FE",
                            highlightthickness=2)
        board_bg.pack()

        self.canvas = tk.Canvas(board_bg, width=500, height=500,
                                bg="#E8F4FD", highlightthickness=0)
        self.canvas.pack(padx=6, pady=6)
        self.draw_board()

        # 中：资产面板 + 日志 + 按钮
        right = tk.Frame(body, bg=BG_MAIN)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(12,0))

        # 玩家资产面板
        tk.Label(right, text="👥  玩家资产",
                 font=("Microsoft YaHei", 11, "bold"),
                 bg=BG_MAIN, fg=TEXT_DARK).pack(anchor="w")

        self.player_panel = tk.Frame(right, bg=BG_PANEL, bd=0,
                                     highlightbackground="#E0E0E0",
                                     highlightthickness=1)
        self.player_panel.pack(fill=tk.X, pady=(4,8))
        self.player_labels = {}
        self.update_player_panel()

        # 游戏日志
        tk.Label(right, text="📋  游戏日志",
                 font=("Microsoft YaHei", 11, "bold"),
                 bg=BG_MAIN, fg=TEXT_DARK).pack(anchor="w")

        log_frame = tk.Frame(right, bg=BG_LOG, bd=0,
                             highlightbackground="#FFD9A0",
                             highlightthickness=1)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(4,8))

        self.log_text = tk.Text(log_frame, width=36, height=12,
                                bg=BG_LOG, fg=TEXT_DARK,
                                font=("Microsoft YaHei", 9),
                                state=tk.DISABLED, wrap=tk.WORD,
                                relief=tk.FLAT, padx=8, pady=6,
                                insertbackground=TEXT_DARK)
        scrollbar = tk.Scrollbar(log_frame, command=self.log_text.yview,
                                 bg=BG_LOG, troughcolor=BG_LOG)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # 颜色标签
        self.log_text.tag_config("system",  foreground="#0984E3", font=("Microsoft YaHei", 9, "bold"))
        self.log_text.tag_config("move",    foreground="#00B894")
        self.log_text.tag_config("buy",     foreground="#6C5CE7")
        self.log_text.tag_config("rent",    foreground="#E17055")
        self.log_text.tag_config("chance",  foreground="#FDCB6E")
        self.log_text.tag_config("tax",     foreground="#D63031")
        self.log_text.tag_config("end",     foreground=ACCENT1, font=("Microsoft YaHei", 9, "bold"))
        self.log_text.tag_config("normal",  foreground=TEXT_DARK)

        # 按钮区域
        btn_outer = tk.Frame(right, bg=BG_PANEL, bd=0,
                             highlightbackground="#E0E0E0",
                             highlightthickness=1)
        btn_outer.pack(fill=tk.X)

        btn_grid = tk.Frame(btn_outer, bg=BG_PANEL)
        btn_grid.pack(padx=8, pady=8)

        def make_btn(parent, text, cmd, color, row, col, colspan=1):
            btn = tk.Button(parent, text=text, command=cmd,
                            bg=color, fg="white",
                            font=("Microsoft YaHei", 10, "bold"),
                            relief=tk.FLAT, cursor="hand2",
                            activebackground=color, activeforeground="white",
                            padx=10, pady=7)
            btn.grid(row=row, column=col, columnspan=colspan,
                     padx=4, pady=3, sticky="ew")
            return btn

        self.roll_btn   = make_btn(btn_grid, "🎲 掷骰子", self.roll_dice,   ACCENT1, 0, 0)
        self.buy_btn    = make_btn(btn_grid, "🏠 购买",   self.buy_property, "#6BCB77", 0, 1)
        self.skip_btn   = make_btn(btn_grid, "⏭ 跳过",   self.skip_buy,     TEXT_MID,  1, 0)
        self.status_btn = make_btn(btn_grid, "📊 状态",   self.check_status, ACCENT2,   1, 1)
        self.start_btn  = make_btn(btn_grid, "▶  开始游戏", self.start_game, "#C77DFF", 2, 0, 2)

        btn_grid.columnconfigure(0, weight=1)
        btn_grid.columnconfigure(1, weight=1)

    # ==================== 棋盘绘制 ====================
    def animate_move(self, pname, old_pos, new_pos, step_delay=120):
        total = len(MAP_LAYOUT)
        # 计算需要走的步数
        if new_pos >= old_pos:
            steps = list(range(old_pos + 1, new_pos + 1))
        else:
            steps = list(range(old_pos + 1, total)) + list(range(0, new_pos + 1))

        def step(i):
            if i >= len(steps):
                self.player_positions[pname] = new_pos
                self.draw_board()
                return
            self.player_positions[pname] = steps[i]
            self.draw_board()
            self.root.after(step_delay, lambda: step(i + 1))

        step(0)
    def draw_board(self):
        self.canvas.delete("all")
        CELL = 90
        PAD  = 10

        for i, (col, row) in enumerate(MAP_LAYOUT):
            x1 = PAD + col * CELL
            y1 = PAD + row * CELL
            x2 = x1 + CELL - 3
            y2 = y1 + CELL - 3

            ctype = CELL_TYPES[i]
            base_color = CELL_COLORS.get(ctype, "#4D96FF")

            # 阴影
            self.canvas.create_rectangle(x1+3, y1+3, x2+3, y2+3,
                                         fill="#D0D0D0", outline="")
            # 格子主体
            self.canvas.create_rectangle(x1, y1, x2, y2,
                                         fill=base_color, outline="white", width=2)

            # 格子编号
            self.canvas.create_text(x1+7, y1+7, text=str(i),
                                    fill="white", font=("Microsoft YaHei", 7),
                                    anchor="nw")

            # 格子名
            cname = CELL_NAMES[i]
            self.canvas.create_text(x1+CELL//2-2, y1+28,
                                    text=cname, fill="white",
                                    font=("Microsoft YaHei", 8, "bold"),
                                    width=CELL-10)

            # 地产信息：显示价格/租金/归属
            if ctype in ("property", "station"):
                owner = self.cell_owners.get(i)
                if owner:
                    o_color = self.player_colors.get(owner, "#888888")
                    level = self.cell_levels.get(i, 1)
                    level_icons = {1: "🛖", 2: "🏠", 3: "🏰"}
                    level_names = {1: "茅房", 2: "房子", 3: "别墅"}
                    rent_mult = {1: 1, 2: 2, 3: 4}[level]
                    rent = CELL_RENTS[i] * rent_mult

                    self.canvas.create_rectangle(x1 + 4, y2 - 22, x2 - 4, y2 - 4,
                                                 fill=o_color, outline="")
                    short = owner[:2] if len(owner) > 2 else owner
                    self.canvas.create_text(x1 + CELL // 2 - 2, y2 - 13,
                                            text=f"{level_icons[level]}{short} ¥{CELL_PRICES[i]}→租{rent}",
                                            fill="white",
                                            font=("Microsoft YaHei", 6, "bold"))
                else:
                    # 未购买：显示价格
                    self.canvas.create_rectangle(x1+4, y2-20, x2-4, y2-4,
                                                 fill="white", outline="")
                    price = CELL_PRICES[i]
                    self.canvas.create_text(x1+CELL//2-2, y2-12,
                                            text=f"¥{price}",
                                            fill=base_color,
                                            font=("Microsoft YaHei", 7, "bold"))

        # 棋子
        for idx, (pname, pos) in enumerate(self.player_positions.items()):
            if pos < len(MAP_LAYOUT):
                col, row = MAP_LAYOUT[pos]
                offset_x = (idx % 2) * 28
                offset_y = (idx // 2) * 28
                cx = PAD + col * CELL + 18 + offset_x
                cy = PAD + row * CELL + 38 + offset_y
                color = self.player_colors.get(pname, PLAYER_COLORS[idx % 4])
                # 棋子阴影
                self.canvas.create_oval(cx+2, cy+2, cx+26, cy+26,
                                        fill="#CCCCCC", outline="")
                # 棋子主体
                self.canvas.create_oval(cx, cy, cx+24, cy+24,
                                        fill=color, outline="white", width=2)
                # 玩家首字
                self.canvas.create_text(cx+12, cy+12,
                                        text=pname[0].upper(),
                                        fill="white",
                                        font=("Microsoft YaHei", 9, "bold"))

    # ==================== 玩家资产面板 ====================
    def update_player_panel(self):
        for w in self.player_panel.winfo_children():
            w.destroy()

        if not self.player_colors:
            tk.Label(self.player_panel, text="等待玩家加入...",
                     font=("Microsoft YaHei", 9),
                     bg=BG_PANEL, fg=TEXT_LIGHT).pack(padx=10, pady=6)
            return

        for idx, pname in enumerate(self.player_colors):
            color  = self.player_colors[pname]
            money  = self.player_money.get(pname, 2000)
            bankrupt = self.player_bankrupt.get(pname, False)
            emoji  = PLAYER_EMOJIS[idx % 4]

            row_bg = "#FFF0F0" if bankrupt else BG_PANEL
            row = tk.Frame(self.player_panel, bg=row_bg)
            row.pack(fill=tk.X, padx=6, pady=2)

            # 颜色块
            tk.Frame(row, bg=color, width=6).pack(side=tk.LEFT, fill=tk.Y, padx=(0,6))

            # 名字
            name_str = f"{emoji} {pname}"
            if bankrupt:
                name_str += " 💀"
            tk.Label(row, text=name_str,
                     font=("Microsoft YaHei", 9, "bold"),
                     bg=row_bg, fg=TEXT_DARK, width=10, anchor="w").pack(side=tk.LEFT)

            # 资金
            money_color = "#D63031" if money < 300 else "#00B894"
            tk.Label(row, text=f"💰 {money}元",
                     font=("Microsoft YaHei", 9),
                     bg=row_bg, fg=money_color).pack(side=tk.LEFT, padx=8)

            # 地产数量
            prop_count = sum(1 for owner in self.cell_owners.values() if owner == pname)
            tk.Label(row, text=f"🏠 {prop_count}处",
                     font=("Microsoft YaHei", 9),
                     bg=row_bg, fg=TEXT_MID).pack(side=tk.LEFT)

    # ==================== 日志输出 ====================
    def log(self, msg):
        self.log_text.config(state=tk.NORMAL)

        tag = "normal"
        if any(k in msg for k in ["[游戏]","[加入]","[回合]","[连接]","[当前玩家]","[操作]","[提示]"]):
            tag = "system"
        elif "[移动]" in msg:
            tag = "move"
        elif "[购买]" in msg:
            tag = "buy"
        elif "[租金]" in msg:
            tag = "rent"
        elif "[机会]" in msg:
            tag = "chance"
        elif "[税务]" in msg or "[起点]" in msg:
            tag = "tax"
        elif "[游戏结束]" in msg or "[结算]" in msg:
            tag = "end"

        self.log_text.insert(tk.END, msg + "\n", tag)
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    # ==================== 服务端消息处理 ====================
    def connect_to_server(self):
        name = simpledialog.askstring("加入游戏", "请输入你的名字：", parent=self.root)
        if not name:
            self.root.destroy()
            return
        self.name = name

        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.connect(("127.0.0.1", 9999))
            self.sock.recv(1024)
            self.sock.sendall((name + "\n").encode("utf-8"))
            self.status_label.config(text=f"玩家：{name}")
            self.log(f"✅ 已连接到服务器！")
            threading.Thread(target=self.receive_loop, daemon=True).start()
        except Exception as e:
            messagebox.showerror("连接失败", f"无法连接服务器：{e}")
            self.root.destroy()

    def receive_loop(self):
        buffer = ""
        while True:
            try:
                data = self.sock.recv(4096).decode("utf-8")
                if not data:
                    break
                buffer += data
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    if line.strip():
                        self.root.after(0, self.handle_server_msg, line.strip())
            except:
                break

    def handle_server_msg(self, msg):
        self.log(msg)

        # 当前玩家列表（新玩家加入时同步）
        if "[当前玩家]" in msg:
            names = msg.replace("[当前玩家]", "").strip().split(",")
            self.player_colors.clear()
            self.player_positions.clear()
            for i, pname in enumerate(names):
                pname = pname.strip()
                if pname:
                    self.player_colors[pname]   = PLAYER_COLORS[i % 4]
                    self.player_positions[pname] = 0
                    self.player_money[pname]     = 2000
            self.draw_board()
            self.update_player_panel()
            return

        # 新玩家加入
        if "[加入]" in msg:
            pname = msg.replace("[加入]", "").strip().split(" ")[0]
            if pname not in self.player_colors:
                idx = len(self.player_colors)
                self.player_colors[pname]   = PLAYER_COLORS[idx % 4]
                self.player_positions[pname] = 0
                self.player_money[pname]     = 2000
            self.draw_board()
            self.update_player_panel()

        # 玩家移动
        if "[移动]" in msg:
            m_pos = re.search(r"格子(\d+)", msg)
            if m_pos:
                new_pos = int(m_pos.group(1))
                for pname in self.player_colors:
                    if msg.startswith(f"[移动] {pname} "):
                        old_pos = self.player_positions.get(pname, 0)
                        self.animate_move(pname, old_pos, new_pos)
                        break
            self.draw_board()

        # 购买地产：解析格子编号更新归属
        if "[购买]" in msg and "购买了" in msg:
            m = re.search(r"(\S+) 购买了", msg)
            if m:
                buyer = m.group(1)
                for i, cname in enumerate(CELL_NAMES):
                    if cname in msg and CELL_TYPES[i] in ("property", "station"):
                        self.cell_owners[i] = buyer
                        break
            self.draw_board()
            self.update_player_panel()

        if "[升级]" in msg and "升级为" in msg:
            for i, cname in enumerate(CELL_NAMES):
                if cname in msg and CELL_TYPES[i] in ("property", "station"):
                    current = self.cell_levels.get(i, 1)
                    self.cell_levels[i] = min(current + 1, 3)
                    break
            self.draw_board()
            self.update_player_panel()

        # 更新资金（从状态消息解析）
        if "===== 当前状态 =====" in msg or "元 |" in msg:
            m = re.search(r"(\S+)：(\d+)元", msg)
            if m:
                pname, money = m.group(1), int(m.group(2))
                if pname in self.player_money:
                    self.player_money[pname] = money
                    self.update_player_panel()

        # 破产
        if "[破产]" in msg:
            for pname in self.player_colors:
                if pname in msg:
                    self.player_bankrupt[pname] = True
            self.update_player_panel()

        # 状态栏更新
        if "[回合]" in msg:
            if self.name in msg:
                self.status_label.config(text=f"⚡ 轮到你了！")
            else:
                other = msg.replace("[回合]", "").replace("现在轮到", "").replace("的回合", "").strip()
                self.status_label.config(text=f"⏳ 等待 {other}...")

        if "[游戏结束]" in msg:
            self.status_label.config(text="🎉 游戏结束！")

    # ==================== 按钮操作 ====================
    def send(self, msg):
        if self.sock:
            try:
                self.sock.sendall((msg + "\n").encode("utf-8"))
            except:
                self.log("❌ 发送失败，连接已断开")

    def roll_dice(self):    self.send("roll")
    def buy_property(self): self.send("buy")
    def skip_buy(self):     self.send("skip")
    def check_status(self): self.send("status")
    def start_game(self):   self.send("start")


if __name__ == "__main__":
    MonopolyClient()