import streamlit as st
import time
import threading
import pandas as pd
import numpy as np
from collections import deque
import matplotlib.pyplot as plt
import folium
from streamlit_folium import st_folium
from geopy.distance import distance

# ----------------------------- 初始默认路径（南京科技职业学院矩形）-----------------------------
DEFAULT_POINTS = [
    {"lat": 32.2322, "lon": 118.7858, "name": "校门"},
    {"lat": 32.2330, "lon": 118.7862, "name": "教学楼A"},
    {"lat": 32.2335, "lon": 118.7855, "name": "图书馆"},
    {"lat": 32.2327, "lon": 118.7848, "name": "食堂"},
]

# ----------------------------- 初始化 session_state -----------------------------
if "points" not in st.session_state:
    st.session_state.points = DEFAULT_POINTS.copy()
if "initialized" not in st.session_state:
    st.session_state.last_received = time.time()
    st.session_state.current_seq = 0
    st.session_state.last_heartbeat_info = "等待心跳..."
    st.session_state.timeout_flag = False
    st.session_state.initialized = True

# 数据缓存
history = deque(maxlen=200)
positions = deque(maxlen=100)
history_lock = threading.Lock()
positions_lock = threading.Lock()

# 后台心跳线程（读取 session_state 中的路径点）
def heartbeat_sender():
    seq = 0
    while True:
        time.sleep(1)
        seq += 1
        now = time.time()
        points = st.session_state.points
        if len(points) < 2:
            lat, lon = points[0]["lat"], points[0]["lon"] if points else (32.2322, 118.7858)
        else:
            total_segments = len(points)
            steps_per_segment = 8
            total_steps = total_segments * steps_per_segment
            step = (seq - 1) % total_steps
            segment = step // steps_per_segment
            step_in_segment = step % steps_per_segment
            start = points[segment]
            end = points[(segment + 1) % total_segments]
            t = step_in_segment / steps_per_segment
            lat = start["lat"] + (end["lat"] - start["lat"]) * t
            lon = start["lon"] + (end["lon"] - start["lon"]) * t
        altitude = seq * 2  # 仅用于记录，不在地图上显示

        st.session_state.last_received = now
        st.session_state.current_seq = seq
        st.session_state.last_heartbeat_info = f"序号: {seq}, 时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now))}"
        st.session_state.timeout_flag = False

        with history_lock:
            history.append((now, seq))
        with positions_lock:
            positions.append((lat, lon, altitude, seq))

# 启动后台线程（只启动一次）
if "thread_started" not in st.session_state:
    thread = threading.Thread(target=heartbeat_sender, daemon=True)
    thread.start()
    st.session_state.thread_started = True

# ----------------------------- 页面布局 -----------------------------
st.title("🚁 无人机心跳监控 + 高德地图动态航线")
st.markdown("在侧边栏编辑路径点，无人机按顺序循环飞行。地图使用高德底图，显示路径、地名和实时位置。")

# 侧边栏：路径点编辑器
st.sidebar.subheader("🗺️ 编辑飞行路径")
with st.sidebar.form("edit_points"):
    edited_points = []
    for i, p in enumerate(st.session_state.points):
        col1, col2, col3, col4 = st.columns([3, 2, 2, 1])
        with col1:
            name = st.text_input("地名", value=p["name"], key=f"name_{i}")
        with col2:
            lat = st.number_input("纬度", value=p["lat"], format="%.6f", key=f"lat_{i}")
        with col3:
            lon = st.number_input("经度", value=p["lon"], format="%.6f", key=f"lon_{i}")
        with col4:
            delete = st.checkbox("删除", key=f"del_{i}")
        if not delete:
            edited_points.append({"name": name, "lat": lat, "lon": lon})
    # 添加新点
    col1, col2 = st.columns(2)
    with col1:
        new_name = st.text_input("新增地名", placeholder="新点名称", key="new_name")
    with col2:
        new_lat = st.number_input("新增纬度", value=32.2325, format="%.6f", key="new_lat")
        new_lon = st.number_input("新增经度", value=118.7855, format="%.6f", key="new_lon")
    if st.form_submit_button("保存航线"):
        if new_name.strip():
            edited_points.append({"name": new_name, "lat": new_lat, "lon": new_lon})
        if len(edited_points) >= 2:
            st.session_state.points = edited_points
            st.success("航线已更新！")
        else:
            st.error("至少需要两个点才能构成路径。")

# 显示当前路径顺序
st.sidebar.subheader("📌 当前航线顺序")
for idx, p in enumerate(st.session_state.points):
    st.sidebar.write(f"{idx+1}. {p['name']} ({p['lat']:.6f}, {p['lon']:.6f})")

# 实时显示区
placeholder = st.empty()
chart_placeholder = st.empty()
map_placeholder = st.empty()

# ----------------------------- 主循环（每秒刷新） -----------------------------
while True:
    now = time.time()
    last = st.session_state.last_received
    delta = now - last
    delta_int = int(round(delta))

    if delta > 3 and not st.session_state.timeout_flag:
        st.session_state.timeout_flag = True

    with placeholder.container():
        col1, col2 = st.columns(2)
        with col1:
            st.metric("最新心跳", st.session_state.last_heartbeat_info)
        with col2:
            st.metric("距上次心跳", f"{delta_int} 秒")
        if st.session_state.timeout_flag:
            st.error("⚠️ 连接超时！超过 3 秒未收到心跳。")
        else:
            st.success("✅ 连接正常")

    # 心跳折线图
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

    # 高德地图（通过 folium）
    with positions_lock:
        if positions:
            pos_df = pd.DataFrame(positions, columns=['lat', 'lon', 'altitude', 'seq'])
            current_pos = pos_df.iloc[-1]
            # 创建地图，初始中心为当前位置
            # 高德瓦片 URL (普通街道图)
            tile_url = "http://webrd02.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}"
           m = folium.Map(location=[current_pos['lat'], current_pos['lon']], zoom_start=18, tiles="CartoDB positron")
            
            # 1. 绘制规划路径（青色闭合线）
            points_coords = [[p["lat"], p["lon"]] for p in st.session_state.points]
            # 闭合路径
            closed_coords = points_coords + [points_coords[0]]
            folium.PolyLine(closed_coords, color="cyan", weight=3, opacity=0.8).add_to(m)
            
            # 2. 无人机实际轨迹（黄色线）
            if len(pos_df) > 1:
                track_coords = pos_df[['lat', 'lon']].values.tolist()
                folium.PolyLine(track_coords, color="yellow", weight=2).add_to(m)
            
            # 3. 路径点标注（带 Popup）
            for p in st.session_state.points:
                folium.Marker(
                    location=[p["lat"], p["lon"]],
                    popup=folium.Popup(p["name"], max_width=200),
                    icon=folium.Icon(color="blue", icon="info-sign")
                ).add_to(m)
            
            # 4. 无人机当前位置（红色圆点）
            folium.CircleMarker(
                location=[current_pos['lat'], current_pos['lon']],
                radius=8,
                color="red",
                fill=True,
                fill_color="red",
                fill_opacity=0.8,
                popup=f"心跳序号: {current_pos['seq']}<br>海拔: {current_pos['altitude']} m"
            ).add_to(m)
            
            # 显示地图
            st_folium(m, width=800, height=500, key="map")
        else:
            map_placeholder.info("等待位置数据...")

    time.sleep(1)
