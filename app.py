import streamlit as st
import time
import math
import threading
from datetime import datetime
import pandas as pd
import matplotlib.pyplot as plt
import folium
from streamlit_folium import folium_static

# ------------------------------- 配置 ---------------------------------
SCHOOL_CENTER = [118.749413, 32.234097]
GAODE_TILE = "https://webst01.is.autonavi.com/appmaptile?style=6&x={x}&y={y}&z={z}"
HEARTBEAT_INTERVAL = 0.2
BASE_SPEED = 5.0

# ------------------------------- 心跳模拟器 ---------------------------------
class HeartbeatData:
    def __init__(self, flight_time, seq, lat, lng, altitude):
        self.flight_time = flight_time
        self.seq = seq
        self.lat = lat
        self.lng = lng
        self.altitude = altitude

class HeartbeatSim:
    def __init__(self, start_point):
        self.current_pos = start_point[:]
        self.path = [start_point[:]]
        self.path_idx = 0
        self.running = False
        self.progress = 0.0
        self.total_dist = 0.0
        self.traveled = 0.0
        self.start_time = None
        self.last_update = None
        self.history = []
        self.speed_pct = 50
        self.altitude = 50

    def set_path(self, path, altitude, speed_pct):
        self.path = path[:]
        self.path_idx = 0
        self.current_pos = path[0][:]
        self.running = True
        self.progress = 0.0
        self.traveled = 0.0
        self.start_time = datetime.now()
        self.last_update = None
        self.history = []
        self.speed_pct = speed_pct
        self.altitude = altitude
        self.total_dist = sum(math.dist(self.path[i], self.path[i+1]) for i in range(len(self.path)-1))
        # 立即生成第一个心跳（序号1）
        self._add_heartbeat(seq=1)

    def _add_heartbeat(self, seq=None):
        flight_t = (datetime.now() - self.start_time).total_seconds() if self.start_time else 0
        if seq is None:
            seq = len(self.history) + 1
        hb = HeartbeatData(flight_t, seq, self.current_pos[1], self.current_pos[0], self.altitude)
        self.history.append(hb)
        return hb

    def update_one_step(self):
        """推进一个心跳步长，返回新生成的心跳（如果没有则返回None）"""
        if not self.running:
            return None
        now = time.time()
        if self.last_update is None:
            dt = HEARTBEAT_INTERVAL
        else:
            dt = min(HEARTBEAT_INTERVAL, now - self.last_update) if (now - self.last_update) > 0 else HEARTBEAT_INTERVAL
        self.last_update = now

        start = self.path[self.path_idx]
        end = self.path[self.path_idx+1]
        seg_len = math.dist(start, end)
        speed = BASE_SPEED * (self.speed_pct / 100.0)
        move = speed * dt
        self.traveled += move
        if self.total_dist > 0:
            self.progress = min(1.0, self.traveled / self.total_dist)

        if self.traveled >= seg_len and self.traveled > 0:
            self.path_idx += 1
            self.traveled = 0
            if self.path_idx < len(self.path)-1:
                self.current_pos = self.path[self.path_idx][:]
            else:
                self.running = False
                return self._add_heartbeat()
        else:
            if seg_len > 0:
                t = max(0, min(1, self.traveled / seg_len))
                lng = start[0] + (end[0]-start[0])*t
                lat = start[1] + (end[1]-start[1])*t
                self.current_pos = [lng, lat]
        return self._add_heartbeat()

# ------------------------------- 后台线程 -------------------------------
def background_worker():
    while True:
        time.sleep(HEARTBEAT_INTERVAL)
        if st.session_state.get('flight_started', False):
            sim = st.session_state.get('sim')
            if sim and sim.running:
                new_hb = sim.update_one_step()
                if new_hb:
                    st.session_state.latest_hb = new_hb
                    st.session_state.hb_list.insert(0, new_hb)
                    if len(st.session_state.hb_list) > 200:
                        st.session_state.hb_list.pop()
                    st.session_state.flight_trail.append([new_hb.lng, new_hb.lat])
                    if len(st.session_state.flight_trail) > 200:
                        st.session_state.flight_trail.pop(0)
                    if not sim.running:
                        st.session_state.flight_started = False

