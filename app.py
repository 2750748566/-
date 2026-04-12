import streamlit as st
import time
import threading
import pandas as pd
import math
from collections import deque
import datetime
import matplotlib.pyplot as plt
import folium
from streamlit_folium import st_folium

# ================== 坐标转换（已提供，保留用于其他用途，但本系统主要使用GCJ-02直接输入） ==================
def out_of_china(lng, lat):
    return not (72.004 <= lng <= 137.8347 and 0.8293 <= lat <= 55.8271)

# ================== 全局数据存储 ==================
history_heartbeat = deque(maxlen=200)      # (timestamp, seq)
history_position = deque(maxlen=200)       # (gcj_lng, gcj_lat)
history_lock = threading.Lock()

# 共享状态（线程安全）
shared_state = {
    'last_heartbeat_time': time.time(),
    'last_seq': 0,
    'timeout_flag': False,
    'running': True,
    # 无人机当前位置（GCJ-02）
    'current_lng': 118.749,      # 南京科技职业学院附近（示例坐标）
    'current_lat': 32.2323,
    # A、B点坐标（GCJ-02）
    'A_lng': 118.749,
    'A_lat': 32.2323,
    'B_lng': 118.749,
    'B_lat': 32.2344,
    'flight_height': 10,          # 飞行高度（米）
    # 飞行参数
    'progress': 0.0,              # 0~1 表示从A到B的进度
    'direction': 1,               # 1: A->B, -1: B->A
    'speed': 0.01                 # 每秒增加的进度（单程100秒）
}
state_lock = threading.Lock()

# ================== 后台线程：心跳发送 + 位置更新（基于A、B点往返） ==================
def update_position_from_progress():
    """根据当前进度计算无人机位置（GCJ-02直线插值）"""
    with state_lock:
        A_lng, A_lat = shared_state['A_lng'], shared_state['A_lat']
        B_lng, B_lat = shared_state['B_lng'], shared_state['B_lat']
        progress = shared_state['progress']
    lng = A_lng + (B_lng - A_lng) * progress
    lat = A_lat + (B_lat - A_lat) * progress
    return lng, lat

def background_worker():
    seq = 0
    while shared_state['running']:
        time.sleep(1)
        seq += 1
        now = time.time()

        # 1. 更新飞行进度（往返）
        with state_lock:
            progress = shared_state['progress']
            direction = shared_state['direction']
            speed = shared_state['speed']
            new_progress = progress + direction * speed
            # 边界反转
            if new_progress >= 1.0:
                new_progress = 1.0 - (new_progress - 1.0)
                shared_state['direction'] = -1
            elif new_progress <= 0.0:
                new_progress = -new_progress
                shared_state['direction'] = 1
            shared_state['progress'] = max(0.0, min(1.0, new_progress))

        # 2. 计算当前无人机位置（GCJ-02）
        cur_lng, cur_lat = update_position_from_progress()

        # 3. 更新共享状态（心跳信息 + 当前位置）
        with state_lock:
            shared_state['last_heartbeat_time'] = now
            shared_state['last_seq'] = seq
            shared_state['current_lng'] = cur_lng
            shared_state['current_lat'] = cur_lat
            shared_state['timeout_flag'] = False

        # 4. 记录历史数据（用于曲线和轨迹）
        with history_lock:
            history_heartbeat.append((now, seq))
            history_position.append((cur_lng, cur_lat))

