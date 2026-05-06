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

# ==================== 配置常量 ====================
SCHOOL_CENTER_GCJ = [118.749413, 32.234097]  # 学校中心 GCJ-02
DEFAULT_A_GCJ = [118.746956, 32.232945]      # 默认起点 A (GCJ-02)
DEFAULT_B_GCJ = [118.751589, 32.235204]      # 默认终点 B (GCJ-02)

# 高德地图瓦片 URL（卫星图）
GAODE_SATELLITE_URL = "https://webst01.is.autonavi.com/appmaptile?style=6&x={x}&y={y}&z={z}"

# 心跳包配置
HEARTBEAT_INTERVAL = 0.2
BASE_SPEED_MPS = 5.0

# ==================== 坐标转换函数 ====================
def wgs84_to_gcj02(lng, lat):
    """WGS84 转 GCJ-02 (简化版，用于演示)"""
    # 实际项目中应使用精确算法，这里使用简单偏移模拟
    # 南京地区大致偏移 (lng+0.006, lat+0.002)
    return lng + 0.006, lat + 0.002

def gcj02_to_wgs84(lng, lat):
    """GCJ-02 转 WGS84 (简化)"""
    return lng - 0.006, lat - 0.002

# ==================== 几何辅助函数 ====================
def distance(p1: List[float], p2: List[float]) -> float:
    return math.sqrt((p1[0]-p2[0])**2 + (p1[1]-p2[1])**2)

def calculate_path_length(path: List[List[float]]) -> float:
    total = 0.0
    for i in range(len(path) - 1):
        total += distance(path[i], path[i+1])
    return total

def point_in_polygon(point: List[float], polygon: List[List[float]]) -> bool:
    x, y = point
    inside = False
    n = len(polygon)
    for i in range(n):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % n]
        if ((y1 > y) != (y2 > y)) and (x < (x2 - x1) * (y - y1) / (y2 - y1) + x1):
            inside = not inside
    return inside

def segments_intersect(p1, p2, p3, p4):
    def orientation(p, q, r):
        val = (q[1]-p[1])*(r[0]-q[0]) - (q[0]-p[0])*(r[1]-q[1])
        if abs(val) < 1e-10: return 0
        return 1 if val > 0 else 2
    def on_segment(p, q, r):
        return (min(p[0], r[0]) <= q[0] <= max(p[0], r[0]) and
                min(p[1], r[1]) <= q[1] <= max(p[1], r[1]))
    o1 = orientation(p1, p2, p3)
    o2 = orientation(p1, p2, p4)
    o3 = orientation(p3, p4, p1)
    o4 = orientation(p3, p4, p2)
    if o1 != o2 and o3 != o4: return True
    if o1 == 0 and on_segment(p1, p3, p2): return True
    if o2 == 0 and on_segment(p1, p4, p2): return True
    if o3 == 0 and on_segment(p3, p1, p4): return True
    if o4 == 0 and on_segment(p3, p2, p4): return True
    return False

def line_intersects_polygon(p1, p2, polygon):
    if point_in_polygon(p1, polygon) or point_in_polygon(p2, polygon):
        return True
    n = len(polygon)
    for i in range(n):
        p3 = polygon[i]
        p4 = polygon[(i+1)%n]
        if segments_intersect(p1, p2, p3, p4):
            return True
    return False

# ==================== 避障路径规划 ====================
def get_blocking_obstacles(start, end, obstacles, flight_altitude):
    blocking = []
    for obs in obstacles:
        if obs.get('height', 30) > flight_altitude:
            coords = obs.get('polygon', [])
            if coords and line_intersects_polygon(start, end, coords):
                blocking.append(obs)
    return blocking

def meters_to_deg(meters, lat=32.23):
    lat_deg = meters / 111000
    lng_deg = meters / (111000 * math.cos(math.radians(lat)))
    return lng_deg, lat_deg

