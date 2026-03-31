import streamlit as st
import time
import threading
import pandas as pd
import numpy as np
from collections import deque
import datetime
import matplotlib.pyplot as plt
import pydeck as pdk
from geopy.distance import distance

# ----------------------------- 南京科技职业学院内部矩形路径点（四个角）-----------------------------
# 定义矩形四个顶点（大致构成矩形，顺序为顺时针）
CAMPUS_POINTS = [
    (32.2322, 118.7858, "校门"),          # 西南角
    (32.2330, 118.7862, "教学楼A"),        # 东南角
    (32.2335, 118.7855, "图书馆"),         # 东北角
    (32.2327, 118.7848, "食堂"),           # 西北角
]
# 为了形成闭合矩形，路径需要按顺序连接四个点，然后回到起点（循环）
ROUTE_LAT = [p[0] for p in CAMPUS_POINTS]
ROUTE_LON = [p[1] for p in CAMPUS_POINTS]
ROUTE_NAMES = [p[2] for p in CAMPUS_POINTS]

# ----------------------------- 全局数据结构 -----------------------------
history = deque(maxlen=200)
positions = deque(maxlen=100)
history_lock = threading.Lock()
positions_lock = threading.Lock()

def heartbeat_sender():
    """后台线程：每秒发送一次心跳，并按矩形路径移动"""
    seq = 0
    current_segment = 0
    steps_per_segment = 8   # 每个线段分成8步，使运动平滑
    step_in_segment = 0

    while True:
        time.sleep(1)
        seq += 1
        now = time.time()

        # 根据当前路段和步数计算位置
        if current_segment < len(ROUTE_LAT):
            start_lat, start_lon = ROUTE_LAT[current_segment], ROUTE_LON[current_segment]
            # 下一个点：如果是最后一个点则回到第一个点，形成闭环
            next_idx = (current_segment + 1) % len(ROUTE_LAT)
            end_lat, end_lon = ROUTE_LAT[next_idx], ROUTE_LON[next_idx]
            t = step_in_segment / steps_per_segment
            lat = start_lat + (end_lat - start_lat) * t
            lon = start_lon + (end_lon - start_lon) * t
            step_in_segment += 1
            if step_in_segment >= steps_per_segment:
                step_in_segment = 0
                current_segment = next_idx  # 移到下一段
        else:
            # 安全起见，重置
            current_segment = 0
            step_in_segment = 0
            lat, lon = ROUTE_LAT[0], ROUTE_LON[0]

        altitude = seq * 2  # 海拔（米），此处保留但不用于柱状图，仅用于数据记录

        st.session_state['last_received'] = now
        st.session_state['current_seq'] = seq
        st.session_state['last_heartbeat_info'] = f"序号: {seq}, 时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now))}"
        st.session_state['timeout_flag'] = False

        with history_lock:
            history.append((now, seq))
        with positions_lock:
            positions.append((lat, lon, altitude, seq))

# ----------------------------- 初始化 -----------------------------
if 'initialized' not in st.session_state:
    st.session_state['last_received'] = time.time()
    st.session_state['current_seq'] = 0
    st.session_state['last_heartbeat_info'] = "等待心跳..."
    st.session_state['timeout_flag'] = False
    st.session_state['initialized'] = True
    thread = threading.Thread(target=heartbeat_sender, daemon=True)
    thread.start()

# 页面布局
st.title("🚁 无人机心跳监控 + 矩形路径规划 + 3D 地图")
st.markdown("模拟无人机按照矩形路径飞行，地图上显示路径、地名和实时位置，3秒未收到心跳报警。")

# 侧边栏：Mapbox Token 输入
mapbox_token = st.sidebar.text_input(
    "Mapbox Token（可选）",
    value="pk.eyJ1IjoibWFwYm94IiwiYSI6ImNpejY4M29iazA2Z2gycXA4N2pmbDZmangifQ.-g_vE53SD2WrJ6t-r0D0FQ"
)
if mapbox_token:
    pdk.settings.mapbox_key = mapbox_token

