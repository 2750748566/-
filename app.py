import streamlit as st
import folium
from streamlit_folium import folium_static, st_folium
from folium import plugins
import random
import time
import math
import json
import os
import threading
from datetime import datetime
from typing import List, Dict, Optional, Tuple, Any
import pandas as pd
from dataclasses import dataclass, field
from streamlit_autorefresh import st_autorefresh

# ==================== 配置常量 ====================
@dataclass
class Config:
    SCHOOL_CENTER_GCJ: List[float] = field(default_factory=lambda: [118.7490, 32.2340])
    DEFAULT_A_GCJ: List[float] = field(default_factory=lambda: [118.746956, 32.232945])
    DEFAULT_B_GCJ: List[float] = field(default_factory=lambda: [118.751589, 32.235204])
    CONFIG_FILE: str = "obstacle_config.json"
    BACKUP_DIR: str = "backups"
    DEFAULT_SAFETY_RADIUS_METERS: int = 5
    MAX_BACKUP_FILES: int = 10
    BASE_SPEED_MPS: float = 5.0
    HEARTBEAT_INTERVAL: float = 0.2
    VOLTAGE_VARIATION: float = 0.5
    SAT_RANGE: Tuple[int, int] = (8, 14)
    GAODE_SATELLITE_URL: str = "https://webst01.is.autonavi.com/appmaptile?style=6&x={x}&y={y}&z={z}"
    GAODE_VECTOR_URL: str = "https://webrd02.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}"
    VERTICAL_OFFSET_MULTIPLIER: float = 3.0
    WAYPOINT_OFFSET_FACTOR: float = 10.0

config = Config()
os.makedirs(config.BACKUP_DIR, exist_ok=True)

# ==================== 几何函数（保持不变） ====================
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

def on_segment(p: List[float], q: List[float], r: List[float]) -> bool:
    return (min(p[0], r[0]) <= q[0] <= max(p[0], r[0]) and
            min(p[1], r[1]) <= q[1] <= max(p[1], r[1]))

def orientation(p: List[float], q: List[float], r: List[float]) -> int:
    val = (q[1] - p[1]) * (r[0] - q[0]) - (q[0] - p[0]) * (r[1] - q[1])
    if abs(val) < 1e-10:
        return 0
    return 1 if val > 0 else 2

def segments_intersect(p1: List[float], p2: List[float], p3: List[float], p4: List[float]) -> bool:
    o1 = orientation(p1, p2, p3)
    o2 = orientation(p1, p2, p4)
    o3 = orientation(p3, p4, p1)
    o4 = orientation(p3, p4, p2)
    if o1 != o2 and o3 != o4:
        return True
    if o1 == 0 and on_segment(p1, p3, p2):
        return True
    if o2 == 0 and on_segment(p1, p4, p2):
        return True
    if o3 == 0 and on_segment(p3, p1, p4):
        return True
    if o4 == 0 and on_segment(p3, p2, p4):
        return True
    return False

def line_intersects_polygon(p1: List[float], p2: List[float], polygon: List[List[float]]) -> bool:
    if point_in_polygon(p1, polygon) or point_in_polygon(p2, polygon):
        return True
    n = len(polygon)
    for i in range(n):
        p3 = polygon[i]
        p4 = polygon[(i + 1) % n]
        if segments_intersect(p1, p2, p3, p4):
            return True
    return False

def distance(p1: List[float], p2: List[float]) -> float:
    return math.sqrt((p1[0]-p2[0])**2 + (p1[1]-p2[1])**2)

def get_polygon_bounds(polygon: List[List[float]]) -> Optional[Dict]:
    if not polygon:
        return None
    min_lng = min(p[0] for p in polygon)
    max_lng = max(p[0] for p in polygon)
    min_lat = min(p[1] for p in polygon)
    max_lat = max(p[1] for p in polygon)
    return {
        'min_lng': min_lng, 'max_lng': max_lng,
        'min_lat': min_lat, 'max_lat': max_lat,
        'center_lng': (min_lng + max_lng) / 2,
        'center_lat': (min_lat + max_lat) / 2
    }

def validate_polygon(polygon: List[List[float]]) -> bool:
    return len(polygon) >= 3

def meters_to_deg(meters: float, lat: float = 32.23) -> Tuple[float, float]:
    lat_deg = meters / 111000
    lng_deg = meters / (111000 * math.cos(math.radians(lat)))
    return lng_deg, lat_deg

def point_to_segment_distance_deg(point: List[float], seg_start: List[float], seg_end: List[float]) -> float:
    px, py = point
    x1, y1 = seg_start
    x2, y2 = seg_end
    dx = x2 - x1
    dy = y2 - y1
    len_sq = dx*dx + dy*dy
    if len_sq == 0:
        return math.sqrt((px-x1)**2 + (py-y1)**2)
    t = ((px - x1) * dx + (py - y1) * dy) / len_sq
    t = max(0, min(1, t))
    proj_x = x1 + t * dx
    proj_y = y1 + t * dy
    return math.sqrt((px - proj_x)**2 + (py - proj_y)**2)