# ================== Streamlit 界面 ==================
def main():
    st.set_page_config(page_title="无人机监控系统 - 南京科院", layout="wide")
    st.title("🚁 无人机心跳与轨迹监控")
    st.markdown("模拟心跳自收自发（每秒1次），3秒未收到则报警；卫星地图基于高德，坐标使用GCJ-02")

    # 启动后台线程（仅一次）
    if 'worker_started' not in st.session_state:
        st.session_state.worker_started = True
        thread = threading.Thread(target=background_worker, daemon=True)
        thread.start()

    # ================== 侧边栏控制面板 ==================
    st.sidebar.header("🎮 控制面板")
    st.sidebar.subheader("📍 起点 A (GCJ-02)")
    colA1, colA2 = st.sidebar.columns(2)
    with colA1:
        new_A_lat = st.number_input("纬度", value=32.2323, format="%.6f", key="A_lat")
    with colA2:
        new_A_lng = st.number_input("经度", value=118.749, format="%.6f", key="A_lng")
    if st.sidebar.button("设置 A 点"):
        with state_lock:
            shared_state['A_lng'] = new_A_lng
            shared_state['A_lat'] = new_A_lat
            # 重置进度，避免跳跃
            shared_state['progress'] = 0.0
            shared_state['direction'] = 1
        st.sidebar.success("A点已更新")

    st.sidebar.subheader("📍 终点 B (GCJ-02)")
    colB1, colB2 = st.sidebar.columns(2)
    with colB1:
        new_B_lat = st.number_input("纬度", value=32.2344, format="%.6f", key="B_lat")
    with colB2:
        new_B_lng = st.number_input("经度", value=118.749, format="%.6f", key="B_lng")
    if st.sidebar.button("设置 B 点"):
        with state_lock:
            shared_state['B_lng'] = new_B_lng
            shared_state['B_lat'] = new_B_lat
            shared_state['progress'] = 0.0
            shared_state['direction'] = 1
        st.sidebar.success("B点已更新")

    st.sidebar.subheader("✈️ 飞行参数")
    new_height = st.sidebar.number_input("飞行高度 (m)", value=10, step=1, key="height")
    if st.sidebar.button("设置高度"):
        with state_lock:
            shared_state['flight_height'] = new_height
        st.sidebar.success(f"高度设为 {new_height} m")

    # 实时状态显示区域（侧边栏下方）
    st.sidebar.markdown("---")
    st.sidebar.subheader("📡 实时状态")
    local_time_placeholder = st.sidebar.empty()
    heartbeat_info_placeholder = st.sidebar.empty()
    timeout_placeholder = st.sidebar.empty()
    flight_info_placeholder = st.sidebar.empty()

    # ================== 主区域：心跳图 + 地图 ==================
    col1, col2 = st.columns([2, 3])
    with col1:
        st.subheader("💓 心跳时序图")
        chart_placeholder = st.empty()
    with col2:
        st.subheader("🗺️ 卫星地图 (高德)")
        map_placeholder = st.empty()

    # 主循环：每秒刷新
    while True:
        # 获取最新状态
        with state_lock:
            last_time = shared_state['last_heartbeat_time']
            seq = shared_state['last_seq']
            timeout_flag = shared_state['timeout_flag']
            cur_lng = shared_state['current_lng']
            cur_lat = shared_state['current_lat']
            A_lng, A_lat = shared_state['A_lng'], shared_state['A_lat']
            B_lng, B_lat = shared_state['B_lng'], shared_state['B_lat']
            progress = shared_state['progress']
            height = shared_state['flight_height']

        now = time.time()
        delta = now - last_time
        delta_int = int(round(delta))

        # 超时检测
        if delta > 3 and not timeout_flag:
            with state_lock:
                shared_state['timeout_flag'] = True

        # 更新侧边栏
        local_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        local_time_placeholder.metric("🕒 本地时间", local_time)
        heartbeat_info_placeholder.metric(
            "最新心跳",
            f"序号 {seq}  @ {time.strftime('%H:%M:%S', time.localtime(last_time))}"
        )
        if timeout_flag or delta > 3:
            timeout_placeholder.error(f"⚠️ 连接超时！ 已 {delta_int} 秒未收到心跳")
        else:
            timeout_placeholder.success(f"✅ 连接正常  距上次心跳 {delta_int} 秒")
        flight_info_placeholder.metric("飞行进度", f"{progress*100:.1f}%  (A→B往返)")

        # 1. 绘制心跳曲线（横轴精确到秒）
        with history_lock:
            if history_heartbeat:
                df = pd.DataFrame(history_heartbeat, columns=['timestamp', 'seq'])
                df['time_str'] = pd.to_datetime(df['timestamp'], unit='s').dt.strftime('%H:%M:%S')
                fig, ax = plt.subplots(figsize=(6, 3))
                ax.plot(df['time_str'], df['seq'], marker='o', markersize=3, linewidth=1.5, color='#1f77b4')
                ax.set_xlabel('时间 (时:分:秒)')
                ax.set_ylabel('心跳包序号')
                ax.set_title('心跳包数量变化')
                plt.xticks(rotation=45, ha='right', fontsize=8)
                plt.tight_layout()
                chart_placeholder.pyplot(fig)
                plt.close(fig)
            else:
                chart_placeholder.info("等待心跳数据...")

        # 2. 绘制地图（高德卫星图 + A/B点标记 + 无人机 + 历史轨迹）
        # 地图中心为无人机当前位置
        m = folium.Map(
            location=[cur_lat, cur_lng],
            zoom_start=17,
            control_scale=True,
            tiles=None
        )
        # 高德卫星影像（style=6）
        folium.TileLayer(
            tiles='https://webst01.is.autonavi.com/appmaptile?style=6&x={x}&y={y}&z={z}',
            attr='高德地图',
            name='高德卫星图',
            overlay=False,
            control=True
        ).add_to(m)

        # 添加起点 A 标记（绿色）
        folium.Marker(
            location=[A_lat, A_lng],
            popup=f"起点 A<br>GCJ-02: {A_lng:.6f}, {A_lat:.6f}",
            icon=folium.Icon(color='green', icon='play', prefix='fa')
        ).add_to(m)

        # 添加终点 B 标记（橙色）
        folium.Marker(
            location=[B_lat, B_lng],
            popup=f"终点 B<br>GCJ-02: {B_lng:.6f}, {B_lat:.6f}",
            icon=folium.Icon(color='orange', icon='flag-checkered', prefix='fa')
        ).add_to(m)

        # 绘制AB连线（航线）
        folium.PolyLine(
            locations=[[A_lat, A_lng], [B_lat, B_lng]],
            color='gray',
            weight=2,
            opacity=0.6,
            dash_array='5,5'
        ).add_to(m)

        # 绘制历史轨迹（蓝色）
        with history_lock:
            if len(history_position) >= 2:
                points = [[lat, lng] for lng, lat in history_position]
                folium.PolyLine(points, color='blue', weight=3, opacity=0.7).add_to(m)

        # 绘制当前位置（红色飞机）
        folium.Marker(
            location=[cur_lat, cur_lng],
            popup=f"无人机<br>序号: {seq}<br>高度: {height}m<br>进度: {progress*100:.1f}%",
            icon=folium.Icon(color='red', icon='plane', prefix='fa')
        ).add_to(m)

        # 可选：显示一个圆形指示范围
        folium.Circle(
            radius=20,
            location=[cur_lat, cur_lng],
            color='red',
            fill=True,
            fill_opacity=0.2
        ).add_to(m)

        # 渲染地图
        with map_placeholder.container():
            st_folium(m, width=650, height=500, key="drone_map")

        time.sleep(1)

if __name__ == "__main__":
    main()
