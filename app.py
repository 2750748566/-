import streamlit as st
import folium
from streamlit_folium import folium_static
import time
import threading
import random
import math
from datetime import datetime
from typing import List, Dict, Optional, Tuple
import pandas as pd
import matplotlib.pyplot as plt

# ==================== 配置常量 ====================
SCHOOL_CENTER_GCJ = [118.749413, 32.234097]  # 南京科技职业学院中心 (GCJ-02)
DEFAULT_A_GCJ = [118.746956, 32.232945]      # 默认起点 A (GCJ-02)
DEFAULT_B_GCJ = [118.751589, 32.235204]      # 默认终点 B (GCJ-02)

# 高德卫星地图瓦片 URL
GAODE_SATELLITE_URL = "https://webst01.is.autonavi.com/appmaptile?style=6&x={x}&y={y}&z={z}"

# 心跳模拟参数
HEARTBEAT_INTERVAL = 0.2   # 心跳间隔（秒）
BASE_SPEED_MPS = 5.0       # 基础速度 (m/s)

# ==================== 坐标转换（简化版，实际可替换为精确算法） ====================
def wgs84_to_gcj02(lng, lat):
    """WGS-84 转 GCJ-02（演示用简化偏移）"""
    return lng + 0.006, lat + 0.002

def gcj02_to_wgs84(lng, lat):
    """GCJ-02 转 WGS-84（演示用）"""
    return lng - 0.006, lat - 0.002

# ==================== 几何辅助 & 避障算法 ====================
def distance(p1: List[float], p2: List[float]) -> float:
    return math.sqrt((p1[0]-p2[0])**2 + (p1[1]-p2[1])**2)

def calculate_path_length(path: List[List[float]]) -> float:
    total = 0.0
    for i in range(len(path)-1):
        total += distance(path[i], path[i+1])
    return total

def point_in_polygon(point: List[float], polygon: List[List[float]]) -> bool:
    x, y = point
    inside = False
    n = len(polygon)
    for i in range(n):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i+1)%n]
        if ((y1 > y) != (y2 > y)) and (x < (x2 - x1)*(y - y1)/(y2 - y1) + x1):
            inside = not inside
    return inside

def segments_intersect(p1, p2, p3, p4):
    def orientation(p, q, r):
        val = (q[1]-p[1])*(r[0]-q[0]) - (q[0]-p[0])*(r[1]-q[1])
        if abs(val) < 1e-10: return 0
        return 1 if val > 0 else 2
    def on_segment(p, q, r):
        return (min(p[0],r[0]) <= q[0] <= max(p[0],r[0]) and
                min(p[1],r[1]) <= q[1] <= max(p[1],r[1]))
    o1 = orientation(p1,p2,p3)
    o2 = orientation(p1,p2,p4)
    o3 = orientation(p3,p4,p1)
    o4 = orientation(p3,p4,p2)
    if o1 != o2 and o3 != o4: return True
    if o1==0 and on_segment(p1,p3,p2): return True
    if o2==0 and on_segment(p1,p4,p2): return True
    if o3==0 and on_segment(p3,p1,p4): return True
    if o4==0 and on_segment(p3,p2,p4): return True
    return False

def line_intersects_polygon(p1, p2, polygon):
    if point_in_polygon(p1, polygon) or point_in_polygon(p2, polygon):
        return True
    n = len(polygon)
    for i in range(n):
        p3, p4 = polygon[i], polygon[(i+1)%n]
        if segments_intersect(p1, p2, p3, p4):
            return True
    return False

def get_blocking_obstacles(start, end, obstacles, flight_alt):
    blocking = []
    for obs in obstacles:
        if obs.get('height',30) > flight_alt:
            coords = obs.get('polygon',[])
            if coords and line_intersects_polygon(start, end, coords):
                blocking.append(obs)
    return blocking

def meters_to_deg(meters, lat=32.23):
    lat_deg = meters / 111000
    lng_deg = meters / (111000 * math.cos(math.radians(lat)))
    return lng_deg, lat_deg

