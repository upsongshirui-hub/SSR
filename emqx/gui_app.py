import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import tkinter.font as tkFont
import matplotlib
matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
import json
import time
import random
import threading
import queue
from datetime import datetime
from collections import deque
from concurrent.futures import ThreadPoolExecutor

from config import (
    WELL_ID, SIMULATION_INTERVAL, EQUIPMENTS, DEFAULT_MQTT_CONFIG,
    MYSQL_CONFIG, SENSOR_CONFIGS, COLORS, NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD
)
from database import MySQLClient, Neo4jClient
from ai_service import DeepSeekClient
from mqtt_manager import BrokerClient


class WellMonitorApp:
    def __init__(self, root):
        self.root = root
        self.root.title(f"智能钻井指挥中心 - {WELL_ID} (多设备集群独立模拟版)")
        self.root.geometry("1400x900")
        self.is_running = True
        self.colors = COLORS
        self.root.configure(bg=self.colors["bg_dark"])

        # 🌐 全局中文字体适配
        self._setup_chinese_fonts()

        # ── 网络 & 仿真状态 ──────────────────────────────
        self.brokers = []
        self.is_simulating = False
        self.sim_thread = None

        # ── 发送端预览标签 ───────────────────────────────
        self.sender_preview_labels = {}

        # ── 手动参数输入变量（全8个传感器）────────────────
        self.manual_params = {
            "WOB":  tk.DoubleVar(value=20.0), "ROP":  tk.DoubleVar(value=12.0),
            "RPM":  tk.DoubleVar(value=100.0), "TQ":   tk.DoubleVar(value=20.0),
            "FLOW": tk.DoubleVar(value=30.0), "TEMP": tk.DoubleVar(value=60.0),
            "GAS":  tk.DoubleVar(value=0.2), "PWH":  tk.DoubleVar(value=13.0),
        }

        # ── 设备选择 ─────────────────────────────────────
        self.current_sender_eq   = tk.StringVar(value=EQUIPMENTS[0])
        self.current_receiver_eq = tk.StringVar(value=EQUIPMENTS[0])

        # ── 数据缓存 ─────────────────────────────────────
        self.latest_sender_data_by_eq = {
            eq: {code: "--" for code in SENSOR_CONFIGS} for eq in EQUIPMENTS
        }
        self.history_data = {
            eq: {code: deque(maxlen=50) for code in SENSOR_CONFIGS}
            for eq in EQUIPMENTS
        }
        self.history_time = {eq: deque(maxlen=50) for eq in EQUIPMENTS}

        # ── 模拟状态（每台设备独立随机初始值）────────────
        self.sim_states = {
            eq: {
                code: random.uniform(cfg["default_min"], cfg["default_max"])
                for code, cfg in SENSOR_CONFIGS.items()
            }
            for eq in EQUIPMENTS
        }

        self.chart_selection_vars = {}

        # ── MySQL & 异步落库队列 ──────────────────────────
        self.mysql_client = MySQLClient(MYSQL_CONFIG)
        self.mysql_insert_queue = queue.Queue()
        self._start_mysql_worker()

        # ── AI 相关 ───────────────────────────────────────
        self.ai_ui_queue  = queue.Queue()
        self.ai_executor  = ThreadPoolExecutor(max_workers=8)
        self.neo4j_client = Neo4jClient(NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD)
        self.ai_page_started = False

        # ⚡ 低延迟巡检核心配置
        self._diagnosis_cooldown = 2.0  # 单设备诊断冷却时间(秒)，防API限流且保证实时性
        self._last_diagnosis_time = {eq: 0.0 for eq in EQUIPMENTS}
        self._diagnosis_lock = threading.Lock()

        # ── 按钮引用（build 后才赋值）────────────────────
        self.btn_sim        = None
        self.btn_manual_sim = None

        # ── ai_status_var 提前初始化
        self.ai_status_var = tk.StringVar(
            value="AI诊断页就绪，等待数据自动诊断...（数据源：内存实时快照）"
        )

        self._setup_styles()
        self._build_ui()
        self.root.after(500, self._connect_default_broker)
        self.root.after(100, self._process_ai_ui_queue)

    # =========================================================
    # 🌐 全局中文字体配置
    # =========================================================
    def _setup_chinese_fonts(self):
        font_candidates = ["Microsoft YaHei", "SimHei", "PingFang SC", "WenQuanYi Micro Hei", "Arial Unicode MS"]
        for font_name in font_candidates:
            try:
                tkFont.nametofont("TkDefaultFont").configure(family=font_name, size=10)
                tkFont.nametofont("TkTextFont").configure(family=font_name, size=10)
                tkFont.nametofont("TkFixedFont").configure(family="Consolas", size=10)
                print(f"✅ 已应用中文字体: {font_name}")
                return
            except tk.TclError:
                continue
        print("⚠️ 未找到可用中文字体，将使用系统默认字体")

    # =========================================================
    # MySQL 异步写入线程
    # =========================================================
    def _start_mysql_worker(self):
        def worker():
            batch = []
            while self.is_running:
                try:
                    record = self.mysql_insert_queue.get(timeout=1.5)
                    batch.append(record)
                    if len(batch) >= 40:
                        self.mysql_client.insert_batch(batch)
                        batch = []
                except queue.Empty:
                    if batch:
                        self.mysql_client.insert_batch(batch)
                        batch = []
        threading.Thread(target=worker, daemon=True).start()

    # =========================================================
    # 样式
    # =========================================================
    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use('clam')
        C = self.colors
        style.configure("TFrame",       background=C["bg_dark"])
        style.configure("Card.TFrame",  background=C["bg_card"])
        style.configure("TLabel",       background=C["bg_dark"],
                        foreground=C["text_light"], font=("微软雅黑", 10))
        style.configure("Card.TLabel",  background=C["bg_card"],
                        foreground=C["text_light"], font=("微软雅黑", 10))
        style.configure("Muted.TLabel", background=C["bg_card"],
                        foreground=C["text_muted"], font=("微软雅黑", 9))
        style.configure("Value.TLabel", background=C["bg_card"],
                        foreground=C["accent_blue"], font=("Consolas", 14, "bold"))
        style.configure("Header.TLabel",    background=C["bg_dark"],
                        foreground="white", font=("微软雅黑", 18, "bold"))
        style.configure("SubHeader.TLabel", background=C["bg_card"],
                        foreground="white", font=("微软雅黑", 11, "bold"))
        style.configure("TButton", font=("微软雅黑", 10, "bold"),
                        borderwidth=0, padding=8)
        style.map("TButton",
                  background=[("active", "#334155")],
                  foreground=[("active", "white")])
        style.configure("Primary.TButton", background=C["accent_blue"],  foreground="white")
        style.map("Primary.TButton", background=[("active", "#1d4ed8")])
        style.configure("Success.TButton", background=C["accent_green"], foreground="white")
        style.map("Success.TButton", background=[("active", "#047857")])
        style.configure("Danger.TButton",  background="#334155", foreground="#ef4444")
        style.map("Danger.TButton",  background=[("active", "#475569")])
        style.configure("TNotebook", background=C["bg_dark"], borderwidth=0)
        style.configure("TNotebook.Tab",
                        background="#334155", foreground=C["text_muted"],
                        padding=[15, 8], font=("微软雅黑", 10, "bold"))
        style.map("TNotebook.Tab",
                  background=[("selected", C["accent_blue"])],
                  foreground=[("selected", "white")])
        style.configure("TCheckbutton",
                        background=C["bg_dark"], foreground=C["text_light"],
                        font=("微软雅黑", 9))
        style.map("TCheckbutton", background=[("active", C["bg_dark"])])
        style.map('TCombobox', fieldbackground=[('readonly', C["bg_input"])])
        style.configure("TCombobox",
                        selectbackground=C["accent_blue"],
                        fieldbackground=C["bg_input"],
                        background=C["bg_card"], foreground="white")

    # =========================================================
    # 顶层 UI 骨架
    # =========================================================
    def _build_ui(self):
        header = ttk.Frame(self.root, padding=20)
        header.pack(fill="x")
        tk.Label(header, text="📡", bg=self.colors["accent_blue"], fg="white",
                 font=("Arial", 16), padx=10, pady=5).pack(side="left", padx=(0, 10))
        title_frame = ttk.Frame(header)
        title_frame.pack(side="left")
        ttk.Label(title_frame, text=f"Well-01 指挥中心 ({WELL_ID})",
                  style="Header.TLabel").pack(anchor="w")

        self.status_bar = ttk.Frame(title_frame)
        self.status_bar.pack(anchor="w", pady=(5, 0))
        self.conn_status_lbl = tk.Label(
            self.status_bar, text="🔴 离线",
            bg="#fee2e2", fg="#ef4444", padx=6, pady=2, font=("微软雅黑", 8))
        self.conn_status_lbl.pack(side="left", padx=(0, 5))
        self.node_status_lbl = tk.Label(
            self.status_bar, text="传输节点: 0",
            bg=self.colors["bg_dark"], fg=self.colors["accent_blue"],
            font=("微软雅黑", 9))
        self.node_status_lbl.pack(side="left")

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=20, pady=10)

        self.sender_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.sender_frame, text="📤 发送/模拟端")
        self._build_sender_ui()

        self.receiver_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.receiver_frame, text="📊 接收/监控端")
        self._build_receiver_ui()

        self.ai_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.ai_frame, text="🧠 AI 智能诊断")
        self._build_ai_diagnosis_ui()

        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

    # =========================================================
    # 发送端 UI
    # =========================================================
    def _build_sender_ui(self):
        container = ttk.Frame(self.sender_frame)
        container.pack(fill="both", expand=True, padx=20, pady=20)
        main_grid = ttk.Frame(container)
        main_grid.pack(fill="both", expand=True)

        left_col = ttk.Frame(main_grid, width=400)
        left_col.pack(side="left", fill="y", padx=(0, 20))

        eq_card = ttk.Frame(left_col, style="Card.TFrame", padding=15)
        eq_card.pack(fill="x", pady=(0, 15))
        ttk.Label(eq_card, text="🚜 钻探设备 (模拟端)",
                  style="SubHeader.TLabel").pack(anchor="w", pady=(0, 10))
        self.sender_eq_cb = ttk.Combobox(
            eq_card, textvariable=self.current_sender_eq,
            values=EQUIPMENTS, state="readonly", font=("微软雅黑", 10))
        self.sender_eq_cb.pack(fill="x", ipady=4)
        self.sender_eq_cb.bind("<<ComboboxSelected>>", self._on_sender_eq_change)

        net_card = ttk.Frame(left_col, style="Card.TFrame", padding=15)
        net_card.pack(fill="x", pady=(0, 15))
        ttk.Label(net_card, text="🌐 传输节点管理",
                  style="SubHeader.TLabel").pack(anchor="w", pady=(0, 10))
        btn_row = ttk.Frame(net_card, style="Card.TFrame")
        btn_row.pack(fill="x", pady=(0, 5))
        ttk.Button(btn_row, text="+ 增加", style="Success.TButton", width=8,
                   command=self._open_broker_dialog).pack(side="left", padx=(0, 5))
        ttk.Button(btn_row, text="- 删除", style="Danger.TButton", width=8,
                   command=self._remove_selected_broker).pack(side="left")
        self.broker_listbox = tk.Listbox(
            net_card, height=3,
            bg=self.colors["bg_input"], fg="white", bd=0,
            highlightthickness=1, highlightbackground=self.colors["border"],
            selectbackground=self.colors["accent_blue"], font=("Consolas", 9))
        self.broker_listbox.pack(fill="x", pady=5)
        self.active_brokers_lbl = ttk.Label(
            net_card, text="当前连接数: 0", style="Muted.TLabel")
        self.active_brokers_lbl.pack(anchor="w")

        sim_card = ttk.Frame(left_col, style="Card.TFrame", padding=15)
        sim_card.pack(fill="x", pady=15)
        ttk.Label(sim_card, text="🤖 自动生成器 (全设备独立模拟)",
                  style="SubHeader.TLabel").pack(anchor="w", pady=(0, 10))

        self.btn_sim = ttk.Button(
            sim_card,
            text=f"▶️ 启动全局发送 ({SIMULATION_INTERVAL}s/次)",
            style="Success.TButton",
            command=self._toggle_simulation)
        self.btn_sim.pack(fill="x", pady=5)

        self.btn_manual_sim = ttk.Button(
            sim_card, text="📤 手动触发单次模拟",
            style="Primary.TButton", command=self._manual_trigger_sim)
        self.btn_manual_sim.pack(fill="x", pady=5)

        self.sim_status_lbl = ttk.Label(
            sim_card, text="状态: 待机", style="Muted.TLabel")
        self.sim_status_lbl.pack(anchor="center")

        alert_card = ttk.Frame(left_col, style="Card.TFrame", padding=15)
        alert_card.pack(fill="x", pady=15)
        ttk.Label(alert_card, text="⚠️ 异常注入 (当前设备)",
                  style="SubHeader.TLabel").pack(anchor="w", pady=(0, 10))
        btn_grid = ttk.Frame(alert_card, style="Card.TFrame")
        btn_grid.pack(fill="x")
        self._create_alert_btn(btn_grid, "🔥 气体告警", "#eab308",
                               lambda: self._trigger_alert("GAS"), "left")
        self._create_alert_btn(btn_grid, "🛑 泵压告警", "#ef4444",
                               lambda: self._trigger_alert("PWH"), "right")

        right_col = ttk.Frame(main_grid)
        right_col.pack(side="left", fill="both", expand=True)

        preview_card = ttk.Frame(right_col, style="Card.TFrame", padding=15)
        preview_card.pack(fill="x", pady=(0, 15))
        self.sender_preview_title = ttk.Label(
            preview_card,
            text=f"📊 模拟数据实时看板 - {self.current_sender_eq.get()}",
            style="SubHeader.TLabel")
        self.sender_preview_title.pack(anchor="w", pady=(0, 15))

        p_grid = ttk.Frame(preview_card, style="Card.TFrame")
        p_grid.pack(fill="x")
        for i, (code, cfg) in enumerate(SENSOR_CONFIGS.items()):
            row, col = divmod(i, 4)
            cell = tk.Frame(p_grid, bg=self.colors["bg_card"], padx=5, pady=5)
            cell.grid(row=row, column=col, sticky="ew", padx=5, pady=5)
            p_grid.grid_columnconfigure(col, weight=1)
            tk.Label(cell, text=cfg["name"], bg=self.colors["bg_card"],
                     fg=self.colors["text_muted"], font=("微软雅黑", 9)).pack(anchor="w")
            val_lbl = ttk.Label(cell, text="--", style="Value.TLabel")
            val_lbl.pack(anchor="w")
            self.sender_preview_labels[code] = val_lbl
            tk.Label(cell, text=cfg["unit"], bg=self.colors["bg_card"],
                     fg=self.colors["text_muted"], font=("Arial", 8)).pack(anchor="e")

        man_card = ttk.Frame(right_col, style="Card.TFrame", padding=15)
        man_card.pack(fill="x", pady=15)
        ttk.Label(man_card, text="🎛️ 手动参数干预 (当前设备)",
                  style="SubHeader.TLabel").pack(anchor="w", pady=(0, 10))

        param_defs = [
            ("钻压",    "kN",  "WOB",  0,   100, "#60a5fa"),
            ("机械钻速","m/h", "ROP",  0,   50,  "#a78bfa"),
            ("转盘转速","RPM", "RPM",  0,   200, "#c084fc"),
            ("扭矩",    "kNm", "TQ",   0,   50,  "#f472b6"),
            ("泥浆流量","L/s", "FLOW", 0,   60,  "#fb7185"),
            ("井下温度","°C",  "TEMP", 0,   150, "#f97316"),
            ("气体浓度","%",   "GAS",  0,   5,   "#eab308"),
            ("立管压力","MPa", "PWH",  0,   60,  "#22c55e"),
        ]
        grid_f = ttk.Frame(man_card, style="Card.TFrame")
        grid_f.pack(fill="x")
        for idx, (label, unit, code, mn, mx, color) in enumerate(param_defs):
            row, col_base = divmod(idx, 2)
            col_offset = col_base * 4
            self._create_input_cell(grid_f, label, unit, self.manual_params[code],
                                    mn, mx, color, row, col_offset)

        ttk.Button(man_card, text="📤 发送手动数据",
                   style="Primary.TButton",
                   command=self._send_manual_data).pack(fill="x", pady=(12, 0))

        tk.Label(right_col, text=">_ 操作日志",
                 bg=self.colors["bg_input"], fg=self.colors["text_muted"],
                 font=("Consolas", 8)).pack(fill="x", pady=(10, 0))
        self.trigger_log = tk.Text(
            right_col, height=5,
            bg=self.colors["bg_input"], fg=self.colors["text_light"],
            font=("Consolas", 9), relief="flat", state="disabled")
        self.trigger_log.pack(fill="x")
        self.trigger_log.tag_configure("yellow", foreground="#eab308")
        self.trigger_log.tag_configure("red",    foreground="#ef4444")
        self.trigger_log.tag_configure("green",  foreground="#34d399")
        self.trigger_log.tag_configure("blue",   foreground="#3b82f6")

    def _create_input_cell(self, parent, label, unit, var, min_v, max_v,
                           color_hex, row, col_start):
        C = self.colors
        tk.Label(parent, text=label, bg=C["bg_card"], fg=C["text_muted"],
                 font=("微软雅黑", 9), anchor="e", width=8).grid(
            row=row, column=col_start, sticky="e", padx=(8, 2), pady=4)
        entry = tk.Entry(parent, textvariable=var, width=8,
                         bg=C["bg_input"], fg=color_hex,
                         insertbackground="white",
                         font=("Consolas", 10, "bold"),
                         relief="flat", justify="right")
        entry.grid(row=row, column=col_start + 1, padx=2, pady=4)
        tk.Label(parent, text=unit, bg=C["bg_card"], fg=C["text_muted"],
                 font=("微软雅黑", 8), width=4).grid(
            row=row, column=col_start + 2, padx=2)
        tk.Label(parent, text=f"({min_v}~{max_v})",
                 bg=C["bg_card"], fg=C["text_muted"],
                 font=("微软雅黑", 7)).grid(
            row=row, column=col_start + 3, padx=(2, 10), sticky="w")

    def _create_alert_btn(self, parent, text, color_hex, cmd, side):
        f = tk.Frame(parent, bg=self.colors["bg_card"],
                     highlightbackground=color_hex, highlightthickness=1,
                     padx=2, pady=2)
        f.pack(side=side, fill="x", expand=True, padx=2)
        tk.Button(f, text=text, bg=self.colors["bg_card"], fg=color_hex,
                  activebackground=color_hex, activeforeground="white",
                  relief="flat", font=("微软雅黑", 9, "bold"),
                  command=cmd).pack(fill="both", pady=4)

    def _on_sender_eq_change(self, _event=None):
        eq = self.current_sender_eq.get()
        self.sender_preview_title.config(
            text=f"📊 模拟数据实时看板 - {eq}")
        self._update_sender_preview(self.latest_sender_data_by_eq[eq])

    # =========================================================
    # 接收端 UI
    # =========================================================
    def _build_receiver_ui(self):
        C = self.colors
        recv_header = tk.Frame(self.receiver_frame, bg=C["bg_dark"], pady=10, padx=10)
        recv_header.pack(fill="x")
        tk.Label(recv_header, text="📡 监控目标：",
                 bg=C["bg_dark"], fg="white",
                 font=("微软雅黑", 12, "bold")).pack(side="left")
        self.receiver_eq_cb = ttk.Combobox(
            recv_header, textvariable=self.current_receiver_eq,
            values=EQUIPMENTS, state="readonly",
            font=("微软雅黑", 12, "bold"), width=15)
        self.receiver_eq_cb.pack(side="left", padx=10, ipady=3)
        self.receiver_eq_cb.bind("<<ComboboxSelected>>", self._on_receiver_eq_change)

        metrics_frame = ttk.Frame(self.receiver_frame)
        metrics_frame.pack(fill="x", padx=10, pady=(0, 10))
        self.sensor_labels = {}
        for i, (code, config) in enumerate(SENSOR_CONFIGS.items()):
            row, col = divmod(i, 4)
            card = tk.Frame(metrics_frame, bg=C["bg_card"],
                            highlightbackground=C["border"], highlightthickness=1)
            card.grid(row=row, column=col, padx=5, pady=5, sticky="nsew")
            metrics_frame.grid_columnconfigure(col, weight=1)
            tk.Label(card, text=config["name"],
                     bg=C["bg_card"], fg=C["text_muted"],
                     font=("微软雅黑", 9, "bold")).pack(anchor="w", padx=10, pady=(10, 0))
            val_lbl = tk.Label(card, text="--",
                               bg=C["bg_card"], fg=C["accent_green"],
                               font=("Consolas", 20, "bold"))
            val_lbl.pack(padx=10)
            self.sensor_labels[code] = val_lbl
            tk.Label(card, text=config["unit"],
                     bg=C["bg_card"], fg=C["text_muted"],
                     font=("Arial", 8)).pack(anchor="e", padx=10, pady=(0, 10))

        content_frame = ttk.Frame(self.receiver_frame)
        content_frame.pack(fill="both", expand=True, padx=10, pady=5)

        chart_container = tk.Frame(
            content_frame, bg=C["bg_dark"],
            highlightbackground=C["border"], highlightthickness=1)
        chart_container.pack(side="left", fill="both", expand=True, padx=(0, 10))

        tk.Label(chart_container, text="📈 关键参数趋势分析",
                 bg=C["bg_dark"], fg="white",
                 font=("微软雅黑", 12, "bold")).pack(anchor="w", padx=10, pady=10)

        selection_frame = tk.Frame(chart_container, bg=C["bg_dark"])
        selection_frame.pack(fill="x", padx=10, pady=5)
        tk.Label(selection_frame, text="显示数据: ",
                 bg=C["bg_dark"], fg=C["text_muted"]).pack(side="left")
        default_checked = ["WOB", "RPM"]
        for code in SENSOR_CONFIGS:
            var = tk.BooleanVar(value=(code in default_checked))
            self.chart_selection_vars[code] = var
            ttk.Checkbutton(selection_frame, text=code, variable=var,
                            command=self._update_chart_plot).pack(side="left", padx=5)

        self.fig = Figure(figsize=(5, 4), dpi=100)
        self.fig.patch.set_facecolor(C["bg_dark"])
        self.ax = self.fig.add_subplot(111)
        self.ax.set_facecolor(C["bg_card"])
        self.canvas = FigureCanvasTkAgg(self.fig, master=chart_container)
        self.canvas.draw()
        toolbar = NavigationToolbar2Tk(self.canvas, chart_container)
        toolbar.update()
        toolbar.config(background=C["bg_card"])
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        log_box = tk.Frame(content_frame, bg=C["bg_card"], width=300,
                           highlightbackground=C["border"], highlightthickness=1)
        log_box.pack(side="right", fill="y")
        log_box.pack_propagate(False)
        tk.Label(log_box, text="📜 系统日志 (全设备)",
                 bg="#334155", fg="white",
                 font=("微软雅黑", 9, "bold"), pady=8).pack(fill="x")
        self.sys_log = scrolledtext.ScrolledText(
            log_box, bg=C["bg_dark"], fg=C["text_muted"],
            font=("Consolas", 8), state="disabled", borderwidth=0)
        self.sys_log.pack(fill="both", expand=True)
        self.sys_log.tag_configure("CRITICAL", foreground="#ef4444", background="#450a0a")
        self.sys_log.tag_configure("WARNING",  foreground="#facc15")
        self.sys_log.tag_configure("INFO",     foreground="#3b82f6")

    def _on_receiver_eq_change(self, _event=None):
        eq = self.current_receiver_eq.get()
        for code in SENSOR_CONFIGS:
            history = self.history_data[eq][code]
            self.sensor_labels[code].config(
                text=str(history[-1]) if history else "--")
        self._update_chart_plot()

    # =========================================================
    # AI 诊断 UI
    # =========================================================
    def _build_ai_diagnosis_ui(self):
        tk.Label(self.ai_frame,
                 text="石油钻井实时监控与智能诊断控制台",
                 font=("Helvetica", 18, "bold"),
                 bg="#2c3e50", fg="white", pady=10).pack(fill=tk.X)

        main_frame = tk.Frame(self.ai_frame, padx=10, pady=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        left_frame = tk.LabelFrame(
            main_frame, text=" 实时数据面板 (严格来自 MySQL 落库) ",
            font=("Helvetica", 11, "bold"), padx=5, pady=5)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=False, padx=(0, 10))

        self.ai_eq_notebook = ttk.Notebook(left_frame)
        self.ai_eq_notebook.pack(fill=tk.BOTH, expand=True)

        self.ai_sensor_vars_by_eq = {}
        sensors_meta = [
            ("钻压 (WOB)",              "kN",  "WOB"),
            ("机械钻速 (ROP)",           "m/h", "ROP"),
            ("转盘转速 (RPM)",           "RPM", "RPM"),
            ("扭矩 (Torque)",            "kNm", "TQ"),
            ("泥浆流量 (Mud Flow)",      "L/s", "FLOW"),
            ("井下温度 (Downhole Temp)", "°C",  "TEMP"),
            ("气体浓度 (Gas Conc)",      "%",   "GAS"),
            ("立管压力 (Standpipe Press)","MPa", "PWH"),
        ]
        for eq in EQUIPMENTS:
            panel = ttk.Frame(self.ai_eq_notebook)
            self.ai_eq_notebook.add(panel, text=eq)
            self.ai_sensor_vars_by_eq[eq] = {}
            for i, (name, unit, code) in enumerate(sensors_meta):
                ttk.Label(panel, text=name,
                          font=("Helvetica", 10)).grid(
                    row=i, column=0, sticky="w", pady=6, padx=5)
                var = tk.StringVar(value="--")
                self.ai_sensor_vars_by_eq[eq][code] = var
                ttk.Label(panel, textvariable=var,
                          font=("Helvetica", 11, "bold"),
                          foreground="#2980b9", width=10,
                          anchor="e").grid(row=i, column=1, pady=6, padx=8)
                ttk.Label(panel, text=unit,
                          font=("Helvetica", 10)).grid(
                    row=i, column=2, sticky="w", pady=6)
            for c in range(3):
                panel.grid_columnconfigure(c, weight=1)

        tk.Label(left_frame, textvariable=self.ai_status_var,
                 fg="#e67e22", font=("Helvetica", 9, "italic"),
                 wraplength=320, justify="left").pack(
            fill=tk.X, pady=(8, 0))

        right_frame = tk.Frame(main_frame)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        auto_frame = tk.LabelFrame(
            right_frame, text=" 自动巡检大模型诊断结果 (自动触发) ",
            font=("Helvetica", 11, "bold"), padx=5, pady=5)
        auto_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        self.ai_text = scrolledtext.ScrolledText(
            auto_frame, wrap=tk.WORD, font=("Consolas", 10),
            bg="#fdfdfd", height=10)
        self.ai_text.pack(fill=tk.BOTH, expand=True)
        self.ai_text.insert(
            tk.END,
            ">>> 自动诊断待机中...启动模拟或手动触发后自动调用 DeepSeek\n")

        interactive_frame = tk.LabelFrame(
            right_frame, text=" 交互式智能诊断控制台 ",
            font=("Helvetica", 11, "bold"), padx=5, pady=5)
        interactive_frame.pack(fill=tk.BOTH, expand=True)

        cmd_top = tk.Frame(interactive_frame)
        cmd_top.pack(fill=tk.X, pady=(0, 5))
        tk.Label(cmd_top, text="诊断指令:",
                 font=("Helvetica", 11, "bold")).pack(side=tk.LEFT)
        self.cmd_entry = tk.Entry(cmd_top, font=("Consolas", 12), bg="#e8f6f3")
        self.cmd_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(10, 0))
        self.cmd_entry.bind("<Return>", self.handle_ai_command)
        tk.Label(cmd_top,
                 text="示例: well001-ROP  (001-005 → 设备1-5)",
                 font=("Consolas", 9, "italic"), fg="gray").pack(
            side=tk.RIGHT, padx=10)

        self.console_text = scrolledtext.ScrolledText(
            interactive_frame, wrap=tk.WORD, font=("Consolas", 10),
            bg="#1e1e1e", fg="#d4d4d4")
        self.console_text.pack(fill=tk.BOTH, expand=True)
        self.console_text.tag_config("error",  foreground="#ff6b6b")
        self.console_text.tag_config("user",   foreground="#4dabf7",
                                     font=("Consolas", 10, "bold"))
        self.console_text.tag_config("system", foreground="#20c997")
        self.console_text.tag_config("ai",     foreground="#fcc419")
        self.console_text.insert(
            tk.END,
            "系统: 交互终端已初始化。\n"
            "输入格式: well001-ROP\n"
            "支持后缀: WOB, ROP, RPM, Torque, Mud Flow, "
            "Downhole Temp, Gas Conc, Standpipe Press\n"
            "设备映射: 001~005 → 设备1~5\n",
            "system")

        tk.Frame(self.ai_frame, pady=5).pack(fill=tk.X)
        ttk.Button(self.ai_frame, text="清空控制台",
                   command=self.clear_ai_console).pack(
            side=tk.RIGHT, padx=20, pady=5)

        self.ai_param_map = {
            "WOB":            ("钻压",     "WOB"),
            "ROP":            ("机械钻速", "ROP"),
            "RPM":            ("转盘转速", "RPM"),
            "Torque":         ("扭矩",     "TQ"),
            "Mud Flow":       ("泥浆流量", "FLOW"),
            "Downhole Temp":  ("井下温度", "TEMP"),
            "Gas Conc":       ("气体浓度", "GAS"),
            "Standpipe Press":("立管压力", "PWH"),
        }

    # =========================================================
    # AI 交互指令处理
    # =========================================================
    def handle_ai_command(self, _event=None):
        cmd_text = self.cmd_entry.get().strip()
        self.cmd_entry.delete(0, tk.END)
        if not cmd_text:
            return
        self.ai_ui_queue.put({"type": "console",
                              "text": f"\n[User] {cmd_text}\n", "tag": "user"})

        parts = cmd_text.split("-", 1)
        if len(parts) != 2:
            self.ai_ui_queue.put({"type": "console",
                                  "text": "无效指令格式，示例: well001-ROP\n",
                                  "tag": "error"})
            return

        well_prefix, suffix = parts
        if suffix not in self.ai_param_map:
            self.ai_ui_queue.put({
                "type": "console",
                "text": (f"无效参数后缀: {suffix}，支持: "
                         "WOB, ROP, RPM, Torque, Mud Flow, "
                         "Downhole Temp, Gas Conc, Standpipe Press\n"),
                "tag": "error"})
            return

        device_num_str = ''.join(filter(str.isdigit, well_prefix))
        if not device_num_str:
            self.ai_ui_queue.put({"type": "console",
                                  "text": "无法识别设备编号，格式: well001-ROP\n",
                                  "tag": "error"})
            return
        device_num = int(device_num_str)
        if not (1 <= device_num <= 5):
            self.ai_ui_queue.put({"type": "console",
                                  "text": f"设备编号超出范围 (001-005)，输入: {device_num_str}\n",
                                  "tag": "error"})
            return

        target_equipment = EQUIPMENTS[device_num - 1]
        param_zh_name, dict_key = self.ai_param_map[suffix]

        db_snapshot = self.mysql_client.get_latest_snapshot(target_equipment)
        current_val = db_snapshot.get(dict_key)
        if current_val is None:
            self.ai_ui_queue.put({
                "type": "console",
                "text": (f"错误：MySQL 中 {target_equipment} 暂无 {param_zh_name} 数据，"
                         "请先启动模拟或手动触发。\n"),
                "tag": "error"})
            return

        self.ai_executor.submit(
            self.run_interactive_ai_workflow,
            well_prefix, target_equipment, param_zh_name, current_val)

    def run_interactive_ai_workflow(self, well_id, equipment,
                                   param_zh_name, current_val):
        try:
            self.ai_ui_queue.put({
                "type": "console",
                "text": (f"[*] 数据抓取: {well_id} / {equipment} "
                         f"[{param_zh_name}] = {current_val}\n"),
                "tag": "system"})
            self.ai_ui_queue.put({"type": "console",
                                  "text": f"[*] Neo4j 检索 {param_zh_name} 知识...\n",
                                  "tag": "system"})
            neo4j_result = self.neo4j_client.query_parameter_knowledge(param_zh_name)

            prompt = (f"【实时状态】: {well_id} / {equipment} 的 {param_zh_name} "
                      f"当前值为 {current_val}。\n"
                      f"【专家知识】: {neo4j_result}\n"
                      f"【任务】: 分析是否异常并给建议。")
            self.ai_ui_queue.put({"type": "console",
                                  "text": "[*] 请求 DeepSeek...\n",
                                  "tag": "system"})
            ai_reply = DeepSeekClient.interactive_analyze(prompt)
            self.ai_ui_queue.put({
                "type": "console",
                "text": f"[DeepSeek 结果]:\n{ai_reply}\n{'-' * 50}\n",
                "tag": "ai"})
        except Exception as e:
            self.ai_ui_queue.put({"type": "console",
                                  "text": f"执行异常: {e}\n",
                                  "tag": "error"})

    def clear_ai_console(self):
        self.console_text.delete(1.0, tk.END)
        self.console_text.insert(tk.END, "系统: 控制台已清空。\n", "system")

    # =========================================================
    # AI UI 队列消费（主线程定时轮询）
    # =========================================================
    def _process_ai_ui_queue(self):
        try:
            while not self.ai_ui_queue.empty():
                item = self.ai_ui_queue.get_nowait()
                t = item["type"]

                if t == "telemetry_all":
                    eq = item["eq"]
                    d  = item["data"]
                    if eq in self.ai_sensor_vars_by_eq:
                        vd = self.ai_sensor_vars_by_eq[eq]
                        fmt = {
                            "WOB":  lambda v: f"{v:.2f}",
                            "ROP":  lambda v: f"{v:.2f}",
                            "RPM":  lambda v: f"{v:.1f}",
                            "TQ":   lambda v: f"{v:.2f}",
                            "FLOW": lambda v: f"{v:.1f}",
                            "TEMP": lambda v: f"{v:.1f}",
                            "GAS":  lambda v: f"{v:.3f}",
                            "PWH":  lambda v: f"{v:.2f}",
                        }
                        for code, fn in fmt.items():
                            val = d.get(code)
                            vd[code].set(fn(val) if val is not None else "N/A")

                elif t == "ai_result":
                    self.ai_text.insert(tk.END, item["text"])
                    self.ai_text.see(tk.END)

                elif t == "console":
                    self.console_text.insert(
                        tk.END, item["text"], item.get("tag", ""))
                    self.console_text.see(tk.END)

                elif t == "status":
                    self.ai_status_var.set(item["msg"])

        except Exception as e:
            print(f"[UI Queue Error] {e}")
        finally:
            self.root.after(100, self._process_ai_ui_queue)

    # =========================================================
    # ⚡ 自动诊断管线（低延迟实时版）
    # =========================================================
    def run_ai_auto_diagnosis_pipeline(self, equipment, realtime_data=None):
        self.ai_ui_queue.put({
            "type": "status",
            "msg": f"🔄 自动诊断中：获取 {equipment} 最新数据..."})

        # 优先使用内存实时数据（延迟 < 10ms），降级使用 MySQL
        db_data = realtime_data or self.mysql_client.get_latest_snapshot(equipment)
        if not db_data:
            self.root.after(
                300,
                lambda eq=equipment: self.ai_executor.submit(
                    self.run_ai_auto_diagnosis_pipeline, eq))
            return

        self.ai_ui_queue.put({"type": "telemetry_all", "eq": equipment, "data": db_data})
        self.ai_ui_queue.put({"type": "status", "msg": "🔄 自动诊断：查询 Neo4j 图谱..."})

        neo4j_info = self.neo4j_client.query_fault_knowledge(db_data)
        self.ai_ui_queue.put({"type": "status", "msg": "🔄 自动诊断：调用 DeepSeek 大模型..."})

        ai_result = DeepSeekClient.analyze(db_data, neo4j_info)
        display = (
            f"\n{'=' * 50}\n"
            f"【自动诊断】\n"
            f"【时间】{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"【设备】{equipment}\n"
            f"【数据源】{'内存实时快照' if realtime_data else 'MySQL 落库'}\n"
            f"【图谱】{neo4j_info}\n"
            f"【DeepSeek 诊断结果】\n{ai_result}\n"
            f"{'=' * 50}\n"
        )
        self.ai_ui_queue.put({"type": "ai_result", "text": display})
        self.ai_ui_queue.put({"type": "status", "msg": "✅ 自动诊断完成，等待下次数据..."})

    def _refresh_all_devices_from_mysql(self):
        for eq in EQUIPMENTS:
            snap = self.mysql_client.get_latest_snapshot(eq)
            if snap:
                self.ai_ui_queue.put({"type": "telemetry_all", "eq": eq, "data": snap})
        self.ai_ui_queue.put({"type": "status", "msg": "✅ 所有设备数据已从 MySQL 刷新"})

    # =========================================================
    # 标签页切换
    # =========================================================
    def _on_tab_changed(self, _event=None):
        idx = self.notebook.index("current")
        if idx == 2:
            self.ai_executor.submit(self._refresh_all_devices_from_mysql)
            if not self.ai_page_started:
                self.ai_page_started = True
                self.ai_status_var.set("AI 页激活，正在从 MySQL 加载全设备数据...")
            else:
                self.ai_status_var.set("AI 页已刷新，数据来自 MySQL...")
        else:
            self.ai_status_var.set("AI 诊断页待机（数据保留）")

    # =========================================================
    # Broker 管理
    # =========================================================
    def _connect_default_broker(self):
        self._add_broker(
            DEFAULT_MQTT_CONFIG["host"], DEFAULT_MQTT_CONFIG["port"],
            DEFAULT_MQTT_CONFIG["username"], DEFAULT_MQTT_CONFIG["password"],
            is_primary=True)

    def _add_broker(self, h, p, u, pwd, is_primary=False):
        try:
            client = BrokerClient(
                h, p, u, pwd,
                on_connect_cb=self._on_broker_connect,
                on_message_cb=self._on_mqtt_message if is_primary else None,
                is_primary=is_primary)
            client.connect()
            self.brokers.append(client)
            self._refresh_broker_list_ui()
        except Exception as e:
            messagebox.showerror("连接错误", str(e))

    def _refresh_broker_list_ui(self):
        self.broker_listbox.delete(0, tk.END)
        for b in self.brokers:
            self.broker_listbox.insert(tk.END, b.get_display_name())
        self._update_status_ui()

    def _remove_selected_broker(self):
        sel = self.broker_listbox.curselection()
        if not sel:
            messagebox.showinfo("提示", "请先选中一个节点")
            return
        idx = sel[0]
        if idx < len(self.brokers):
            broker = self.brokers.pop(idx)
            broker.disconnect()
            self._refresh_broker_list_ui()
            self._log_trigger(f"已移除节点: {broker.host}", "blue")

    def _on_broker_connect(self, client_instance):
        self.root.after(0, self._refresh_broker_list_ui)

    def _update_status_ui(self):
        active = sum(1 for b in self.brokers if b.connected)
        is_online = bool(self.brokers) and self.brokers[0].connected
        if is_online:
            self.conn_status_lbl.config(text="🟢 在线", bg="#dcfce7", fg="#15803d")
        else:
            self.conn_status_lbl.config(text="🔴 离线", bg="#fee2e2", fg="#ef4444")
        self.node_status_lbl.config(text=f"传输节点: {active}")
        self.active_brokers_lbl.config(text=f"当前连接数: {active}")

        if self.btn_sim is None:
            return
        if active == 0:
            self.btn_sim.state(["disabled"])
            self.btn_manual_sim.config(state="disabled")
            self.sim_status_lbl.config(text="错误: 无连接")
        else:
            self.btn_sim.state(["!disabled"])
            self.btn_manual_sim.config(
                state="disabled" if self.is_simulating else "normal")

    def _open_broker_dialog(self):
        d = tk.Toplevel(self.root)
        d.title("添加传输节点")
        d.geometry("420x400")
        d.configure(bg=self.colors["bg_card"])
        d.transient(self.root)
        d.grab_set()

        def make_input(label_text, default):
            f = tk.Frame(d, bg=self.colors["bg_card"])
            f.pack(fill="x", padx=25, pady=8)
            tk.Label(f, text=label_text, bg=self.colors["bg_card"],
                     fg=self.colors["text_muted"],
                     font=("微软雅黑", 9)).pack(anchor="w")
            e = tk.Entry(f, bg=self.colors["bg_input"], fg="white",
                         insertbackground="white", relief="flat",
                         font=("Arial", 10))
            e.insert(0, default)
            e.pack(fill="x", pady=(4, 0), ipady=5)
            return e

        ttk.Label(d, text="配置 MQTT 服务器",
                  style="SubHeader.TLabel",
                  background=self.colors["bg_card"]).pack(pady=20)
        e_host = make_input("服务器地址 (Host)", DEFAULT_MQTT_CONFIG["host"])
        e_port = make_input("端口 (Port / SSL)", "8883")
        e_user = make_input("用户名", "emqx")
        e_pass = make_input("密码",  "123456")

        def do_connect():
            if e_host.get():
                self._add_broker(e_host.get(), e_port.get(),
                                 e_user.get(), e_pass.get())
                d.destroy()

        tk.Frame(d, bg=self.colors["bg_card"]).pack(fill="x", padx=25, pady=25)
        ttk.Button(d, text="确 认 连 接",
                   style="Primary.TButton",
                   command=do_connect).pack(padx=25, fill="x")

    # =========================================================
    # 模拟控制
    # =========================================================
    def _toggle_simulation(self):
        if self.is_simulating:
            self.is_simulating = False
            self.btn_sim.config(
                text=f"▶️ 启动全局发送 ({SIMULATION_INTERVAL}s/次)",
                style="Success.TButton")
            self.sim_status_lbl.config(text="状态: 已停止")
            self.btn_manual_sim.config(state="normal")
        else:
            if not self.brokers:
                return
            self.is_simulating = True
            self.btn_sim.config(
                text="⏹️ 停止模拟发送", style="Danger.TButton")
            self.sim_status_lbl.config(
                text="状态: 发送中 (多设备独立模拟)...",
                foreground=self.colors["accent_green"])
            self.btn_manual_sim.config(state="disabled")
            self.sim_thread = threading.Thread(
                target=self._sim_loop, daemon=True)
            self.sim_thread.start()

    def _manual_trigger_sim(self):
        if self.is_simulating:
            messagebox.showinfo("提示", "自动模拟运行中，请先停止。")
            return
        if not self.brokers:
            messagebox.showinfo("提示", "请先连接 MQTT 节点")
            return
        self.btn_manual_sim.config(state="disabled")
        self._log_trigger("🖱️ 手动触发单次数据模拟...", "blue")
        self.sim_status_lbl.config(
            text="状态: 正在生成并落库...",
            foreground=self.colors["accent_blue"])

        def _run():
            self._send_batch()
            time.sleep(0.8)
            self.root.after(0, lambda: self.sim_status_lbl.config(
                text="状态: 已手动发送一次",
                foreground=self.colors["accent_blue"]))
            self.root.after(2500, lambda: self.sim_status_lbl.config(
                text="状态: 待机",
                foreground=self.colors["text_muted"]))
            self.root.after(0, lambda: self.btn_manual_sim.config(state="normal"))

        threading.Thread(target=_run, daemon=True).start()

    def _sim_loop(self):
        while self.is_simulating:
            self._send_batch()
            time.sleep(SIMULATION_INTERVAL)

    # =========================================================
    # 数据发送核心
    # =========================================================
    def _broadcast(self, topic, payload):
        for b in self.brokers:
            b.publish(topic, payload)

    def _send_batch(self):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        records_to_db = []

        for eq in EQUIPMENTS:
            for code, cfg in SENSOR_CONFIGS.items():
                cur = self.sim_states[eq][code]
                span = cfg["default_max"] - cfg["default_min"]
                delta = random.uniform(-0.05 * span, 0.05 * span)
                new_val = cur + delta
                new_val = max(cfg["default_min"], min(cfg["default_max"], new_val))
                self.sim_states[eq][code] = new_val
                val = round(new_val, 3)
                self.latest_sender_data_by_eq[eq][code] = val
                records_to_db.append((WELL_ID, eq, code, val, cfg["unit"], now))

                payload = {
                    "name": cfg["name"], "value": val, "unit": cfg["unit"],
                    "timestamp": now, "min": cfg["min"], "max": cfg["max"]
                }
                topic = (f"{DEFAULT_MQTT_CONFIG['topic_root']}"
                         f"/{eq}/sensor/{code}01/data")
                self._broadcast(topic, payload)

            if random.random() > 0.95:
                self._broadcast(
                    f"{DEFAULT_MQTT_CONFIG['topic_root']}/{eq}/sys/log",
                    {"level": random.choice(["INFO", "WARNING"]),
                     "message": f"[{eq}] 自动巡检: 运转稳定",
                     "source": "Sim", "timestamp": now})

        # 批量落库
        for rec in records_to_db:
            self.mysql_insert_queue.put(rec)

        # 更新发送端预览
        cur_eq = self.current_sender_eq.get()
        self.root.after(0, lambda: self._update_sender_preview(
            self.latest_sender_data_by_eq[cur_eq]))

        # ⚡ 低延迟实时触发 AI 巡检（带冷却防刷机制）
        current_time = time.time()
        for eq in EQUIPMENTS:
            with self._diagnosis_lock:
                if current_time - self._last_diagnosis_time[eq] > self._diagnosis_cooldown:
                    self._last_diagnosis_time[eq] = current_time
                    # 直接传入内存快照，绕过 MySQL 查询延迟，实现 <50ms 触发
                    self.ai_executor.submit(
                        self.run_ai_auto_diagnosis_pipeline,
                        eq,
                        realtime_data=self.latest_sender_data_by_eq[eq].copy()
                    )

    def _update_sender_preview(self, data):
        for code, val in data.items():
            if code in self.sender_preview_labels:
                self.sender_preview_labels[code].config(text=str(val))

    # =========================================================
    # 手动发送（全8参数）
    # =========================================================
    def _send_manual_data(self):
        if not self.brokers:
            messagebox.showinfo("提示", "请先连接 MQTT 节点")
            return
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        eq  = self.current_sender_eq.get()

        values = {}
        for code, var in self.manual_params.items():
            try:
                val = float(var.get())
            except (tk.TclError, ValueError):
                messagebox.showwarning(
                    "输入错误",
                    f"{SENSOR_CONFIGS[code]['name']} 的值不合法，请输入数字")
                return
            cfg = SENSOR_CONFIGS[code]
            if not (cfg["min"] <= val <= cfg["max"]):
                messagebox.showwarning(
                    "范围错误",
                    f"{cfg['name']} 超出范围 "
                    f"({cfg['min']} ~ {cfg['max']} {cfg['unit']})")
                return
            values[code] = val

        for code, val in values.items():
            cfg = SENSOR_CONFIGS[code]
            self.sim_states[eq][code] = val
            self.mysql_insert_queue.put((WELL_ID, eq, code, val, cfg["unit"], now))
            self._broadcast(
                f"{DEFAULT_MQTT_CONFIG['topic_root']}/{eq}/sensor/{code}01/data",
                {"name": cfg["name"], "value": val, "unit": cfg["unit"],
                 "timestamp": now, "min": cfg["min"], "max": cfg["max"]})

        summary = ", ".join(
            f"{SENSOR_CONFIGS[c]['name']}={v}" for c, v in values.items())
        self._broadcast(
            f"{DEFAULT_MQTT_CONFIG['topic_root']}/{eq}/sys/log",
            {"level": "INFO",
             "message": f"[{eq}] 手动干预: {summary}",
             "source": "Console", "timestamp": now})
        self._log_trigger(f"[{eq}] 手动干预已发送: {summary}", "green")

        # ⚡ 手动触发同样走低延迟管线
        self.ai_executor.submit(
            self.run_ai_auto_diagnosis_pipeline,
            eq,
            realtime_data=self.latest_sender_data_by_eq[eq].copy()
        )

    # =========================================================
    # 异常注入
    # =========================================================
    def _trigger_alert(self, type_):
        if not self.brokers:
            return
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        eq  = self.current_sender_eq.get()
        if type_ == "GAS":
            msg, lvl, src, color = "井区检测到硫化氢 (H2S) 浓度超标", "High",     "GAS01", "yellow"
        else:
            msg, lvl, src, color = "立管泵压突增，存在井涌风险",       "Critical", "PWH01", "red"
        self._broadcast(
            f"{DEFAULT_MQTT_CONFIG['topic_root']}/{eq}/sensor/{src}/alert",
            {"msg": msg, "level": lvl, "source": src,
             "timestamp": now, "eq": eq})
        self._log_trigger(f"[{eq}] 注入严重异常: {msg}", color)

    # =========================================================
    # 日志辅助
    # =========================================================
    def _log_trigger(self, msg, color="blue"):
        now = datetime.now().strftime("%H:%M:%S")
        self.trigger_log.config(state="normal")
        self.trigger_log.insert("1.0", f"[{now}] {msg}\n", color)
        self.trigger_log.config(state="disabled")

    def _log_system(self, ts, level, msg, src):
        tag = level if level in ("CRITICAL", "WARNING", "INFO") else "INFO"
        self.sys_log.config(state="normal")
        self.sys_log.insert("1.0", f" {msg} ({src})\n")
        self.sys_log.insert("1.0", f"[{level}]", tag)
        self.sys_log.insert("1.0", f"[{ts}] ")
        self.sys_log.config(state="disabled")

    # =========================================================
    # MQTT 消息处理
    # =========================================================
    def _on_mqtt_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
            self.root.after(0, lambda: self._process_data(msg.topic, payload))
        except Exception:
            pass

    def _process_data(self, topic, data):
        target_eq = next(
            (eq for eq in EQUIPMENTS if f"/{eq}/" in topic), None)
        if not target_eq:
            return

        if "/data" in topic:
            code = next((k for k in SENSOR_CONFIGS if k in topic), None)
            if code:
                val = data["value"]
                self.history_data[target_eq][code].append(val)
                self.latest_sender_data_by_eq[target_eq][code] = val
                if code == "WOB":
                    self.history_time[target_eq].append(
                        data.get("timestamp",
                                 datetime.now().strftime("%H:%M:%S")))
                if (target_eq == self.current_receiver_eq.get()
                        and code in self.sensor_labels):
                    self.sensor_labels[code].config(text=str(val))
                    self._update_chart_plot()

        elif "/alert" in topic:
            msg_txt = (f"[{data['timestamp']}] ⚠️ "
                       f"[{target_eq}] {data['msg']}")
            if target_eq == self.current_receiver_eq.get():
                messagebox.showwarning(
                    f"接收端告警 - {target_eq}", msg_txt)
            self._log_system(
                data['timestamp'], "CRITICAL",
                f"[{target_eq}] {data['msg']}", data['source'])

        elif "/log" in topic:
            self._log_system(
                data['timestamp'], data['level'],
                data['message'], data['source'])

    # =========================================================
    # 图表
    # =========================================================
    def _update_chart_plot(self):
        self.ax.clear()
        self.ax.set_facecolor(self.colors["bg_card"])
        eq = self.current_receiver_eq.get()
        has_data = False
        for code, var in self.chart_selection_vars.items():
            if var.get():
                d = self.history_data[eq][code]
                if d:
                    self.ax.plot(list(d), label=SENSOR_CONFIGS[code]["name"])
                    has_data = True
        self.ax.set_title(f"实时趋势 - {eq}", color="white", fontsize=10)
        self.ax.set_xlabel("数据点", color="#94a3b8")
        self.ax.set_ylabel("数值",   color="#94a3b8")
        self.ax.tick_params(axis="x", colors="#94a3b8")
        self.ax.tick_params(axis="y", colors="#94a3b8")
        self.ax.grid(True, color="#334155", linestyle="--", linewidth=0.5)
        if has_data:
            self.ax.legend(
                facecolor=self.colors["bg_card"],
                edgecolor=self.colors["border"],
                labelcolor="white", fontsize=8)
        self.canvas.draw()

    # =========================================================
    # 退出
    # =========================================================
    def on_closing(self):
        self.is_running    = False
        self.is_simulating = False
        self.neo4j_client.close()
        self.ai_executor.shutdown(wait=False)
        for b in self.brokers:
            b.disconnect()
        self.root.destroy()