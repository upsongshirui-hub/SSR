import tkinter as tk
import matplotlib
import matplotlib.pyplot as plt
import logging

matplotlib.use("TkAgg")

# 全局设置
plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'SimSun']
plt.rcParams['axes.unicode_minus'] = False
logging.getLogger("neo4j").setLevel(logging.ERROR)
logging.getLogger("neo4j.pool").setLevel(logging.ERROR)

from gui_app import WellMonitorApp
from config import WELL_ID


if __name__ == "__main__":
    print(f"启动智能钻井指挥中心 - {WELL_ID}")
    root = tk.Tk()
    app = WellMonitorApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()