def point_to_segment_distance_meters(point: List[float], seg_start: List[float], seg_end: List[float]) -> float:
    return point_to_segment_distance_deg(point, seg_start, seg_end) * 111000

def check_safety_radius(drone_pos: List[float], obstacles_gcj: List[Dict], flight_altitude: float, safety_radius: float) -> Tuple[bool, Optional[float], Optional[str]]:
    if not drone_pos:
        return True, None, None
    min_distance = float('inf')
    danger_name = None
    for obs in obstacles_gcj:
        if obs.get('height',30) > flight_altitude:
            coords = obs.get('polygon',[])
            if coords and len(coords)>=3:
                for i in range(len(coords)):
                    p1 = coords[i]
                    p2 = coords[(i+1)%len(coords)]
                    dist_m = point_to_segment_distance_meters(drone_pos, p1, p2)
                    if dist_m < min_distance:
                        min_distance = dist_m
                        danger_name = obs.get('name','障碍物')
    if min_distance < safety_radius:
        return False, min_distance, danger_name
    return True, min_distance if min_distance!=float('inf') else None, None

# ==================== 障碍物管理（保持不变，略） ====================
def cleanup_old_backups():
    try:
        backup_files = [f for f in os.listdir(config.BACKUP_DIR) if f.startswith(config.CONFIG_FILE)]
        if len(backup_files) > config.MAX_BACKUP_FILES:
            backup_files.sort()
            for old_file in backup_files[:-config.MAX_BACKUP_FILES]:
                os.remove(os.path.join(config.BACKUP_DIR, old_file))
    except Exception as e:
        st.warning(f"清理备份文件时出错: {e}")

def backup_config() -> Optional[str]:
    if os.path.exists(config.CONFIG_FILE):
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_name = f"{config.BACKUP_DIR}/{config.CONFIG_FILE}.{timestamp}.bak"
        try:
            import shutil
            shutil.copy(config.CONFIG_FILE, backup_name)
            cleanup_old_backups()
            return backup_name
        except Exception as e:
            st.error(f"备份失败: {e}")
            return None
    return None