# 侧边栏：矩形路径点展示
st.sidebar.subheader("🗺️ 矩形路径点（顺时针）")
for idx, name in enumerate(ROUTE_NAMES, 1):
    st.sidebar.write(f"{idx}. {name}")
st.sidebar.caption("无人机将沿矩形顺时针循环飞行")

# 实时显示区域
placeholder = st.empty()
chart_placeholder = st.empty()
map_placeholder = st.empty()

# ----------------------------- 主循环（每秒刷新） -----------------------------
while True:
    now = time.time()
    last = st.session_state['last_received']
    delta = now - last
    delta_int = int(round(delta))

    if delta > 3 and not st.session_state['timeout_flag']:
        st.session_state['timeout_flag'] = True

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

    # 心跳折线图（不变）
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

    # 3D 地图（无柱状图，只有路径、地名和当前位置点）
    with positions_lock:
        if positions:
            pos_df = pd.DataFrame(positions, columns=['lat', 'lon', 'altitude', 'seq'])

            # 图层列表
            layers = []

            # 1. 规划路线（矩形路径，青色）
            planned_path_coords = []
            for i in range(len(ROUTE_LAT)):
                planned_path_coords.append([ROUTE_LON[i], ROUTE_LAT[i]])
            # 闭合矩形：添加第一个点到末尾形成闭合
            planned_path_coords.append([ROUTE_LON[0], ROUTE_LAT[0]])
            planned_path_layer = pdk.Layer(
                "PathLayer",
                data=[{"path": planned_path_coords}],
                get_path="path",
                get_width=3,
                get_color=[0, 255, 255],  # 青色
                width_scale=1,
                opacity=0.8,
            )
            layers.append(planned_path_layer)

            # 2. 无人机轨迹（实际飞过的路径，黄色）
            if len(pos_df) > 1:
                path_coords = pos_df[['lon', 'lat']].values.tolist()
                path_layer = pdk.Layer(
                    "PathLayer",
                    data=[{"path": path_coords}],
                    get_path="path",
                    get_width=2,
                    get_color=[255, 255, 0],  # 黄色
                    width_scale=1,
                )
                layers.append(path_layer)

            # 3. 地名标签（每个矩形顶点）
            text_data = pd.DataFrame({
                "lat": ROUTE_LAT,
                "lon": ROUTE_LON,
                "name": ROUTE_NAMES
            })
            text_layer = pdk.Layer(
                "TextLayer",
                data=text_data,
                get_position=["lon", "lat"],
                get_text="name",
                get_size=16,
                get_color=[255, 255, 255, 255],  # 白色文字
                get_angle=0,
                get_text_anchor="middle",
                get_alignment_baseline="center",
                pickable=False,
                font_family="Arial",
                billboard=True,
            )
            layers.append(text_layer)

            # 4. 无人机当前位置点（红色圆点，大小为15像素）
            current_pos = pos_df.iloc[-1]
            current_point_layer = pdk.Layer(
                "ScatterplotLayer",
                data=pd.DataFrame({
                    "lat": [current_pos['lat']],
                    "lon": [current_pos['lon']]
                }),
                get_position=["lon", "lat"],
                get_radius=5,            # 半径5米
                get_fill_color=[255, 0, 0, 255],  # 红色
                pickable=True,
            )
            layers.append(current_point_layer)

            # 视图状态：跟随最新位置，zoom=18 放大比例尺
            view_state = pdk.ViewState(
                latitude=current_pos['lat'],
                longitude=current_pos['lon'],
                zoom=18,
                pitch=50,
                bearing=0,
            )

            deck = pdk.Deck(
                layers=layers,
                initial_view_state=view_state,
                tooltip={"text": "序号: {seq}\n海拔: {altitude} m"}
            )
            map_placeholder.pydeck_chart(deck, use_container_width=True)
        else:
            map_placeholder.info("等待位置数据...")

    time.sleep(1)