def find_avoidance_path(start, end, obstacles, flight_alt, safety_radius=5):
    blocking = get_blocking_obstacles(start, end, obstacles, flight_alt)
    if not blocking:
        return [start, end]
    max_lng, max_lat, min_lat = -float('inf'), -float('inf'), float('inf')
    for obs in blocking:
        for p in obs.get('polygon',[]):
            max_lng, max_lat, min_lat = max(max_lng,p[0]), max(max_lat,p[1]), min(min_lat,p[1])
    safe_lng, safe_lat = meters_to_deg(safety_radius*3)
    obs_h = max_lat - min_lat
    p1 = [start[0]+0.0012, max_lat + obs_h*3 + safe_lat*5 + 0.0002]
    p2 = [max_lng + obs_h*2 + safe_lng*3, p1[1]]
    return [start, p1, p2, end]

# ==================== 心跳模拟器 ====================
class HeartbeatData:
    def __init__(self, timestamp, flight_time, lat, lng, altitude, voltage, satellites, speed, progress, arrived, remaining_dist):
        self.timestamp = timestamp
        self.flight_time = flight_time
        self.lat = lat
        self.lng = lng
        self.altitude = altitude
        self.voltage = voltage
        self.satellites = satellites
        self.speed = speed
        self.progress = progress
        self.arrived = arrived
        self.remaining_distance = remaining_dist

class HeartbeatSimulator:
    def __init__(self, start_point):
        self.current_pos = start_point.copy()
        self.path = [start_point.copy()]
        self.path_idx = 0
        self.simulating = False
        self.altitude = 50
        self.speed_pct = 50
        self.progress = 0.0
        self.total_dist = 0.0
        self.traveled = 0.0
        self.safety_radius = 5
        self.start_time = None
        self.last_update = None
        self.history = []      # 存储心跳历史用于绘图

    def set_path(self, path, altitude, speed_pct, safety_radius):
        self.path = path
        self.path_idx = 0
        self.current_pos = path[0].copy()
        self.altitude = altitude
        self.speed_pct = speed_pct
        self.safety_radius = safety_radius
        self.simulating = True
        self.progress = 0.0
        self.traveled = 0.0
        self.start_time = datetime.now()
        self.last_update = None
        self.history = []
        self.total_dist = sum(distance(self.path[i], self.path[i+1]) for i in range(len(self.path)-1))
        return self._gen_heartbeat(False)

    def update(self):
        if not self.simulating or self.path_idx >= len(self.path)-1:
            if self.simulating:
                self.simulating = False
            return None
        now_t = time.time()
        if self.last_update is None:
            dt = HEARTBEAT_INTERVAL
        else:
            dt = min(0.5, now_t - self.last_update)
        self.last_update = now_t
        start = self.path[self.path_idx]
        end = self.path[self.path_idx+1]
        seg_dist = distance(start, end)
        speed_ms = BASE_SPEED_MPS * (self.speed_pct/100)
        move = speed_ms * dt
        self.traveled += move
        if self.total_dist > 0:
            self.progress = min(1.0, self.traveled/self.total_dist)
        if self.traveled >= seg_dist and self.traveled > 0:
            self.path_idx += 1
            self.traveled = 0
            if self.path_idx < len(self.path):
                self.current_pos = self.path[self.path_idx].copy()
            else:
                self.simulating = False
                return self._gen_heartbeat(True)
        else:
            if seg_dist > 0:
                t = max(0, min(1, self.traveled/seg_dist))
                lng = start[0] + (end[0]-start[0])*t
                lat = start[1] + (end[1]-start[1])*t
                self.current_pos = [lng, lat]
        return self._gen_heartbeat(False)

    def _gen_heartbeat(self, arrived):
        flight_t = (datetime.now()-self.start_time).total_seconds() if self.start_time else 0
        remain = max(0, self.total_dist - self.traveled) * 111000
        hb = HeartbeatData(
            timestamp=datetime.now().strftime("%H:%M:%S"),
            flight_time=flight_t,
            lat=self.current_pos[1],
            lng=self.current_pos[0],
            altitude=self.altitude,
            voltage=round(22.2 + random.uniform(-0.5,0.5),1),
            satellites=random.randint(8,14),
            speed=round(BASE_SPEED_MPS*(self.speed_pct/100),1),
            progress=self.progress,
            arrived=arrived,
            remaining_dist=remain
        )
        self.history.insert(0, hb)   # 最新在前
        if len(self.history) > 200: self.history.pop()
        return hb