# ------------------------------- 地图创建 ---------------------------------
def make_planning_map(center, points, flight_trail, plan_path, drone_pos, alt):
    m = folium.Map(location=[center[1], center[0]], zoom_start=16, tiles=GAODE_TILE, attr='高德')
    if points.get('A'):
        folium.Marker([points['A'][1], points['A'][0]], popup='起点A', icon=folium.Icon(color='green')).add_to(m)
    if points.get('B'):
        folium.Marker([points['B'][1], points['B'][0]], popup='终点B', icon=folium.Icon(color='red')).add_to(m)
    if plan_path and len(plan_path)>1:
        folium.PolyLine([[p[1],p[0]] for p in plan_path], color='green', weight=4).add_to(m)
    if flight_trail:
        folium.PolyLine([[lat,lng] for lng,lat in flight_trail[-100:]], color='orange', weight=2).add_to(m)
    if drone_pos:
        folium.Marker([drone_pos[1], drone_pos[0]], icon=folium.Icon(color='blue', icon='plane', prefix='fa')).add_to(m)
    return m

# ------------------------------- 初始化状态 -------------------------------
def init():
    defaults = {
        'page': '航线规划',
        'points': {'A': [118.746956, 32.232945], 'B': [118.751589, 32.235204]},
        'sim': HeartbeatSim([118.746956, 32.232945]),
        'flight_started': False,
        'latest_hb': None,
        'hb_list': [],
        'flight_trail': [],
        'plan_path': None,
        'flight_alt': 50,
        'drone_speed': 50,
        'bg_thread_started': False
    }
    for k,v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

    if not st.session_state.bg_thread_started:
        thread = threading.Thread(target=background_worker, daemon=True)
        thread.start()
        st.session_state.bg_thread_started = True

