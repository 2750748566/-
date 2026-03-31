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

# ----------------------------- 南京科技职业学院内部关键点（粗略坐标）-----------------------------
# 坐标来源：通过高德/百度地图粗略获取，实际使用前请校正
# 假设校园范围约500米
CAMPUS_POINTS = [
    (32.2322, 118.7858, "校门"),          # 校门（原有中心点）
    (32.2328, 118.7865, "教学楼A"),
    (32.2335, 118.7850, "图书馆"),
    (32.2329, 118.7842, "食堂"),
    (32.2320, 118.7845, "实验楼"),
    (32.2315, 118.7858, "体育馆"),
]
# 提取经纬度列表
ROUTE_LAT = [p[0] for p in CAMPUS_POINTS]
ROUTE_LON = [p[1] for p in CAMPUS_POINTS]

# ----------------------------- 全局数据结构 -----------------------------
history = deque(maxlen=200)
positions = deque(maxlen=100)
history_lock = threading.Lock()
positions_lock = threading.Lock()

def heartbeat_sender():
    """后台线程：每秒发送一次心跳，并按预定义路线移动"""
    seq = 0
    # 当前所在路段的索引（0 到 len(ROUTE)-2）
    current_segment = 0
    # 每个路段总步数（为了让运动更平滑，可以增加插值，这里简单起见每个路段停留5秒）
    steps_per_segment = 5
    step_in_segment = 0

    while True:
        time.sleep(1)
        seq += 1
        now = time.time()

        # 根据当前路段和步数计算位置
        if current_segment < len(ROUTE_LAT) - 1:
            start_lat, start_lon = ROUTE_LAT[current_segment], ROUTE_LON[current_segment]
            end_lat, end_lon = ROUTE_LAT[current_segment+1], ROUTE_LON[current_segment+1]
            t = step_in_segment / steps_per_segment
            lat = start_lat + (end_lat - start_lat) * t
            lon = start_lon + (end_lon - start_lon) * t
            step_in_segment += 1
            if step_in_segment >= steps_per_segment:
                step_in_segment = 0
                current_segment += 1
                if current_segment >= len(ROUTE_LAT) - 1:
                    # 到达终点，可选择折返或循环，这里选择折返（倒序）
                    ROUTE_LAT.reverse()
                    ROUTE_LON.reverse()
                    current_segment = 0
                    step_in_segment = 0
        else:
            # 安全起见，重置
            current_segment = 0
            step_in_segment = 0
            lat, lon = ROUTE_LAT[0], ROUTE_LON[0]

        altitude = seq * 2  # 海拔（米）用心跳序号线性放大

        # 更新 session_state
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
    st.session_state['obstacles'] = []   # 障碍物列表
    # 启动后台心跳线程
    thread = threading.Thread(target=heartbeat_sender, daemon=True)
    thread.start()

# 页面布局
st.title("🚁 无人机心跳监控 + 校园内部路线规划 + 3D 地图")
st.markdown("模拟无人机按照南京科技职业学院内部预设路线飞行，3秒未收到心跳报警；地图上柱状高度代表心跳序号，红色柱体为障碍物，黄色线为规划路线。")

# 侧边栏：Mapbox Token 输入
mapbox_token = st.sidebar.text_input(
    "Mapbox Token（可选）",
    value="pk.eyJ1IjoibWFwYm94IiwiYSI6ImNpejY4M29iazA2Z2gycXA4N2pmbDZmangifQ.-g_vE53SD2WrJ6t-r0D0FQ"
)
if mapbox_token:
    pdk.settings.mapbox_key = mapbox_token

# 侧边栏：障碍物管理（与之前相同，略）
st.sidebar.subheader("🗻 障碍物管理")
col1, col2 = st.sidebar.columns(2)
with col1:
    obs_lat = st.number_input("纬度", value=32.2322 + 0.001, format="%.6f")
with col2:
    obs_lon = st.number_input("经度", value=118.7858 + 0.001, format="%.6f")
if st.sidebar.button("➕ 添加障碍物"):
    st.session_state['obstacles'].append((obs_lat, obs_lon))
if st.session_state['obstacles']:
    st.sidebar.write("当前障碍物：")
    for i, (lat, lon) in enumerate(st.session_state['obstacles']):
        col1, col2 = st.sidebar.columns([4, 1])
        col1.write(f"{i+1}. ({lat:.6f}, {lon:.6f})")
        if col2.button("❌", key=f"del_{i}"):
            st.session_state['obstacles'].pop(i)
            st.rerun()
else:
    st.sidebar.info("暂无障碍物，请添加。")

# 实时显示区域
placeholder = st.empty()
chart_placeholder = st.empty()
map_placeholder = st.empty()
warning_placeholder = st.sidebar.empty()

# ----------------------------- 主循环（每秒刷新） -----------------------------
while True:
    now = time.time()
    last = st.session_state['last_received']
    delta = now - last
    delta_int = int(round(delta))

    if delta > 3 and not st.session_state['timeout_flag']:
        st.session_state['timeout_flag'] = True

    # 实时指标
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

    # 心跳折线图（同前）
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

    # 碰撞检测（同前）
    warning_msg = None
    with positions_lock:
        if positions and st.session_state['obstacles']:
            latest_pos = positions[-1]
            uav_lat, uav_lon = latest_pos[0], latest_pos[1]
            for obs_lat, obs_lon in st.session_state['obstacles']:
                dist = distance((uav_lat, uav_lon), (obs_lat, obs_lon)).meters
                if dist < 50:
                    warning_msg = f"⚠️ 无人机靠近障碍物 ({obs_lat:.6f}, {obs_lon:.6f})，距离 {dist:.1f} 米！"
                    break
    if warning_msg:
        warning_placeholder.warning(warning_msg)
    else:
        warning_placeholder.empty()

    # 3D 地图（含路线规划展示）
    with positions_lock:
        if positions:
            pos_df = pd.DataFrame(positions, columns=['lat', 'lon', 'altitude', 'seq'])
            def get_color(seq):
                r = min(255, int(255 * (seq / 200)))
                g = 50
                b = min(255, int(255 * (1 - seq / 200)))
                return [r, g, b]
            pos_df['color'] = pos_df['seq'].apply(get_color)

            # 无人机柱状图层
            column_layer = pdk.Layer(
                "ColumnLayer",
                data=pos_df,
                get_position=["lon", "lat"],
                get_elevation="altitude",
                elevation_scale=1,
                radius=10,
                get_fill_color="color",
                pickable=True,
                auto_highlight=True,
            )

            # 无人机轨迹线
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

            # 添加预定义规划路线（用蓝色虚线表示，此处用实线）
            # 将规划的路线点转成路径
            planned_path_coords = list(zip(ROUTE_LON, ROUTE_LAT))
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

            # 障碍物图层
            if st.session_state['obstacles']:
                obs_df = pd.DataFrame(st.session_state['obstacles'], columns=['lat', 'lon'])
                obs_df['altitude'] = 20
                obs_df['color'] = [[255, 0, 0]] * len(obs_df)
                obstacle_layer = pdk.Layer(
                    "ColumnLayer",
                    data=obs_df,
                    get_position=["lon", "lat"],
                    get_elevation="altitude",
                    elevation_scale=1,
                    radius=15,
                    get_fill_color="color",
                    pickable=True,
                )
                layers.append(obstacle_layer)

            # 视图状态：跟随最新位置，zoom 放大至 18 以显示校园细节
            latest = pos_df.iloc[-1]
            view_state = pdk.ViewState(
                latitude=latest['lat'],
                longitude=latest['lon'],
                zoom=18,          # 放大比例尺
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
