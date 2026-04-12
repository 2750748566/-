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

# ================== 坐标转换（WGS-84 转 GCJ-02） ==================
def out_of_china(lng, lat):
    return not (72.004 <= lng <= 137.8347 and 0.8293 <= lat <= 55.8271)

def transform_lat(x, y):
    ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(y / 12.0 * math.pi) + 320 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
    return ret

def transform_lon(x, y):
    ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
    return ret

def wgs84_to_gcj02(lng, lat):
    a = 6378245.0
    ee = 0.00669342162296594323
    if out_of_china(lng, lat):
        return lng, lat
    dlat = transform_lat(lng - 105.0, lat - 35.0)
    dlng = transform_lon(lng - 105.0, lat - 35.0)
    radlat = lat / 180.0 * math.pi
    magic = math.sin(radlat)
    magic = 1 - ee * magic * magic
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((a * (1 - ee)) / (magic * sqrtmagic) * math.pi)
    dlng = (dlng * 180.0) / (a / sqrtmagic * math.cos(radlat) * math.pi)
    mg_lat = lat + dlat
    mg_lng = lng + dlng
    return mg_lng, mg_lat

# ================== 全局数据存储 ==================
history_heartbeat = deque(maxlen=200)      # (timestamp, seq)
history_position = deque(maxlen=200)       # (gcj_lng, gcj_lat)
history_lock = threading.Lock()

shared_state = {
    'last_heartbeat_time': time.time(),
    'last_seq': 0,
    'timeout_flag': False,
    'running': True,
    # 当前位置（GCJ-02）
    'current_lng': 118.749,
    'current_lat': 32.2323,
    # 起点 A（GCJ-02）
    'A_lng': 118.749,
    'A_lat': 32.2323,
    # 终点 B（GCJ-02）
    'B_lng': 118.749,
    'B_lat': 32.2344,
    'flight_height': 10,
    'progress': 0.0,        # 0~1
    'direction': 1,          # 1: A->B, -1: B->A
    'speed': 0.01            # 每秒进度变化
}
state_lock = threading.Lock()

# ================== 辅助函数 ==================
def update_position_from_progress():
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

        # 更新往返进度
        with state_lock:
            progress = shared_state['progress']
            direction = shared_state['direction']
            speed = shared_state['speed']
            new_progress = progress + direction * speed
            if new_progress >= 1.0:
                new_progress = 1.0 - (new_progress - 1.0)
                shared_state['direction'] = -1
            elif new_progress <= 0.0:
                new_progress = -new_progress
                shared_state['direction'] = 1
            shared_state['progress'] = max(0.0, min(1.0, new_progress))

        cur_lng, cur_lat = update_position_from_progress()

        with state_lock:
            shared_state['last_heartbeat_time'] = now
            shared_state['last_seq'] = seq
            shared_state['current_lng'] = cur_lng
            shared_state['current_lat'] = cur_lat
            shared_state['timeout_flag'] = False

        with history_lock:
            history_heartbeat.append((now, seq))
            history_position.append((cur_lng, cur_lat))