# ------------------------------- 主程序 -------------------------------
def main():
    st.set_page_config(layout="wide")
    st.title("🏫 南京科技职业学院 - 无人机地面站 (心跳正比例图像)")
    init()

    with st.sidebar:
        st.header("📌 导航")
        st.session_state.page = st.radio("功能页面", ["航线规划", "飞行监控"])
        st.markdown("---")
        st.subheader("📊 系统状态")
        st.checkbox("A点已设", value=st.session_state.points.get('A') is not None, disabled=True)
        st.checkbox("B点已设", value=st.session_state.points.get('B') is not None, disabled=True)
        st.checkbox("飞行进行中", value=st.session_state.flight_started, disabled=True)

    if st.session_state.page == "航线规划":
        st.header("🗺️ 航线规划")
        col_map, col_panel = st.columns([3, 1.2])

        with col_panel:
            st.markdown("### 🎮 控制面板")
            st.markdown("#### 📍 起点 A")
            col_a1, col_a2 = st.columns(2)
            with col_a1:
                a_lat = st.number_input("纬度", value=st.session_state.points['A'][1], format="%.6f", key="a_lat")
            with col_a2:
                a_lng = st.number_input("经度", value=st.session_state.points['A'][0], format="%.6f", key="a_lng")
            if st.button("设置 A 点", use_container_width=True):
                st.session_state.points['A'] = [a_lng, a_lat]
                st.session_state.plan_path = [st.session_state.points['A'], st.session_state.points['B']]
                st.rerun()

            st.markdown("#### 📍 终点 B")
            col_b1, col_b2 = st.columns(2)
            with col_b1:
                b_lat = st.number_input("纬度", value=st.session_state.points['B'][1], format="%.6f", key="b_lat")
            with col_b2:
                b_lng = st.number_input("经度", value=st.session_state.points['B'][0], format="%.6f", key="b_lng")
            if st.button("设置 B 点", use_container_width=True):
                st.session_state.points['B'] = [b_lng, b_lat]
                st.session_state.plan_path = [st.session_state.points['A'], st.session_state.points['B']]
                st.rerun()

            st.markdown("---")
            st.subheader("✈️ 飞行参数")
            new_alt = st.slider("飞行高度 (m)", 10, 200, st.session_state.flight_alt, 5)
            if new_alt != st.session_state.flight_alt:
                st.session_state.flight_alt = new_alt
                st.rerun()
            new_speed = st.slider("速度系数 (%)", 10, 100, st.session_state.drone_speed, 5)
            st.session_state.drone_speed = new_speed

            st.markdown("---")
            col_start, col_stop = st.columns(2)
            with col_start:
                if st.button("▶️ 开始飞行", type="primary", use_container_width=True):
                    a = st.session_state.points.get('A')
                    b = st.session_state.points.get('B')
                    if a and b:
                        path = [a, b]
                        # 创建新的模拟器并启动
                        st.session_state.sim = HeartbeatSim(a.copy())
                        st.session_state.sim.set_path(path, st.session_state.flight_alt, st.session_state.drone_speed)
                        st.session_state.latest_hb = st.session_state.sim.history[-1] if st.session_state.sim.history else None
                        st.session_state.hb_list = [st.session_state.latest_hb] if st.session_state.latest_hb else []
                        st.session_state.flight_trail = [[st.session_state.latest_hb.lng, st.session_state.latest_hb.lat]] if st.session_state.latest_hb else []
                        st.session_state.flight_started = True
                        st.success("飞行已开始，切换至「飞行监控」查看心跳图像")
                        st.rerun()
                    else:
                        st.error("请先设置起点和终点")
            with col_stop:
                if st.button("⏹️ 停止飞行", use_container_width=True):
                    st.session_state.flight_started = False
                    if st.session_state.sim:
                        st.session_state.sim.running = False
                    st.info("飞行已停止")
                    st.rerun()

            if st.session_state.flight_started and st.session_state.latest_hb:
                hb = st.session_state.latest_hb
                st.metric("实时进度", f"{st.session_state.sim.progress*100:.1f}%")
                st.metric("当前心跳序号", hb.seq)

        with col_map:
            if st.session_state.plan_path is None and st.session_state.points.get('A') and st.session_state.points.get('B'):
                st.session_state.plan_path = [st.session_state.points['A'], st.session_state.points['B']]
            drone_pos = None
            if st.session_state.flight_started and st.session_state.latest_hb:
                drone_pos = [st.session_state.latest_hb.lng, st.session_state.latest_hb.lat]
            m = make_planning_map(SCHOOL_CENTER, st.session_state.points, st.session_state.flight_trail,
                                  st.session_state.plan_path, drone_pos, st.session_state.flight_alt)
            folium_static(m, width=700, height=550)

    else:  # 飞行监控页面
        st.header("📡 飞行监控 - 实时心跳包")
        # 添加自动刷新，每秒刷新页面以显示最新数据
        from streamlit_autorefresh import st_autorefresh
        st_autorefresh(interval=1000, key="monitor_auto")

        if not st.session_state.flight_started:
            st.info("⏳ 飞行未开始。请切换到「航线规划」页面，设置起点终点后点击「开始飞行」。")
            st.stop()

        if st.session_state.latest_hb is None:
            st.warning("等待第一个心跳...")
            st.stop()

        hb = st.session_state.latest_hb
        progress_val = st.session_state.sim.progress if hasattr(st.session_state.sim, 'progress') else 0.0
        st.progress(progress_val, text=f"飞行进度：{progress_val*100:.1f}%")

        col1, col2, col3, col4 = st.columns(4)
        with col1: st.metric("飞行时间", f"{hb.flight_time:.1f} s")
        with col2: st.metric("当前心跳序号", hb.seq)
        with col3: st.metric("飞行高度", f"{st.session_state.flight_alt} m")
        with col4: st.metric("速度系数", f"{st.session_state.drone_speed}%")

        st.markdown("---")
        st.subheader("💓 心跳序号 vs 飞行时间 (正比例关系)")

        history = st.session_state.sim.history
        if len(history) >= 2:
            times = [h.flight_time for h in history]
            seqs = [h.seq for h in history]
            fig, ax = plt.subplots(figsize=(8, 5))
            ax.plot(times, seqs, marker='o', markersize=4, linewidth=2, color='#1f77b4')
            ax.set_xlabel('飞行时间 (秒)', fontsize=12)
            ax.set_ylabel('心跳包序号', fontsize=12)
            ax.set_title('心跳序号与飞行时间关系（正比例）', fontsize=14)
            ax.grid(True, linestyle='--', alpha=0.6)
            st.pyplot(fig)
            plt.close(fig)
        else:
            st.info(f"等待更多心跳数据... (当前 {len(history)} 个)")

        st.markdown("---")
        st.subheader("📈 实时趋势")
        if len(st.session_state.hb_list) > 1:
            df = pd.DataFrame([{"时间": i, "高度": h.altitude} for i, h in enumerate(st.session_state.hb_list[:50])])
            st.line_chart(df, x="时间", y="高度")
        else:
            st.info("等待更多数据...")

if __name__ == "__main__":
    main()