def find_avoidance_path(start, end, obstacles, flight_altitude, safety_radius=5):
    blocking_obs = get_blocking_obstacles(start, end, obstacles, flight_altitude)
    if not blocking_obs:
        return [start, end]
    max_lng = -float('inf')
    max_lat = -float('inf')
    min_lat = float('inf')
    for obs in blocking_obs:
        for point in obs.get('polygon', []):
            max_lng = max(max_lng, point[0])
            max_lat = max(max_lat, point[1])
            min_lat = min(min_lat, point[1])
    safe_lng, safe_lat = meters_to_deg(safety_radius * 3)
    obstacle_height = max_lat - min_lat
    point1 = [start[0] + 0.0012, max_lat + obstacle_height * 3 + safe_lat * 5 + 0.0002]
    point2 = [max_lng + obstacle_height * 2 + safe_lng * 3, point1[1]]
    return [start, point1, point2, end]

# ==================== 心跳包数据类 ====================
class HeartbeatData:
    def __init__(self, timestamp, flight_time, lat, lng, altitude, 
                 voltage, satellites, speed, progress, arrived, remaining_distance):
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
        self.remaining_distance = remaining_distance

class HeartbeatSimulator:
    def __init__(self, start_point):
        self.history = []
        self.current_pos = start_point.copy()
        self.path = [start_point.copy()]
        self.path_index = 0
        self.simulating = False
        self.flight_altitude = 50
        self.speed_percent = 50
        self.progress = 0.0
        self.total_distance = 0.0
        self.distance_traveled = 0.0
        self.safety_radius = 5
        self.start_time = None
        self.last_update_time = None
    
    def set_path(self, path, altitude, speed_percent, safety_radius):
        self.path = path
        self.path_index = 0
        self.current_pos = path[0].copy()
        self.flight_altitude = altitude
        self.speed_percent = speed_percent
        self.safety_radius = safety_radius
        self.simulating = True
        self.progress = 0.0
        self.distance_traveled = 0.0
        self.start_time = datetime.now()
        self.last_update_time = None
        self.history = []
        self.total_distance = 0.0
        for i in range(len(path) - 1):
            self.total_distance += distance(path[i], path[i+1])
        return self._generate_heartbeat(False)
    
    def update(self):
        if not self.simulating or self.path_index >= len(self.path) - 1:
            if self.simulating:
                self.simulating = False
            return None
        
        current_time = time.time()
        if self.last_update_time is None:
            delta_time = HEARTBEAT_INTERVAL
        else:
            delta_time = min(0.5, current_time - self.last_update_time)
        self.last_update_time = current_time
        
        start = self.path[self.path_index]
        end = self.path[self.path_index + 1]
        segment_distance = distance(start, end)
        
        speed_m_per_s = BASE_SPEED_MPS * (self.speed_percent / 100)
        move_distance = speed_m_per_s * delta_time
        self.distance_traveled += move_distance
        
        if self.total_distance > 0:
            self.progress = min(1.0, self.distance_traveled / self.total_distance)
        
        if self.distance_traveled >= segment_distance and self.distance_traveled > 0:
            self.path_index += 1
            self.distance_traveled = 0
            if self.path_index < len(self.path):
                self.current_pos = self.path[self.path_index].copy()
            else:
                self.simulating = False
                return self._generate_heartbeat(True)
        else:
            if segment_distance > 0:
                t = max(0, min(1, self.distance_traveled / segment_distance))
                lng = start[0] + (end[0] - start[0]) * t
                lat = start[1] + (end[1] - start[1]) * t
                self.current_pos = [lng, lat]
        
        return self._generate_heartbeat(False)
    
    def _generate_heartbeat(self, arrived):
        flight_time = (datetime.now() - self.start_time).total_seconds() if self.start_time else 0
        remaining = max(0, self.total_distance - self.distance_traveled) * 111000
        return HeartbeatData(
            timestamp=datetime.now().strftime("%H:%M:%S"),
            flight_time=flight_time,
            lat=self.current_pos[1],
            lng=self.current_pos[0],
            altitude=self.flight_altitude,
            voltage=round(22.2 + random.uniform(-0.5, 0.5), 1),
            satellites=random.randint(8, 14),
            speed=round(BASE_SPEED_MPS * (self.speed_percent / 100), 1),
            progress=self.progress,
            arrived=arrived,
            remaining_distance=remaining
        )