# ================== Streamlit 主界面 ==================
def main():
    st.set_page_config(page_title="无人机监控系统 - 南京科院", layout="wide")
    st.title("🚁 无人机心跳与轨迹监控")
    st.markdown("模拟心跳自收自发（每秒1次），3秒未收到则报警；卫星地图基于高德，坐标使用GCJ-02")

    # 启动后台线程（仅一次）
    if 'worker_started' not in st.session_state:
        st.session_state.worker_started = True
        thread = threading.Thread(target=background_worker, daemon=True)
        thread.start()

    # ========== 侧边栏控制面板 ==========
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

    st.sidebar.markdown("---")
    st.sidebar.subheader("📡 实时状态")
    local_time_placeholder = st.sidebar.empty()
    heartbeat_info_placeholder = st.sidebar.empty()
    timeout_placeholder = st.sidebar.empty()
    flight_info_placeholder = st.sidebar.empty()

    # ========== 主区域布局 ==========
    col1, col2 = st.columns([2, 3])
    with col1:
        st.subheader("💓 心跳时序图")
        chart_placeholder = st.empty()
    with col2:
        st.subheader("🗺️ 卫星地图 (高德)")
        map_placeholder = st.empty()

    # 循环控制
    iteration = 0
    last_map_update = 0
    map_update_interval = 2   # 地图每2秒刷新一次，避免闪烁

    while True:
        iteration += 1
        now = time.time()

        # 获取最新状态（线程安全）
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

        delta = now - last_time
        delta_int = int(round(delta))

        # 超时检测
        if delta > 3 and not timeout_flag:
            with state_lock:
                shared_state['timeout_flag'] = True

        # 更新侧边栏（每秒）
        local_time_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        local_time_placeholder.metric("🕒 本地时间", local_time_str)
        heartbeat_info_placeholder.metric(
            "最新心跳",
            f"序号 {seq}  @ {time.strftime('%H:%M:%S', time.localtime(last_time))}"
        )
        if timeout_flag or delta > 3:
            timeout_placeholder.error(f"⚠️ 连接超时！ 已 {delta_int} 秒未收到心跳")
        else:
            timeout_placeholder.success(f"✅ 连接正常  距上次心跳 {delta_int} 秒")
        flight_info_placeholder.metric("飞行进度", f"{progress*100:.1f}%  (A→B往返)")

        # 更新心跳曲线图（每秒）
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

        # 更新地图（降低频率）
        if now - last_map_update >= map_update_interval:
            # 创建地图
            m = folium.Map(location=[cur_lat, cur_lng], zoom_start=17, control_scale=True, tiles=None)
            # 高德卫星影像
            folium.TileLayer(
                tiles='https://webst01.is.autonavi.com/appmaptile?style=6&x={x}&y={y}&z={z}',
                attr='高德地图',
                name='高德卫星图',
                overlay=False,
                control=True
            ).add_to(m)

            # 起点 A
            folium.Marker(
                location=[A_lat, A_lng],
                popup=f"起点 A<br>GCJ-02: {A_lng:.6f}, {A_lat:.6f}",
                icon=folium.Icon(color='green', icon='play', prefix='fa')
            ).add_to(m)

            # 终点 B
            folium.Marker(
                location=[B_lat, B_lng],
                popup=f"终点 B<br>GCJ-02: {B_lng:.6f}, {B_lat:.6f}",
                icon=folium.Icon(color='orange', icon='flag-checkered', prefix='fa')
            ).add_to(m)

            # AB 连线（虚线）
            folium.PolyLine(
                locations=[[A_lat, A_lng], [B_lat, B_lng]],
                color='gray', weight=2, opacity=0.6, dash_array='5,5'
            ).add_to(m)

            # 历史轨迹（蓝色实线）
            with history_lock:
                if len(history_position) >= 2:
                    points = [[lat, lng] for lng, lat in history_position]
                    folium.PolyLine(points, color='blue', weight=3, opacity=0.7).add_to(m)

            # 无人机当前位置（红色飞机）
            folium.Marker(
                location=[cur_lat, cur_lng],
                popup=f"无人机<br>序号: {seq}<br>高度: {height}m<br>进度: {progress*100:.1f}%",
                icon=folium.Icon(color='red', icon='plane', prefix='fa')
            ).add_to(m)

            # 圆形指示范围
            folium.Circle(
                radius=20,
                location=[cur_lat, cur_lng],
                color='red',
                fill=True,
                fill_opacity=0.2
            ).add_to(m)

            # 使用动态 key 避免重复
            map_key = f"drone_map_{iteration}_{int(now*1000)}"
            with map_placeholder.container():
                st_folium(m, width=650, height=500, key=map_key)

            last_map_update = now
        else:
            # 不更新地图，仅等待
            pass

        time.sleep(1)

if __name__ == "__main__":
    main()