# ==================== 后台线程 ====================
def background_worker():
    while True:
        time.sleep(HEARTBEAT_INTERVAL)
        if st.session_state.get('sim_running', False):
            hb = st.session_state.sim.update()
            if hb:
                st.session_state.latest_hb = hb
                st.session_state.hb_history.insert(0, hb)
                if len(st.session_state.hb_history) > 200:
                    st.session_state.hb_history.pop()
                st.session_state.flight_trail.append([hb.lng, hb.lat])
                if len(st.session_state.flight_trail) > 200:
                    st.session_state.flight_trail.pop(0)
                if hb.arrived:
                    st.session_state.sim_running = False

# ==================== 地图创建（航线规划使用） ====================
def create_planning_map(center, points, obstacles, flight_trail, plan_path, drone_pos, safety_r, alt):
    m = folium.Map(location=[center[1],center[0]], zoom_start=16, tiles=GAODE_SATELLITE_URL, attr='高德卫星')
    for obs in obstacles:
        coords = obs.get('polygon',[])
        h = obs.get('height',30)
        if coords and len(coords)>=3:
            color = 'red' if h>alt else 'orange'
            folium.Polygon([[c[1],c[0]] for c in coords], color=color, weight=3, fill=True, fill_color=color, fill_opacity=0.4,
                           popup=f"🚧 {obs.get('name')}\n高度:{h}m").add_to(m)
    if points.get('A'):
        folium.Marker([points['A'][1], points['A'][0]], popup='🟢 起点A', icon=folium.Icon(color='green', icon='play', prefix='fa')).add_to(m)
    if points.get('B'):
        folium.Marker([points['B'][1], points['B'][0]], popup='🔴 终点B', icon=folium.Icon(color='red', icon='flag-checkered', prefix='fa')).add_to(m)
    if plan_path and len(plan_path)>1:
        pts = [[p[1],p[0]] for p in plan_path]
        folium.PolyLine(pts, color='green', weight=5, opacity=0.8, popup='规划航线').add_to(m)
        for i,pt in enumerate(plan_path[1:-1]):
            folium.CircleMarker([pt[1],pt[0]], radius=5, color='green', fill=True, fill_color='white', popup=f'航点{i+1}').add_to(m)
    if points.get('A') and points.get('B'):
        folium.PolyLine([[points['A'][1],points['A'][0]],[points['B'][1],points['B'][0]]], color='gray', weight=2, opacity=0.5, dash_array='5,5', popup='直线').add_to(m)
    if flight_trail and len(flight_trail)>1:
        trail_pts = [[lat,lng] for lng,lat in flight_trail[-50:]]
        folium.PolyLine(trail_pts, color='orange', weight=2, opacity=0.6, popup='轨迹').add_to(m)
    if drone_pos:
        folium.Marker([drone_pos[1],drone_pos[0]], popup='✈️ 无人机', icon=folium.Icon(color='blue', icon='plane', prefix='fa')).add_to(m)
        folium.Circle(radius=safety_r, location=[drone_pos[1],drone_pos[0]], color='blue', weight=2, fill=True, fill_color='blue', fill_opacity=0.2, popup=f'安全半径{safety_r}m').add_to(m)
    return m