# ==================== 后台线程 ====================
def background_worker():
    while True:
        time.sleep(HEARTBEAT_INTERVAL)
        if st.session_state.get('simulation_running', False):
            hb = st.session_state.heartbeat_sim.update()
            if hb:
                st.session_state.latest_heartbeat = hb
                st.session_state.last_heartbeat_time = time.time()
                st.session_state.heartbeat_history.insert(0, hb)
                if len(st.session_state.heartbeat_history) > 100:
                    st.session_state.heartbeat_history.pop()
                st.session_state.flight_trail.append([hb.lng, hb.lat])
                if len(st.session_state.flight_trail) > 200:
                    st.session_state.flight_trail.pop(0)
                if hb.arrived:
                    st.session_state.simulation_running = False

# ==================== 地图创建（仅用于航线规划） ====================
def create_planning_map(center, points, obstacles, flight_trail, planned_path, 
                        drone_pos, safety_radius, flight_altitude):
    m = folium.Map(location=[center[1], center[0]], zoom_start=16,
                   tiles=GAODE_SATELLITE_URL, attr='高德卫星地图')
    for obs in obstacles:
        coords = obs.get('polygon', [])
        height = obs.get('height', 30)
        if coords and len(coords) >= 3:
            color = "red" if height > flight_altitude else "orange"
            folium.Polygon([[c[1], c[0]] for c in coords], color=color, weight=3,
                           fill=True, fill_color=color, fill_opacity=0.4,
                           popup=f"🚧 {obs.get('name')}\n高度: {height}m").add_to(m)
    if points.get('A'):
        folium.Marker([points['A'][1], points['A'][0]], popup="🟢 起点 A",
                      icon=folium.Icon(color="green", icon="play", prefix="fa")).add_to(m)
    if points.get('B'):
        folium.Marker([points['B'][1], points['B'][0]], popup="🔴 终点 B",
                      icon=folium.Icon(color="red", icon="flag-checkered", prefix="fa")).add_to(m)
    if planned_path and len(planned_path) > 1:
        path_locations = [[p[1], p[0]] for p in planned_path]
        folium.PolyLine(path_locations, color="green", weight=5, opacity=0.8, popup="规划航线").add_to(m)
        for i, point in enumerate(planned_path[1:-1]):
            folium.CircleMarker([point[1], point[0]], radius=5, color="green", fill=True,
                                fill_color="white", popup=f"航点 {i+1}").add_to(m)
    if points.get('A') and points.get('B'):
        folium.PolyLine([[points['A'][1], points['A'][0]], [points['B'][1], points['B'][0]]],
                        color="gray", weight=2, opacity=0.5, dash_array='5,5', popup="直线航线").add_to(m)
    if flight_trail and len(flight_trail) > 1:
        trail_locations = [[lat, lng] for lng, lat in flight_trail[-50:]]
        folium.PolyLine(trail_locations, color="orange", weight=2, opacity=0.6, popup="历史轨迹").add_to(m)
    if drone_pos:
        folium.Marker([drone_pos[1], drone_pos[0]], popup="✈️ 无人机",
                      icon=folium.Icon(color="blue", icon="plane", prefix="fa")).add_to(m)
        folium.Circle(radius=safety_radius, location=[drone_pos[1], drone_pos[0]],
                      color="blue", weight=2, fill=True, fill_color="blue", fill_opacity=0.2,
                      popup=f"安全半径: {safety_radius}米").add_to(m)
    return m

