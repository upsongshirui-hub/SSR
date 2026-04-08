import random

# ==================== 常量与配置 ====================
WELL_ID = f"well-{random.randint(100, 999)}"
SIMULATION_INTERVAL = 30
EQUIPMENTS = ["设备1", "设备2", "设备3", "设备4", "设备5"]

DEFAULT_MQTT_CONFIG = {
    "host": "b0a21395.ala.cn-hangzhou.emqxsl.cn",
    "port": 8883,
    "username": "emqx",
    "password": "123456",
    "topic_root": f"oil_demo_cn/{WELL_ID}"
}

MYSQL_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": "12345678",
    "database": "oil_drilling_db",
    "pool_name": "drilling_pool",
    "pool_size": 5
}

SENSOR_CONFIGS = {
    "WOB":  {"name": "钻压",    "unit": "kN",  "min": 0, "max": 100, "default_min": 15, "default_max": 45},
    "ROP":  {"name": "机械钻速","unit": "m/h", "min": 0, "max": 50,  "default_min": 8,  "default_max": 18},
    "RPM":  {"name": "转盘转速","unit": "RPM", "min": 0, "max": 200, "default_min": 90, "default_max": 110},
    "TQ":   {"name": "扭矩",    "unit": "kNm", "min": 0, "max": 50,  "default_min": 18, "default_max": 22},
    "FLOW": {"name": "泥浆流量","unit": "L/s", "min": 0, "max": 60,  "default_min": 28, "default_max": 32},
    "TEMP": {"name": "井下温度","unit": "°C",  "min": 0, "max": 150, "default_min": 50, "default_max": 70},
    "GAS":  {"name": "气体浓度","unit": "%",   "min": 0, "max": 5,   "default_min": 0,  "default_max": 0.5},
    "PWH":  {"name": "立管压力","unit": "MPa", "min": 0, "max": 60,  "default_min": 12, "default_max": 14}
}

DEEPSEEK_API_KEY = "sk-0558f2143b974018a6df045f868d1858"
DEEPSEEK_BASE_URL = "https://api.deepseek.com/chat/completions"
NEO4J_URI = "neo4j://127.0.0.1:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "12345678"

COLORS = {
    "bg_dark":    "#0f172a",
    "bg_card":    "#1e293b",
    "bg_input":   "#020617",
    "text_light": "#e2e8f0",
    "text_muted": "#94a3b8",
    "accent_blue":  "#3b82f6",
    "accent_green": "#10b981",
    "border": "#334155"
}