# ==================== 初始化 Session State ====================
def init_state():
    defaults = {
        'page': '航线规划',
        'coord_sys': 'GCJ-02',
        'points': {'A': DEFAULT_A_GCJ.copy(), 'B': DEFAULT_B_GCJ.copy()},
        'obstacles': [],
        'sim': HeartbeatSimulator(DEFAULT_A_GCJ.copy()),
        'sim_running': False,
        'latest_hb': None,
        'hb_history': [],
        'flight_trail': [],
        'plan_path': None,
        'safety_radius': 5,
        'flight_alt': 50,
        'drone_speed': 50,
    }
    for k,v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

# ==================== 主程序 ====================
def main():
    st.set_page_config(page_title="南京科技职业学院 - 无人机地面站", layout="wide")
    st.title("🏫 南京科技职业学院 - 无人机地面站系统")
    init_state()

    # 启动后台线程（仅一次）
    if 'worker_started' not in st.session_state:
        st.session_state.worker_started = True
        threading.Thread(target=background_worker, daemon=True).start()

    # ========== 侧边栏 ==========
    with st.sidebar:
        st.header("📌 导航")
        selected_page = st.radio("功能页面", ["航线规划", "飞行监控"], index=0 if st.session_state.page=="航线规划" else 1)
        st.session_state.page = selected_page

        st.markdown("---")
        st.subheader("🗺️ 坐标系设置")
        coord_choice = st.radio("输入坐标系", ["WGS-84", "GCJ-02(高德/百度)"], index=0 if st.session_state.coord_sys=="WGS-84" else 1)
        st.session_state.coord_sys = "WGS-84" if coord_choice == "WGS-84" else "GCJ-02"

        st.markdown("---")
        st.subheader("📊 系统状态")
        a_ok = st.session_state.points.get('A') is not None
        b_ok = st.session_state.points.get('B') is not None
        st.checkbox("A点已设", value=a_ok, disabled=True)
        st.checkbox("B点已设", value=b_ok, disabled=True)

        # 飞行公共参数（高度、速度、安全半径）放在侧边栏方便调整
        st.markdown("---")
        st.subheader("✈️ 飞行参数")
        new_alt = st.slider("飞行高度 (m)", 10, 200, st.session_state.flight_alt, 5)
        if new_alt != st.session_state.flight_alt:
            st.session_state.flight_alt = new_alt
            # 重新规划路径
            if st.session_state.points.get('A') and st.session_state.points.get('B'):
                st.session_state.plan_path = find_avoidance_path(
                    st.session_state.points['A'], st.session_state.points['B'],
                    st.session_state.obstacles, new_alt, st.session_state.safety_radius)
                st.rerun()
        new_speed = st.slider("速度系数 (%)", 10, 100, st.session_state.drone_speed, 5)
        st.session_state.drone_speed = new_speed
        new_rad = st.slider("安全半径 (米)", 1, 20, st.session_state.safety_radius, 1)
        st.session_state.safety_radius = new_rad

    # ========== 根据页面显示不同内容 ==========
    if st.session_state.page == "航线规划":
        st.header("🗺️ 航线规划")
        col_map, col_ctrl = st.columns([3,1])
        with col_map:
            # 自动规划路径
            if st.session_state.plan_path is None and st.session_state.points.get('A') and st.session_state.points.get('B'):
                st.session_state.plan_path = find_avoidance_path(
                    st.session_state.points['A'], st.session_state.points['B'],
                    st.session_state.obstacles, st.session_state.flight_alt, st.session_state.safety_radius)
            drone_pos = None
            if st.session_state.sim_running and st.session_state.latest_hb:
                drone_pos = [st.session_state.latest_hb.lng, st.session_state.latest_hb.lat]
            m = create_planning_map(
                SCHOOL_CENTER_GCJ, st.session_state.points, st.session_state.obstacles,
                st.session_state.flight_trail, st.session_state.plan_path, drone_pos,
                st.session_state.safety_radius, st.session_state.flight_alt)
            folium_static(m, width=800, height=550)
        with col_ctrl:
            st.subheader("🎮 飞行控制")
            # 坐标输入辅助函数（根据所选坐标系自动转换存储为GCJ-02）
            def point_input(label, default_gcj):
                if st.session_state.coord_sys == "GCJ-02":
                    lat = st.number_input(f"{label} 纬度", value=default_gcj[1], format="%.6f", key=f"{label}_lat")
                    lng = st.number_input(f"{label} 经度", value=default_gcj[0], format="%.6f", key=f"{label}_lng")
                    return [lng, lat]
                else:  # WGS-84
                    wgs_lat = st.number_input(f"{label} 纬度 (WGS-84)", value=gcj02_to_wgs84(default_gcj[0],default_gcj[1])[1], format="%.6f", key=f"{label}_wgs_lat")
                    wgs_lng = st.number_input(f"{label} 经度 (WGS-84)", value=gcj02_to_wgs84(default_gcj[0],default_gcj[1])[0], format="%.6f", key=f"{label}_wgs_lng")
                    lng_gcj, lat_gcj = wgs84_to_gcj02(wgs_lng, wgs_lat)
                    return [lng_gcj, lat_gcj]

            with st.expander("📍 起点/终点设置", expanded=True):
                a_pt = point_input("A点", DEFAULT_A_GCJ)
                if st.button("设置起点 A"):
                    st.session_state.points['A'] = a_pt
                    st.session_state.plan_path = find_avoidance_path(
                        st.session_state.points['A'], st.session_state.points['B'],
                        st.session_state.obstacles, st.session_state.flight_alt, st.session_state.safety_radius)
                    st.rerun()
                b_pt = point_input("B点", DEFAULT_B_GCJ)
                if st.button("设置终点 B"):
                    st.session_state.points['B'] = b_pt
                    st.session_state.plan_path = find_avoidance_path(
                        st.session_state.points['A'], st.session_state.points['B'],
                        st.session_state.obstacles, st.session_state.flight_alt, st.session_state.safety_radius)
                    st.rerun()

            # 航线信息
            if st.session_state.points.get('A') and st.session_state.points.get('B'):
                d = distance(st.session_state.points['A'], st.session_state.points['B']) * 111000
                st.metric("📏 直线距离", f"{d:.0f} 米")
            if st.session_state.plan_path:
                plen = calculate_path_length(st.session_state.plan_path) * 111000
                st.metric("✈️ 航线长度", f"{plen:.0f} 米")
                wpcnt = len(st.session_state.plan_path) - 2
                st.metric("🎯 绕行点数量", wpcnt)

            st.markdown("---")
            col_start, col_stop = st.columns(2)
            with col_start:
                if st.button("▶️ 开始飞行", type="primary", use_container_width=True):
                    if st.session_state.points.get('A') and st.session_state.points.get('B'):
                        path = st.session_state.plan_path or [st.session_state.points['A'], st.session_state.points['B']]
                        init_hb = st.session_state.sim.set_path(
                            path, st.session_state.flight_alt, st.session_state.drone_speed, st.session_state.safety_radius)
                        if init_hb:
                            st.session_state.latest_hb = init_hb
                            st.session_state.hb_history = [init_hb]
                            st.session_state.flight_trail = [[init_hb.lng, init_hb.lat]]
                        st.session_state.sim_running = True
                        st.success("飞行已开始")
                        st.rerun()
                    else:
                        st.error("请先设置起点和终点")
            with col_stop:
                if st.button("⏹️ 停止飞行", use_container_width=True):
                    st.session_state.sim_running = False
                    st.session_state.sim.simulating = False
                    st.info("飞行已停止")
                    st.rerun()

            # 显示实时简讯
            if st.session_state.sim_running and st.session_state.latest_hb:
                hb = st.session_state.latest_hb
                st.markdown("---")
                st.subheader("📡 实时简讯")
                st.metric("进度", f"{hb.progress*100:.1f}%")
                st.metric("速度", f"{hb.speed} m/s")
                st.metric("高度", f"{hb.altitude} m")

    else:  # 飞行监控页面
        st.header("📡 飞行监控 - 实时心跳包")
        if st.session_state.sim_running and st.session_state.latest_hb:
            hb = st.session_state.latest_hb
            # 进度条
            st.progress(hb.progress, text=f"飞行进度：{int(hb.progress*100)}%")

            # 主要指标卡片
            cols1 = st.columns(5)
            with cols1[0]: st.metric("⏰ 飞行时间", f"{hb.flight_time:.1f}s")
            with cols1[1]: st.metric("📍 当前位置", f"{hb.lat:.6f}, {hb.lng:.6f}")
            with cols1[2]: st.metric("📏 飞行高度", f"{hb.altitude} m")
            with cols1[3]: st.metric("💨 当前速度", f"{hb.speed} m/s")
            with cols1[4]: st.metric("📏 剩余距离", f"{hb.remaining_distance:.0f} m")

            cols2 = st.columns(4)
            with cols2[0]: st.metric("🔋 电池电压", f"{hb.voltage} V")
            with cols2[1]: st.metric("🛰️ 卫星数量", f"{hb.satellites} 颗")
            with cols2[2]: st.metric("🎯 任务进度", f"{int(hb.progress*100)}%")
            with cols2[3]: st.metric("🛡️ 安全半径", f"{st.session_state.safety_radius} m")

            if hb.arrived:
                st.success("🎉 无人机已到达目的地！")

            st.markdown("---")
            st.subheader("💓 心跳序号 - 时间关系图")
            # 绘制心跳序号 vs 飞行时间（秒）的正比例直线
            if len(st.session_state.sim.history) >= 2:
                # 注意 history 是最新在前，我们需要时间从小到大
                hist_list = st.session_state.sim.history[::-1]   # 从旧到新
                flight_times = [h.flight_time for h in hist_list]
                seqs = list(range(1, len(hist_list)+1))   # 序号从1开始递增
                fig, ax = plt.subplots(figsize=(8,4))
                ax.plot(flight_times, seqs, marker='o', markersize=3, linewidth=2, color='#1f77b4')
                ax.set_xlabel('飞行时间 (秒)')
                ax.set_ylabel('心跳包序号')
                ax.set_title('心跳序号与飞行时间关系（正比例）')
                ax.grid(True, linestyle='--', alpha=0.6)
                st.pyplot(fig)
                plt.close(fig)
            else:
                st.info("等待心跳数据...")

            st.markdown("---")
            st.subheader("📈 实时数据图表")
            if len(st.session_state.hb_history) > 1:
                col_ch1, col_ch2 = st.columns(2)
                with col_ch1:
                    alt_df = pd.DataFrame([{"时间": i, "高度(m)": h.altitude} for i, h in enumerate(st.session_state.hb_history[:50])])
                    st.line_chart(alt_df, x="时间", y="高度(m)")
                with col_ch2:
                    spd_df = pd.DataFrame([{"时间": i, "速度(m/s)": h.speed} for i, h in enumerate(st.session_state.hb_history[:50])])
                    st.line_chart(spd_df, x="时间", y="速度(m/s)")
            else:
                st.info("等待更多数据...")

            st.subheader("📋 飞行日志")
            if st.session_state.hb_history:
                log = []
                for h in st.session_state.hb_history[:20]:
                    log.append({
                        "时间": h.timestamp, "飞行时间(s)": f"{h.flight_time:.1f}",
                        "纬度": f"{h.lat:.6f}", "经度": f"{h.lng:.6f}",
                        "高度(m)": h.altitude, "速度(m/s)": h.speed,
                        "电压(V)": h.voltage, "卫星数": h.satellites
                    })
                st.dataframe(pd.DataFrame(log), use_container_width=True)
            else:
                st.info("暂无飞行数据")
        else:
            st.info("⏳ 等待心跳数据... 请在「航线规划」页面点击「开始飞行」")
            st.markdown("---")
            st.info("💡 提示：先设置起点/终点，然后点击「开始飞行」")

if __name__ == "__main__":
    main()