# ==================== 初始化 Session State ====================
def init_session_state():
    defaults = {
        'points_gcj': {'A': DEFAULT_A_GCJ.copy(), 'B': DEFAULT_B_GCJ.copy()},
        'obstacles_gcj': [],
        'heartbeat_sim': HeartbeatSimulator(DEFAULT_A_GCJ.copy()),
        'latest_heartbeat': None,
        'heartbeat_history': [],
        'flight_trail': [],
        'last_heartbeat_time': time.time(),
        'simulation_running': False,
        'planned_path': None,
        'safety_radius': 5,
        'flight_altitude': 50,
        'drone_speed': 50,
        'coord_system': 'GCJ-02'   # 坐标系选择
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

# ==================== 主界面 ====================
def main():
    st.set_page_config(page_title="南京科技职业学院 - 无人机地面站", layout="wide")
    st.title("🏫 南京科技职业学院 - 无人机地面站系统")
    st.markdown("---")
    
    init_session_state()
    
    # 启动后台线程
    if 'worker_started' not in st.session_state:
        st.session_state.worker_started = True
        thread = threading.Thread(target=background_worker, daemon=True)
        thread.start()
    
    # ==================== 侧边栏 ====================
    st.sidebar.header("⚙️ 系统设置")
    # 坐标系设置
    coord_system = st.sidebar.radio(
        "坐标系设置",
        options=["WGS-84", "GCJ-02(高德/百度)"],
        index=0 if st.session_state.coord_system == "WGS-84" else 1,
        help="高德地图使用 GCJ-02 坐标系。若选择 WGS-84，输入的坐标将自动转换为 GCJ-02。"
    )
    st.session_state.coord_system = "WGS-84" if coord_system == "WGS-84" else "GCJ-02"
    
    st.sidebar.markdown("---")
    st.sidebar.subheader("📍 导航")
    # 直接在侧边栏显示页面选择，但主区域仍使用 tabs，保持原样
    
    st.sidebar.markdown("---")
    st.sidebar.subheader("📊 系统状态")
    # 显示 A/B 点状态
    a_set = st.session_state.points_gcj.get('A') is not None
    b_set = st.session_state.points_gcj.get('B') is not None
    st.sidebar.checkbox("A点已设", value=a_set, disabled=True)
    st.sidebar.checkbox("B点已设", value=b_set, disabled=True)
    
    # 飞行参数设置（移到侧边栏）
    st.sidebar.markdown("---")
    st.sidebar.subheader("✈️ 飞行参数")
    flight_alt = st.sidebar.slider("飞行高度 (m)", 10, 200, st.session_state.flight_altitude, 5)
    if flight_alt != st.session_state.flight_altitude:
        st.session_state.flight_altitude = flight_alt
        # 重新规划路径
        if st.session_state.points_gcj.get('A') and st.session_state.points_gcj.get('B'):
            st.session_state.planned_path = find_avoidance_path(
                st.session_state.points_gcj['A'], st.session_state.points_gcj['B'],
                st.session_state.obstacles_gcj, flight_alt, st.session_state.safety_radius)
            st.rerun()
    
    drone_speed = st.sidebar.slider("速度系数 (%)", 10, 100, st.session_state.drone_speed, 5)
    st.session_state.drone_speed = drone_speed
    
    safety_radius = st.sidebar.slider("安全半径 (米)", 1, 20, st.session_state.safety_radius, 1)
    st.session_state.safety_radius = safety_radius
    
    # ==================== 坐标输入辅助函数 ====================
    def get_input_point(label, default_gcj):
        """根据当前坐标系，返回用户输入的坐标（转换为 GCJ-02 存储）"""
        if st.session_state.coord_system == "GCJ-02":
            lat = st.number_input(f"{label} 纬度", value=default_gcj[1], format="%.6f", key=f"{label}_lat")
            lng = st.number_input(f"{label} 经度", value=default_gcj[0], format="%.6f", key=f"{label}_lng")
            return [lng, lat]
        else:  # WGS-84
            lat_wgs = st.number_input(f"{label} 纬度 (WGS-84)", value=gcj02_to_wgs84(default_gcj[0], default_gcj[1])[1], format="%.6f", key=f"{label}_lat_wgs")
            lng_wgs = st.number_input(f"{label} 经度 (WGS-84)", value=gcj02_to_wgs84(default_gcj[0], default_gcj[1])[0], format="%.6f", key=f"{label}_lng_wgs")
            # 转换为 GCJ-02 存储
            lng_gcj, lat_gcj = wgs84_to_gcj02(lng_wgs, lat_wgs)
            return [lng_gcj, lat_gcj]
    
    # ==================== 航线规划页面（地图 + 控制） ====================
    st.header("🗺️ 航线规划")
    col_map, col_info = st.columns([3, 1])
    with col_map:
        # 显示地图
        if st.session_state.planned_path is None and st.session_state.points_gcj.get('A') and st.session_state.points_gcj.get('B'):
            st.session_state.planned_path = find_avoidance_path(
                st.session_state.points_gcj['A'], st.session_state.points_gcj['B'],
                st.session_state.obstacles_gcj, st.session_state.flight_altitude, st.session_state.safety_radius)
        
        drone_pos = None
        if st.session_state.simulation_running and st.session_state.latest_heartbeat:
            drone_pos = [st.session_state.latest_heartbeat.lng, st.session_state.latest_heartbeat.lat]
        
        m = create_planning_map(
            SCHOOL_CENTER_GCJ, st.session_state.points_gcj, st.session_state.obstacles_gcj,
            st.session_state.flight_trail, st.session_state.planned_path, drone_pos,
            st.session_state.safety_radius, st.session_state.flight_altitude)
        folium_static(m, width=800, height=550)
    
    with col_info:
        st.subheader("🎮 飞行控制")
        # 起点/终点设置（放在侧边栏？为了方便，放在此处）
        st.markdown("#### 起点 A")
        a_input = get_input_point("A点", DEFAULT_A_GCJ)
        if st.button("设置起点 A"):
            st.session_state.points_gcj['A'] = a_input
            st.session_state.planned_path = find_avoidance_path(
                st.session_state.points_gcj['A'], st.session_state.points_gcj['B'],
                st.session_state.obstacles_gcj, st.session_state.flight_altitude, st.session_state.safety_radius)
            st.rerun()
        
        st.markdown("#### 终点 B")
        b_input = get_input_point("B点", DEFAULT_B_GCJ)
        if st.button("设置终点 B"):
            st.session_state.points_gcj['B'] = b_input
            st.session_state.planned_path = find_avoidance_path(
                st.session_state.points_gcj['A'], st.session_state.points_gcj['B'],
                st.session_state.obstacles_gcj, st.session_state.flight_altitude, st.session_state.safety_radius)
            st.rerun()
        
        # 航线信息
        if st.session_state.points_gcj.get('A') and st.session_state.points_gcj.get('B'):
            a = st.session_state.points_gcj['A']
            b = st.session_state.points_gcj['B']
            dist = distance(a, b) * 111000
            st.metric("📏 直线距离", f"{dist:.0f} 米")
        if st.session_state.planned_path:
            path_len = calculate_path_length(st.session_state.planned_path) * 111000
            st.metric("✈️ 航线长度", f"{path_len:.0f} 米")
            waypoint_count = len(st.session_state.planned_path) - 2
            st.metric("🎯 绕行点数量", waypoint_count)
        
        st.markdown("---")
        col_start, col_stop = st.columns(2)
        with col_start:
            if st.button("▶️ 开始飞行", type="primary", use_container_width=True):
                if st.session_state.points_gcj.get('A') and st.session_state.points_gcj.get('B'):
                    path = st.session_state.planned_path or [st.session_state.points_gcj['A'], st.session_state.points_gcj['B']]
                    init_hb = st.session_state.heartbeat_sim.set_path(
                        path, st.session_state.flight_altitude, st.session_state.drone_speed, st.session_state.safety_radius)
                    if init_hb:
                        st.session_state.latest_heartbeat = init_hb
                        st.session_state.last_heartbeat_time = time.time()
                        st.session_state.heartbeat_history.insert(0, init_hb)
                        st.session_state.flight_trail.append([init_hb.lng, init_hb.lat])
                    st.session_state.simulation_running = True
                    st.success("🚁 飞行已开始！")
                    st.rerun()
                else:
                    st.error("请先设置起点和终点")
        with col_stop:
            if st.button("⏹️ 停止飞行", use_container_width=True):
                st.session_state.simulation_running = False
                st.session_state.heartbeat_sim.simulating = False
                st.info("飞行已停止")
                st.rerun()
        
        # 显示实时状态简讯（可选）
        if st.session_state.simulation_running and st.session_state.latest_heartbeat:
            hb = st.session_state.latest_heartbeat
            st.markdown("---")
            st.subheader("📡 实时状态")
            st.metric("进度", f"{hb.progress*100:.1f}%")
            st.metric("速度", f"{hb.speed} m/s")
            st.metric("高度", f"{hb.altitude} m")
    
    # ==================== 飞行监控页面（心跳包数据，无地图） ====================
    st.markdown("---")
    st.header("📡 飞行监控 - 实时心跳包")
    if st.session_state.simulation_running and st.session_state.latest_heartbeat:
        hb = st.session_state.latest_heartbeat
        st.progress(hb.progress, text=f"飞行进度：{int(hb.progress*100)}%")
        
        col1, col2, col3, col4, col5 = st.columns(5)
        with col1: st.metric("⏰ 飞行时间", f"{hb.flight_time:.1f}s")
        with col2: st.metric("📍 当前位置", f"{hb.lat:.6f}, {hb.lng:.6f}")
        with col3: st.metric("📏 飞行高度", f"{hb.altitude} m")
        with col4: st.metric("💨 当前速度", f"{hb.speed} m/s")
        with col5: st.metric("📏 剩余距离", f"{hb.remaining_distance:.0f} m")
        
        col6, col7, col8, col9 = st.columns(4)
        with col6: st.metric("🔋 电池电压", f"{hb.voltage} V")
        with col7: st.metric("🛰️ 卫星数量", f"{hb.satellites} 颗")
        with col8: st.metric("🎯 任务进度", f"{int(hb.progress*100)}%")
        with col9: st.metric("🛡️ 安全半径", f"{st.session_state.safety_radius} m")
        
        if hb.arrived:
            st.success("🎉 无人机已到达目的地！飞行任务完成！")
        
        st.markdown("---")
        st.subheader("📈 实时数据图表")
        if len(st.session_state.heartbeat_history) > 1:
            col_ch1, col_ch2 = st.columns(2)
            with col_ch1:
                alt_data = [{"时间": i, "高度(m)": h.altitude} for i, h in enumerate(st.session_state.heartbeat_history[:50])]
                st.line_chart(pd.DataFrame(alt_data), x="时间", y="高度(m)")
            with col_ch2:
                speed_data = [{"时间": i, "速度(m/s)": h.speed} for i, h in enumerate(st.session_state.heartbeat_history[:50])]
                st.line_chart(pd.DataFrame(speed_data), x="时间", y="速度(m/s)")
        else:
            st.info("等待更多数据...")
        
        st.subheader("📋 飞行日志")
        if st.session_state.heartbeat_history:
            log_data = []
            for h in st.session_state.heartbeat_history[:20]:
                log_data.append({
                    "时间": h.timestamp, "飞行时间(s)": f"{h.flight_time:.1f}",
                    "纬度": f"{h.lat:.6f}", "经度": f"{h.lng:.6f}",
                    "高度(m)": h.altitude, "速度(m/s)": h.speed,
                    "电压(V)": h.voltage, "卫星数": h.satellites
                })
            st.dataframe(pd.DataFrame(log_data), use_container_width=True)
        else:
            st.info("暂无飞行数据")
    else:
        st.info("⏳ 等待心跳数据... 请在「航线规划」中点击「开始飞行」")

if __name__ == "__main__":
    main()
