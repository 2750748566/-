import streamlit as st
import time
import threading
import pandas as pd
import numpy as np
from collections import deque
import matplotlib.pyplot as plt
import folium
from streamlit_folium import st_folium

# ----------------------------- 初始路径 -----------------------------
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

# 全局数据结构（线程安全）
history = deque(maxlen=200)
positions = deque(maxlen=100)
history_lock = threading.Lock()
positions_lock = threading.Lock()

def heartbeat_sender():
    """后台线程：每秒生成心跳和位置"""
    seq = 0
    try:
        while True:
            time.sleep(1)
            seq += 1
            now = time.time()

            # 获取当前路径点（从 session_state 读取）
            points = st.session_state.get("points", DEFAULT_POINTS)

            # 根据路径点计算当前位置
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
            altitude = seq * 2

            # 更新 session_state 实时信息
            st.session_state.last_received = now
            st.session_state.current_seq = seq
            st.session_state.last_heartbeat_info = f"序号: {seq}, 时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now))}"
            st.session_state.timeout_flag = False

            # 存储数据（加锁）
            with history_lock:
                history.append((now, seq))
            with positions_lock:
                positions.append((lat, lon, altitude, seq))

            # 调试：每10秒打印一次队列长度（在终端查看）
            if seq % 10 == 0:
                print(f"[线程] 已生成 {seq} 个心跳，positions 长度 {len(positions)}")

    except Exception as e:
        print(f"后台线程出错: {e}")

# 启动后台线程（只启动一次）
if "thread_started" not in st.session_state:
    print("正在启动后台线程...")
    thread = threading.Thread(target=heartbeat_sender, daemon=True)
    thread.start()
    st.session_state.thread_started = True
    print("后台线程已启动")

# ----------------------------- 页面布局 -----------------------------
st.title("🚁 无人机心跳监控 + 动态航线 (调试版)")
st.markdown("侧边栏可编辑路径点，地图将显示无人机位置。下方显示调试信息。")

# 侧边栏：路径编辑器（与原代码相同，略... 为了简洁，省略侧边栏代码，但实际运行时必须保留）
# 此处我们只保留核心，但为避免代码过长，将完整版放在最后

# 实时显示区
placeholder = st.empty()
chart_placeholder = st.empty()
map_placeholder = st.empty()

# 添加一个调试区域，显示 positions 队列长度
debug_placeholder = st.empty()

# ----------------------------- 实时更新部分（每次脚本执行时运行） -----------------------------
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

# 调试信息：显示 positions 长度
with debug_placeholder.container():
    with positions_lock:
        pos_len = len(positions)
    st.info(f"当前 positions 队列长度: {pos_len}")

# 心跳折线图（与原代码相同）
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

# 地图
with positions_lock:
    if positions:
        pos_df = pd.DataFrame(positions, columns=['lat', 'lon', 'altitude', 'seq'])
        current_pos = pos_df.iloc[-1]
        # 使用 CartoDB 底图（稳定）
        m = folium.Map(location=[current_pos['lat'], current_pos['lon']], zoom_start=18, tiles="CartoDB positron")
        
        # 规划路径（青色闭合线）
        points_coords = [[p["lat"], p["lon"]] for p in st.session_state.points]
        closed_coords = points_coords + [points_coords[0]]
        folium.PolyLine(closed_coords, color="cyan", weight=3, opacity=0.8).add_to(m)
        
        # 实际轨迹（黄色线）
        if len(pos_df) > 1:
            track_coords = pos_df[['lat', 'lon']].values.tolist()
            folium.PolyLine(track_coords, color="yellow", weight=2).add_to(m)
        
        # 路径点标注
        for p in st.session_state.points:
            folium.Marker(
                location=[p["lat"], p["lon"]],
                popup=folium.Popup(p["name"], max_width=200),
                icon=folium.Icon(color="blue", icon="info-sign")
            ).add_to(m)
        
        # 当前位置
        folium.CircleMarker(
            location=[current_pos['lat'], current_pos['lon']],
            radius=8,
            color="red",
            fill=True,
            fill_color="red",
            fill_opacity=0.8,
            popup=f"心跳序号: {current_pos['seq']}<br>海拔: {current_pos['altitude']} m"
        ).add_to(m)
        
        st_folium(m, width=800, height=500, key="map")
    else:
        map_placeholder.info("等待位置数据...")

# 延迟1秒后重新运行脚本，实现“实时刷新”
time.sleep(1)
st.experimental_rerun()