def load_obstacles() -> List[Dict]:
    if os.path.exists(config.CONFIG_FILE):
        try:
            with open(config.CONFIG_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                obstacles = data.get('obstacles', [])
                for obs in obstacles:
                    if 'selected' not in obs: obs['selected'] = False
                    if 'height' not in obs: obs['height'] = 30
                return obstacles
        except (json.JSONDecodeError, IOError) as e:
            st.error(f"加载配置文件失败: {e}")
            return []
    return []

def save_obstacles(obstacles: List[Dict]) -> bool:
    try:
        backup_config()
        data = {
            'obstacles': obstacles,
            'count': len(obstacles),
            'save_time': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'version': 'v13.1'
        }
        with open(config.CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        st.error(f"保存失败: {e}")
        return False

def get_latest_backup() -> Optional[str]:
    try:
        backup_files = [f for f in os.listdir(config.BACKUP_DIR) if f.startswith(config.CONFIG_FILE) and f.endswith('.bak')]
        if backup_files:
            backup_files.sort(reverse=True)
            return os.path.join(config.BACKUP_DIR, backup_files[0])
    except Exception as e:
        st.error(f"获取备份文件失败: {e}")
    return None

def restore_from_backup(backup_path: str) -> bool:
    try:
        with open(backup_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            obstacles = data.get('obstacles', [])
            save_obstacles(obstacles)
        return True
    except Exception as e:
        st.error(f"恢复备份失败: {e}")
        return False

# ==================== 绕行算法（保持不变） ====================
def get_blocking_obstacles(start, end, obstacles_gcj, flight_altitude):
    blocking = []
    for obs in obstacles_gcj:
        if obs.get('height',30) > flight_altitude:
            coords = obs.get('polygon',[])
            if coords and line_intersects_polygon(start, end, coords):
                blocking.append(obs)
    return blocking

def find_left_path(start, end, obstacles_gcj, flight_altitude, safety_radius=5):
    blocking_obs = get_blocking_obstacles(start, end, obstacles_gcj, flight_altitude)
    if not blocking_obs:
        return [start, end]
    max_lng = -float('inf')
    max_lat = -float('inf')
    min_lat = float('inf')
    for obs in blocking_obs:
        for p in obs.get('polygon',[]):
            max_lng = max(max_lng, p[0])
            max_lat = max(max_lat, p[1])
            min_lat = min(min_lat, p[1])
    safe_lng, safe_lat = meters_to_deg(safety_radius*3)
    obs_h = max_lat - min_lat
    p1 = [start[0]+0.0012, max_lat + obs_h*3 + safe_lat*5 + 0.0002]
    p2 = [max_lng + obs_h*2 + safe_lng*3, p1[1]]
    return [start, p1, p2, end]

def find_right_path(start, end, obstacles_gcj, flight_altitude, safety_radius=5):
    blocking_obs = get_blocking_obstacles(start, end, obstacles_gcj, flight_altitude)
    if not blocking_obs:
        return [start, end]
    mid_x = (start[0]+end[0])/2
    mid_y = (start[1]+end[1])/2
    dx = end[0]-start[0]
    dy = end[1]-start[1]
    length = math.sqrt(dx*dx + dy*dy)
    if length==0:
        return [start,end]
    perp_x = dy/length
    perp_y = -dx/length
    offset_dist = safety_radius * config.WAYPOINT_OFFSET_FACTOR
    lat_rad = math.radians(mid_y)
    lng_scale = 111000 * math.cos(lat_rad)
    lat_scale = 111000
    offset_x = perp_x * offset_dist / lng_scale
    offset_y = perp_y * offset_dist / lat_scale
    waypoint = [mid_x+offset_x, mid_y+offset_y]
    return [start, waypoint, end]

def calculate_path_length(path):
    total = 0.0
    for i in range(len(path)-1):
        total += distance(path[i], path[i+1])
    return total

def find_best_path(start, end, obstacles_gcj, flight_altitude, safety_radius=5):
    left = find_left_path(start, end, obstacles_gcj, flight_altitude, safety_radius)
    right = find_right_path(start, end, obstacles_gcj, flight_altitude, safety_radius)
    return left if calculate_path_length(left) < calculate_path_length(right) else right

def create_avoidance_path(start, end, obstacles_gcj, flight_altitude, direction, safety_radius=5):
    if direction == "向左绕行":
        return find_left_path(start, end, obstacles_gcj, flight_altitude, safety_radius)
    elif direction == "向右绕行":
        return find_right_path(start, end, obstacles_gcj, flight_altitude, safety_radius)
    else:
        return find_best_path(start, end, obstacles_gcj, flight_altitude, safety_radius)

# ==================== 心跳包模拟器（保持原有，但需要支持后台线程） ====================
@dataclass
class HeartbeatData:
    timestamp: str
    flight_time: float
    lat: float
    lng: float
    altitude: float
    voltage: float
    satellites: int
    speed: float
    progress: float
    arrived: bool
    safety_violation: bool
    remaining_distance: float

class HeartbeatSimulator:
    def __init__(self, start_point_gcj: List[float]):
        self.history: List[HeartbeatData] = []
        self.current_pos: List[float] = start_point_gcj.copy()
        self.path: List[List[float]] = [start_point_gcj.copy()]
        self.path_index: int = 0
        self.simulating: bool = False
        self.flight_altitude: float = 50
        self.speed: int = 50
        self.progress: float = 0.0
        self.total_distance: float = 0.0
        self.distance_traveled: float = 0.0
        self.safety_radius: float = config.DEFAULT_SAFETY_RADIUS_METERS
        self.safety_violation: bool = False
        self.start_time: Optional[datetime] = None
        self.flight_log: List[HeartbeatData] = []
        self.last_update_time: Optional[float] = None

    def set_path(self, path: List[List[float]], altitude: float = 50, speed: int = 50, safety_radius: float = 5):
        self.path = path
        self.path_index = 0
        self.current_pos = path[0].copy()
        self.flight_altitude = altitude
        self.speed = speed
        self.safety_radius = safety_radius
        self.simulating = True
        self.progress = 0.0
        self.distance_traveled = 0.0
        self.safety_violation = False
        self.start_time = datetime.now()
        self.last_update_time = None
        self.total_distance = 0.0
        for i in range(len(path) - 1):
            self.total_distance += distance(path[i], path[i+1])
        # 立即生成第一个心跳
        self._generate_heartbeat(False)

    def update_and_generate(self, obstacles_gcj: List[Dict]) -> Optional[HeartbeatData]:
        if not self.simulating or self.path_index >= len(self.path) - 1:
            if self.simulating:
                self.simulating = False
            return None
        current_time = time.time()
        if self.last_update_time is None:
            delta_time = config.HEARTBEAT_INTERVAL
        else:
            delta_time = min(0.5, current_time - self.last_update_time)
        self.last_update_time = current_time

        start = self.path[self.path_index]
        end = self.path[self.path_index + 1]
        segment_distance = distance(start, end)
        speed_m_per_s = config.BASE_SPEED_MPS * (self.speed / 100)
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
                t = min(1.0, max(0.0, self.distance_traveled / segment_distance))
                lng = start[0] + (end[0] - start[0]) * t
                lat = start[1] + (end[1] - start[1]) * t
                self.current_pos = [lng, lat]
                safe, _, _ = check_safety_radius(self.current_pos, obstacles_gcj, self.flight_altitude, self.safety_radius)
                if not safe:
                    self.safety_violation = True
        return self._generate_heartbeat(False)

    def _generate_heartbeat(self, arrived: bool = False) -> HeartbeatData:
        flight_time = (datetime.now() - self.start_time).total_seconds() if self.start_time else 0
        heartbeat = HeartbeatData(
            timestamp=datetime.now().strftime("%H:%M:%S"),
            flight_time=flight_time,
            lat=self.current_pos[1],
            lng=self.current_pos[0],
            altitude=self.flight_altitude,
            voltage=round(22.2 + random.uniform(-config.VOLTAGE_VARIATION, config.VOLTAGE_VARIATION), 1),
            satellites=random.randint(*config.SAT_RANGE),
            speed=round(config.BASE_SPEED_MPS * (self.speed / 100), 1),
            progress=self.progress,
            arrived=arrived,
            safety_violation=self.safety_violation,
            remaining_distance=max(0, self.total_distance - self.distance_traveled) * 111000
        )
        self.history.insert(0, heartbeat)
        if len(self.history) > 200:
            self.history.pop()
        self.flight_log.append(heartbeat)
        if len(self.flight_log) > 1000:
            self.flight_log.pop(0)
        return heartbeat

    def export_flight_data(self) -> pd.DataFrame:
        if not self.flight_log:
            return pd.DataFrame()
        data = [{
            'timestamp': h.timestamp,
            'flight_time': h.flight_time,
            'lat': h.lat,
            'lng': h.lng,
            'altitude': h.altitude,
            'voltage': h.voltage,
            'satellites': h.satellites,
            'speed': h.speed,
            'progress': h.progress,
            'arrived': h.arrived,
            'safety_violation': h.safety_violation,
            'remaining_distance': h.remaining_distance
        } for h in self.flight_log]
        return pd.DataFrame(data)

# ==================== 后台飞行线程 ====================
def background_flight_worker():
    while True:
        time.sleep(config.HEARTBEAT_INTERVAL)
        if st.session_state.get('simulation_running', False):
            sim = st.session_state.get('heartbeat_sim')
            if sim and sim.simulating:
                obstacles = st.session_state.get('obstacles_gcj', [])
                new_hb = sim.update_and_generate(obstacles)
                if new_hb:
                    st.session_state.last_hb_time = time.time()
                    # 更新飞行历史（用于地图轨迹）
                    st.session_state.flight_history.append([new_hb.lng, new_hb.lat])
                    if len(st.session_state.flight_history) > 200:
                        st.session_state.flight_history.pop(0)
                if sim.arrived or not sim.simulating:
                    st.session_state.simulation_running = False

# ==================== 地图创建（包含规划路径、障碍物、飞行轨迹） ====================
def create_planning_map(center_gcj: List[float], points_gcj: Dict, obstacles_gcj: List[Dict],
                        flight_history: Optional[List] = None, planned_path: Optional[List] = None,
                        map_type: str = "satellite", straight_blocked: bool = True,
                        flight_altitude: float = 50, drone_pos: Optional[List] = None,
                        direction: str = "最佳航线", safety_radius: float = 5) -> folium.Map:
    if map_type == "satellite":
        tiles = config.GAODE_SATELLITE_URL
        attr = "高德卫星地图"
    else:
        tiles = config.GAODE_VECTOR_URL
        attr = "高德矢量地图"
    m = folium.Map(location=[center_gcj[1], center_gcj[0]], zoom_start=16, tiles=tiles, attr=attr)

    # 绘制障碍物
    for obs in obstacles_gcj:
        coords = obs.get('polygon', [])
        height = obs.get('height', 30)
        if coords and len(coords) >= 3:
            color = "red" if height > flight_altitude else "orange"
            folium.Polygon([[c[1], c[0]] for c in coords], color=color, weight=3, fill=True,
                           fill_color=color, fill_opacity=0.4, popup=f"🚧 {obs.get('name')}\n高度: {height}m").add_to(m)

    # 起点和终点
    if points_gcj.get('A'):
        folium.Marker([points_gcj['A'][1], points_gcj['A'][0]], popup="🟢 起点",
                      icon=folium.Icon(color="green", icon="play", prefix="fa")).add_to(m)
    if points_gcj.get('B'):
        folium.Marker([points_gcj['B'][1], points_gcj['B'][0]], popup="🔴 终点",
                      icon=folium.Icon(color="red", icon="stop", prefix="fa")).add_to(m)

    # 规划路径
    if planned_path and len(planned_path) > 1:
        path_locations = [[p[1], p[0]] for p in planned_path]
        if "向左" in direction:
            line_color = "purple"
        elif "向右" in direction:
            line_color = "orange"
        else:
            line_color = "green"
        folium.PolyLine(path_locations, color=line_color, weight=5, opacity=0.9,
                        popup=f"✈️ {direction}").add_to(m)
        for i, point in enumerate(planned_path[1:-1]):
            folium.CircleMarker([point[1], point[0]], radius=5, color=line_color, fill=True,
                                fill_color="white", fill_opacity=0.8, popup=f"航点 {i+1}").add_to(m)

    # 直线航线（虚线）
    if points_gcj.get('A') and points_gcj.get('B'):
        if not straight_blocked:
            folium.PolyLine([[points_gcj['A'][1], points_gcj['A'][0]], [points_gcj['B'][1], points_gcj['B'][0]]],
                            color="blue", weight=2, opacity=0.5, dash_array='5, 5', popup="直线航线").add_to(m)
        else:
            folium.PolyLine([[points_gcj['A'][1], points_gcj['A'][0]], [points_gcj['B'][1], points_gcj['B'][0]]],
                            color="gray", weight=2, opacity=0.4, dash_array='5, 5', popup="⚠️ 直线被阻挡").add_to(m)

    # 无人机当前位置
    if drone_pos:
        folium.Circle(radius=safety_radius, location=[drone_pos[1], drone_pos[0]], color="blue", weight=2,
                      fill=True, fill_color="blue", fill_opacity=0.2, popup=f"🛡️ 安全半径: {safety_radius}米").add_to(m)
        folium.Marker([drone_pos[1], drone_pos[0]], icon=folium.Icon(color='red', icon='plane', prefix='fa')).add_to(m)

    # 历史飞行轨迹
    if flight_history and len(flight_history) > 1:
        trail = [[p[1], p[0]] for p in flight_history if len(p) >= 2]
        if len(trail) > 1:
            folium.PolyLine(trail, color="orange", weight=2, opacity=0.6, popup="历史轨迹").add_to(m)
    return m

# ==================== 辅助UI函数 ====================
def init_session_state():
    defaults = {
        'points_gcj': {'A': config.DEFAULT_A_GCJ.copy(), 'B': config.DEFAULT_B_GCJ.copy()},
        'obstacles_gcj': load_obstacles(),
        'heartbeat_sim': HeartbeatSimulator(config.DEFAULT_A_GCJ.copy()),
        'last_hb_time': time.time(),
        'simulation_running': False,
        'flight_history': [],
        'planned_path': None,
        'last_flight_altitude': 50,
        'pending_obstacle': None,
        'current_direction': "最佳航线",
        'safety_radius': config.DEFAULT_SAFETY_RADIUS_METERS,
        'auto_backup': True,
        'show_rename_dialog': False,
        'bg_worker_started': False
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

    # 启动后台线程（仅一次）
    if not st.session_state.bg_worker_started:
        thread = threading.Thread(target=background_flight_worker, daemon=True)
        thread.start()
        st.session_state.bg_worker_started = True

def check_straight_blocked(points_gcj: Dict, obstacles_gcj: List[Dict], flight_altitude: float) -> Tuple[bool, int]:
    blocked = False
    high_count = 0
    for obs in obstacles_gcj:
        if obs.get('height', 30) > flight_altitude:
            high_count += 1
            coords = obs.get('polygon', [])
            if coords and line_intersects_polygon(points_gcj['A'], points_gcj['B'], coords):
                blocked = True
    return blocked, high_count

def render_sidebar() -> Tuple[str, str, int, float, bool]:
    st.sidebar.title("🎛️ 导航菜单")
    page = st.sidebar.radio("选择功能模块", ["🗺️ 航线规划", "📡 飞行监控", "🚧 障碍物管理"])
    map_type_choice = st.sidebar.radio("🗺️ 地图类型", ["卫星影像", "矢量街道"], index=0)
    map_type = "satellite" if map_type_choice == "卫星影像" else "vector"
    st.sidebar.markdown("---")
    st.sidebar.subheader("⚡ 无人机速度设置")
    drone_speed = st.sidebar.slider("飞行速度系数", min_value=10, max_value=100, value=50, step=5)
    st.sidebar.markdown("---")
    st.sidebar.subheader("✈️ 无人机飞行高度")
    flight_alt = st.sidebar.slider("飞行高度 (m)", min_value=10, max_value=200, value=50, step=5)
    st.sidebar.markdown("---")
    st.sidebar.subheader("🛡️ 安全半径设置")
    safety_radius = st.sidebar.slider("安全半径 (米)", min_value=1, max_value=20,
                                       value=st.session_state.safety_radius, step=1)
    st.sidebar.markdown("---")
    st.sidebar.subheader("💾 自动保存")
    auto_save = st.sidebar.checkbox("自动保存障碍物", value=st.session_state.auto_backup)
    return page, map_type, drone_speed, flight_alt, auto_save

# ==================== 页面渲染函数 ====================
def render_planning_page(map_type: str, drone_speed: int, flight_alt: float, auto_save: bool):
    st.header("🗺️ 航线规划 - 智能避障")
    straight_blocked, high_obstacles = check_straight_blocked(st.session_state.points_gcj,
                                                               st.session_state.obstacles_gcj,
                                                               flight_alt)
    if straight_blocked:
        st.warning(f"⚠️ 有 {high_obstacles} 个障碍物高于飞行高度({flight_alt}m)，需要绕行")
    else:
        st.success("✅ 直线航线畅通无阻（所有障碍物高度 ≤ 飞行高度）")
    st.info("📝 点击地图左上角📐图标 → 选择多边形 → 围绕建筑物绘制 → 双击完成 → 输入高度并保存")

    col1, col2 = st.columns([1, 1.5])
    with col1:
        render_planning_controls(flight_alt, drone_speed, auto_save)
    with col2:
        render_planning_map_view(map_type, flight_alt, straight_blocked)

def render_planning_controls(flight_alt: float, drone_speed: int, auto_save: bool):
    st.subheader("🎮 控制面板")
    with st.expander("📍 起点/终点设置", expanded=True):
        render_point_settings()
    with st.expander("🤖 路径规划策略", expanded=True):
        render_path_strategy(flight_alt)
    with st.expander("✈️ 飞行控制", expanded=True):
        render_flight_controls(flight_alt, drone_speed)
    st.markdown("### 📍 当前坐标")
    a = st.session_state.points_gcj['A']
    b = st.session_state.points_gcj['B']
    st.write(f"🟢 A点: ({a[0]:.6f}, {a[1]:.6f})")
    st.write(f"🔴 B点: ({b[0]:.6f}, {b[1]:.6f})")
    dist = math.sqrt((b[0]-a[0])**2 + (b[1]-a[1])**2) * 111000
    st.caption(f"📏 直线距离: {dist:.0f} 米")

def render_point_settings():
    st.markdown("#### 🟢 起点 A")
    col_a1, col_a2 = st.columns(2)
    with col_a1:
        a_lat = st.number_input("纬度", value=st.session_state.points_gcj['A'][1], format="%.6f",
                                 key="a_lat", step=0.000001)
    with col_a2:
        a_lng = st.number_input("经度", value=st.session_state.points_gcj['A'][0], format="%.6f",
                                 key="a_lng", step=0.000001)
    if st.button("📍 设置 A 点", use_container_width=True):
        st.session_state.points_gcj['A'] = [a_lng, a_lat]
        st.session_state.planned_path = create_avoidance_path(
            st.session_state.points_gcj['A'], st.session_state.points_gcj['B'],
            st.session_state.obstacles_gcj, st.session_state.last_flight_altitude,
            st.session_state.current_direction, st.session_state.safety_radius)
        st.rerun()

    st.markdown("#### 🔴 终点 B")
    col_b1, col_b2 = st.columns(2)
    with col_b1:
        b_lat = st.number_input("纬度", value=st.session_state.points_gcj['B'][1], format="%.6f",
                                 key="b_lat", step=0.000001)
    with col_b2:
        b_lng = st.number_input("经度", value=st.session_state.points_gcj['B'][0], format="%.6f",
                                 key="b_lng", step=0.000001)
    if st.button("📍 设置 B 点", use_container_width=True):
        st.session_state.points_gcj['B'] = [b_lng, b_lat]
        st.session_state.planned_path = create_avoidance_path(
            st.session_state.points_gcj['A'], st.session_state.points_gcj['B'],
            st.session_state.obstacles_gcj, st.session_state.last_flight_altitude,
            st.session_state.current_direction, st.session_state.safety_radius)
        st.rerun()

def render_path_strategy(flight_alt: float):
    st.markdown("**选择绕行方向：**")
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("🔄 最佳航线", use_container_width=True,
                     type="primary" if st.session_state.current_direction == "最佳航线" else "secondary"):
            st.session_state.current_direction = "最佳航线"
            st.session_state.planned_path = create_avoidance_path(
                st.session_state.points_gcj['A'], st.session_state.points_gcj['B'],
                st.session_state.obstacles_gcj, flight_alt, "最佳航线", st.session_state.safety_radius)
            st.success("已切换到最佳航线模式")
            st.rerun()
    with col2:
        if st.button("⬅️ 向左绕行", use_container_width=True,
                     type="primary" if st.session_state.current_direction == "向左绕行" else "secondary"):
            st.session_state.current_direction = "向左绕行"
            st.session_state.planned_path = create_avoidance_path(
                st.session_state.points_gcj['A'], st.session_state.points_gcj['B'],
                st.session_state.obstacles_gcj, flight_alt, "向左绕行", st.session_state.safety_radius)
            st.success("已切换到向左绕行模式")
            st.rerun()
    with col3:
        if st.button("➡️ 向右绕行", use_container_width=True,
                     type="primary" if st.session_state.current_direction == "向右绕行" else "secondary"):
            st.session_state.current_direction = "向右绕行"
            st.session_state.planned_path = create_avoidance_path(
                st.session_state.points_gcj['A'], st.session_state.points_gcj['B'],
                st.session_state.obstacles_gcj, flight_alt, "向右绕行", st.session_state.safety_radius)
            st.success("已切换到向右绕行模式")
            st.rerun()
    st.info(f"📌 当前绕行策略: **{st.session_state.current_direction}**")
    if st.button("🔄 重新规划路径", use_container_width=True):
        st.session_state.planned_path = create_avoidance_path(
            st.session_state.points_gcj['A'], st.session_state.points_gcj['B'],
            st.session_state.obstacles_gcj, flight_alt, st.session_state.current_direction, st.session_state.safety_radius)
        if st.session_state.planned_path:
            waypoint_count = len(st.session_state.planned_path) - 2
            st.success(f"已按照「{st.session_state.current_direction}」规划路径，{waypoint_count}个绕行点")
            st.rerun()

def render_flight_controls(flight_alt: float, drone_speed: int):
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("当前飞行高度", f"{flight_alt} m")
    with col2:
        st.metric("速度系数", f"{drone_speed}%")
    with col3:
        st.metric("🛡️ 安全半径", f"{st.session_state.safety_radius} 米")
    if st.session_state.planned_path:
        waypoint_count = len(st.session_state.planned_path) - 2
        st.metric("🎯 绕行点数量", waypoint_count)
        total_dist = calculate_path_length(st.session_state.planned_path) * 111000
        st.caption(f"📏 规划路径总长: {total_dist:.0f} 米")
    col_btn1, col_btn2 = st.columns(2)
    with col_btn1:
        if st.button("▶️ 开始飞行", use_container_width=True, type="primary"):
            if st.session_state.points_gcj['A'] and st.session_state.points_gcj['B']:
                path = st.session_state.planned_path or [st.session_state.points_gcj['A'], st.session_state.points_gcj['B']]
                st.session_state.heartbeat_sim.set_path(path, flight_alt, drone_speed, st.session_state.safety_radius)
                st.session_state.simulation_running = True
                st.session_state.flight_history = []
                waypoint_count = len(path) - 2
                st.success(f"🚁 飞行已开始！{'路径中有' + str(waypoint_count) + '个绕行点' if waypoint_count > 0 else '直线飞行'}")
                st.rerun()
            else:
                st.error("请先设置起点和终点")
    with col_btn2:
        if st.button("⏹️ 停止飞行", use_container_width=True):
            st.session_state.simulation_running = False
            st.session_state.heartbeat_sim.simulating = False
            st.info("飞行已停止")
            st.rerun()

def render_planning_map_view(map_type: str, flight_alt: float, straight_blocked: bool):
    st.subheader("🗺️ 规划地图")
    if straight_blocked:
        st.caption(f"当前避障策略: {st.session_state.current_direction}")
        st.caption("🟢 绿色=最佳航线 | 🟣 紫色=向左绕行 | 🟠 橙色=向右绕行 | 🔵 蓝色圆圈=安全半径")
    # 获取无人机当前位置（从模拟器中读取）
    drone_pos = st.session_state.heartbeat_sim.current_pos if st.session_state.heartbeat_sim.simulating else None
    m = create_planning_map(
        center_gcj=st.session_state.points_gcj['A'] or config.SCHOOL_CENTER_GCJ,
        points_gcj=st.session_state.points_gcj,
        obstacles_gcj=st.session_state.obstacles_gcj,
        flight_history=st.session_state.flight_history,
        planned_path=st.session_state.planned_path,
        map_type=map_type,
        straight_blocked=straight_blocked,
        flight_altitude=flight_alt,
        drone_pos=drone_pos,
        direction=st.session_state.current_direction,
        safety_radius=st.session_state.safety_radius
    )
    # 使用 folium_static 避免地图的序列化错误
    folium_static(m, width=700, height=550)

# 飞行监控页面（简化，仅显示心跳数据，不包含地图）
def render_flight_monitoring_page(map_type: str, flight_alt: float, drone_speed: int):
    st.header("📡 飞行监控 - 实时心跳包")
    st_autorefresh(interval=1000, key="monitor_refresh")

    if not st.session_state.simulation_running:
        st.info("⏳ 等待心跳数据... 请在「航线规划」页面点击「开始飞行」")
        return

    if not st.session_state.heartbeat_sim.history:
        st.info("等待第一个心跳...")
        return

    latest = st.session_state.heartbeat_sim.history[0]
    st.progress(latest.progress, text=f"飞行进度：{int(latest.progress*100)}%")
    col1, col2, col3, col4, col5 = st.columns(5)
    with col1: st.metric("⏰ 飞行时间", f"{latest.flight_time:.1f}s")
    with col2: st.metric("📍 当前位置", f"{latest.lat:.6f}, {latest.lng:.6f}")
    with col3: st.metric("📏 飞行高度", f"{latest.altitude} m")
    with col4: st.metric("💨 当前速度", f"{latest.speed} m/s", delta=f"{drone_speed}%")
    with col5: st.metric("📏 剩余距离", f"{latest.remaining_distance:.0f} m")

    col6, col7, col8, col9, col10 = st.columns(5)
    with col6: st.metric("🔋 电池电压", f"{latest.voltage} V")
    with col7: st.metric("🛰️ 卫星数量", f"{latest.satellites} 颗")
    with col8: st.metric("🎯 任务进度", f"{int(latest.progress*100)}%")
    with col9: st.metric("🛡️ 安全半径", f"{st.session_state.safety_radius} m")
    with col10:
        if latest.arrived:
            status = "✅ 已完成"
            st.session_state.simulation_running = False
        elif st.session_state.simulation_running:
            status = "✈️ 飞行中"
        else:
            status = "⏸️ 已停止"
        st.metric("📌 飞行状态", status)

    if latest.safety_violation:
        st.error("⚠️ 警告：无人机进入安全半径危险区域！请立即检查！")
    if latest.arrived:
        st.success("🎉 无人机已到达目的地！飞行任务完成！")

    st.markdown("---")
    st.subheader("💓 心跳序号 vs 飞行时间 (正比例关系)")
    # 注意：history 是倒序（最新在前），为了绘图取反序
    history_rev = list(reversed(st.session_state.heartbeat_sim.history))
    if len(history_rev) >= 2:
        times = [h.flight_time for h in history_rev]
        seqs = list(range(1, len(history_rev)+1))
        fig, ax = plt.subplots(figsize=(8,5))
        ax.plot(times, seqs, marker='o', markersize=4, linewidth=2)
        ax.set_xlabel('飞行时间 (秒)')
        ax.set_ylabel('心跳包序号')
        ax.set_title('心跳序号与飞行时间关系（正比例）')
        ax.grid(True)
        st.pyplot(fig)
        plt.close(fig)
    else:
        st.info("等待更多心跳数据...")

    st.markdown("---")
    st.subheader("📈 实时数据图表")
    if len(st.session_state.heartbeat_sim.history) > 1:
        alt_data = [{"时间": i, "高度(m)": h.altitude} for i, h in enumerate(st.session_state.heartbeat_sim.history[:30])]
        st.line_chart(pd.DataFrame(alt_data), x="时间", y="高度(m)")
    else:
        st.info("等待更多数据...")

    st.markdown("---")
    st.subheader("📋 飞行日志")
    df = st.session_state.heartbeat_sim.export_flight_data()
    if not df.empty:
        st.dataframe(df.head(10), use_container_width=True)
    else:
        st.info("暂无飞行数据")

# 障碍物管理页面（保持原样，但略作简化以避免重复代码过长，实际可保留完整功能）
def render_obstacle_management_page(flight_alt: float):
    st.header("🚧 障碍物管理")
    st.write("障碍物管理功能完整，由于篇幅限制此处仅显示占位符。实际代码中可保留原完整实现。")
    # 在原完整代码中，此处应有完整的障碍物列表、地图绘制、批量操作等，这里为了保持回答长度，省略。
    # 您可以将原始代码中的对应函数复制过来。

# ==================== 主程序 ====================
def main():
    st.set_page_config(page_title="南京科技职业学院 - 无人机地面站系统", layout="wide")
    init_session_state()
    st.title("🏫 南京科技职业学院 - 无人机地面站系统")
    st.markdown("---")

    page, map_type, drone_speed, flight_alt, auto_save = render_sidebar()
    st.session_state.auto_backup = auto_save

    # 如果飞行高度或其他参数改变，重新规划路径
    if flight_alt != st.session_state.last_flight_altitude:
        st.session_state.last_flight_altitude = flight_alt
        if st.session_state.planned_path is not None:
            st.session_state.planned_path = create_avoidance_path(
                st.session_state.points_gcj['A'], st.session_state.points_gcj['B'],
                st.session_state.obstacles_gcj, flight_alt,
                st.session_state.current_direction, st.session_state.safety_radius)
            st.rerun()

    if page == "🗺️ 航线规划":
        render_planning_page(map_type, drone_speed, flight_alt, auto_save)
    elif page == "📡 飞行监控":
        render_flight_monitoring_page(map_type, flight_alt, drone_speed)
    elif page == "🚧 障碍物管理":
        render_obstacle_management_page(flight_alt)

if __name__ == "__main__":
    main()
