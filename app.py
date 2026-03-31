import streamlit as st
import time
import threading
import pandas as pd
import numpy as np
from collections import deque
import datetime
import matplotlib.pyplot as plt
import pydeck as pdk

# ----------------------------- 全局数据结构 -----------------------------
history = deque(maxlen=200)        # 心跳历史 (时间戳, 序号)
positions = deque(maxlen=100)      # 位置历史 (纬度, 经度, 海拔, 心跳序号)
history_lock = threading.Lock()
positions_lock = threading.Lock()

def heartbeat_sender():
    """后台线程：每秒发送一次心跳，并生成对应的模拟位置"""
    seq = 0
    center_lat, center_lon = 40.7128, -74.0060  # 纽约市中心
    radius = 0.005  # 轨迹半径（约500米）
    while True:
        time.sleep(1)
        seq += 1
        now = time.time()

        # 生成模拟位置（圆形轨迹）
        angle = seq * 0.1  # 每步转动0.1弧度
        lat = center_lat + radius * np.cos(angle)
        lon = center_lon + radius * np.sin(angle)
        altitude = seq * 2  # 海拔（米）用心跳序号线性放大，便于观察

        # 更新 session_state 实时信息
        st.session_state['last_received'] = now
        st.session_state['current_seq'] = seq
        st.session_state['last_heartbeat_info'] = f"序号: {seq}, 时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now))}"
        st.session_state['timeout_flag'] = False

        # 存储心跳数据
        with history_lock:
            history.append((now, seq))
        # 存储位置数据
        with positions_lock:
            positions.append((lat, lon, altitude, seq))

# ----------------------------- 初始化 -----------------------------
if 'initialized' not in st.session_state:
    st.session_state['last_received'] = time.time()
    st.session_state['current_seq'] = 0
    st.session_state['last_heartbeat_info'] = "等待心跳..."
    st.session_state['timeout_flag'] = False
    st.session_state['initialized'] = True
    # 启动后台心跳线程
    thread = threading.Thread(target=heartbeat_sender, daemon=True)
    thread.start()

# 页面布局
st.title("🚁 无人机心跳监控 + 3D 地图")
st.markdown("模拟无人机心跳自收自发，每秒发送一次，3秒未收到则报警；地图上柱状高度代表心跳序号，颜色深浅表示心跳强度。")

# 侧边栏：Mapbox Token 输入（使用公共示例 token 有限制，建议注册自己的）
mapbox_token = st.sidebar.text_input(
    "Mapbox Token（可选）",
    value="pk.eyJ1IjoibWFwYm94IiwiYSI6ImNpejY4M29iazA2Z2gycXA4N2pmbDZmangifQ.-g_vE53SD2WrJ6t-r0D0FQ"
)
if mapbox_token:
    pdk.settings.mapbox_key = mapbox_token

# 实时显示区域
placeholder = st.empty()       # 用于显示实时指标和报警
chart_placeholder = st.empty() # 用于显示心跳折线图
map_placeholder = st.empty()   # 用于显示3D地图

# ----------------------------- 主循环（每秒刷新） -----------------------------
while True:
    now = time.time()
    last = st.session_state['last_received']
    delta = now - last
    delta_int = int(round(delta))

    # 超时检测
    if delta > 3 and not st.session_state['timeout_flag']:
        st.session_state['timeout_flag'] = True

    # ---------- 更新实时指标区域 ----------
    with placeholder.container():
        col1, col2 = st.columns(2)
        with col1:
            st.metric("最新心跳", st.session_state['last_heartbeat_info'])
        with col2:
            st.metric("距上次心跳", f"{delta_int} 秒")

        if st.session_state['timeout_flag']:
            st.error("⚠️ 连接超时！超过 3 秒未收到心跳。")
        else:
            st.success("✅ 连接正常")

    # ---------- 心跳折线图 ----------
    with history_lock:
        if history:
            df = pd.DataFrame(history, columns=['timestamp', 'seq'])
            df['time_str'] = pd.to_datetime(df['timestamp'], unit='s').dt.strftime('%H:%M:%S')
            fig, ax = plt.subplots(figsize=(10, 4))
            ax.plot(df['time_str'], df['seq'], marker='o', markersize=4, linewidth=2, color='#1f77b4')
            ax.set_xlabel('时间 (时:分:秒)')
            ax.set_ylabel('心跳包序号')
            ax.set_title('心跳包数量变化趋势')
            plt.xticks(rotation=45, ha='right')
            plt.tight_layout()
            chart_placeholder.pyplot(fig)
            plt.close(fig)
        else:
            chart_placeholder.info("等待心跳数据...")

    # ---------- 3D 地图（pydeck 柱状图） ----------
    with positions_lock:
        if positions:
            # 将位置数据转为 DataFrame
            pos_df = pd.DataFrame(positions, columns=['lat', 'lon', 'altitude', 'seq'])
            # 颜色映射：用心跳序号，从蓝（小）到红（大）
            def get_color(seq):
                # seq 范围假设 0~200，映射到 RGB
                r = min(255, int(255 * (seq / 200)))
                g = 50
                b = min(255, int(255 * (1 - seq / 200)))
                return [r, g, b]
            pos_df['color'] = pos_df['seq'].apply(get_color)

            # 创建柱状图层（每个位置一根柱子，高度为海拔）
            column_layer = pdk.Layer(
                "ColumnLayer",
                data=pos_df,
                get_position=["lon", "lat"],
                get_elevation="altitude",
                elevation_scale=1,
                radius=10,               # 柱子半径（米）
                get_fill_color="color",
                pickable=True,
                auto_highlight=True,
            )

            # 轨迹线（PathLayer）
            # 将经纬度序列整理为路径
            if len(pos_df) > 1:
                path_coords = pos_df[['lon', 'lat']].values.tolist()
                path_layer = pdk.Layer(
                    "PathLayer",
                    data=[{"path": path_coords}],
                    get_path="path",
                    get_width=2,
                    get_color=[255, 255, 0],
                    width_scale=1,
                )
                layers = [column_layer, path_layer]
            else:
                layers = [column_layer]

            # 视图状态：跟随最新位置
            latest = pos_df.iloc[-1]
            view_state = pdk.ViewState(
                latitude=latest['lat'],
                longitude=latest['lon'],
                zoom=15,
                pitch=50,
                bearing=0,
            )

            # 绘制地图
            deck = pdk.Deck(
                layers=layers,
                initial_view_state=view_state,
                tooltip={"text": "序号: {seq}\n海拔: {altitude} m"}
            )
            map_placeholder.pydeck_chart(deck, use_container_width=True)
        else:
            map_placeholder.info("等待位置数据...")

    time.sleep(1)  # 每秒刷新一次
