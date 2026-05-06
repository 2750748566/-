import streamlit as st
import folium
from streamlit_folium import folium_static
from streamlit_folium import st_folium
from folium import plugins
import random
import time
import math
import json
import os
from datetime import datetime
from typing import List, Dict, Optional, Tuple, Any
import pandas as pd
import threading
import matplotlib.pyplot as plt
from dataclasses import dataclass, field
from streamlit.components.v1 import html as components_html

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
    # 以下两个不再用于瓦片，但保留以避免错误
    GAODE_SATELLITE_URL: str = "https://webst01.is.autonavi.com/appmaptile?style=6&x={x}&y={y}&z={z}"
    GAODE_VECTOR_URL: str = "https://webrd02.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}"
    VERTICAL_OFFSET_MULTIPLIER: float = 3.0
    WAYPOINT_OFFSET_FACTOR: float = 10.0

config = Config()
os.makedirs(config.BACKUP_DIR, exist_ok=True)

# 高德地图 JS API Key（请替换为您自己的）
AMAP_JS_KEY = "e261f231ca30f2b7aef79d8b3e5964d2"   # 用户提供的密钥

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
    if o1 == 0 and on_segment(p1, p3, p2): return True
    if o2 == 0 and on_segment(p1, p4, p2): return True
    if o3 == 0 and on_segment(p3, p1, p4): return True
    if o4 == 0 and on_segment(p3, p2, p4): return True
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

def meters_to_deg(meters: float, lat: float = 32.23) -> Tuple[float, float]:
    lat_deg = meters / 111000
    lng_deg = meters / (111000 * math.cos(math.radians(lat)))
    return lng_deg, lat_deg

def point_to_segment_distance_meters(point: List[float], seg_start: List[float], seg_end: List[float]) -> float:
    px, py = point
    x1, y1 = seg_start
    x2, y2 = seg_end
    dx = x2 - x1
    dy = y2 - y1
    len_sq = dx*dx + dy*dy
    if len_sq == 0:
        return math.hypot(px-x1, py-y1) * 111000
    t = ((px - x1)*dx + (py - y1)*dy) / len_sq
    t = max(0, min(1, t))
    proj_x = x1 + t*dx
    proj_y = y1 + t*dy
    return math.hypot(px-proj_x, py-proj_y) * 111000

def check_safety_radius(drone_pos: List[float], obstacles_gcj: List[Dict], flight_altitude: float, safety_radius: float) -> Tuple[bool, Optional[float], Optional[str]]:
    if not drone_pos:
        return True, None, None
    min_dist = float('inf')
    danger_name = None
    for obs in obstacles_gcj:
        if obs.get('height',30) > flight_altitude:
            coords = obs.get('polygon',[])
            if coords and len(coords)>=3:
                for i in range(len(coords)):
                    p1 = coords[i]
                    p2 = coords[(i+1)%len(coords)]
                    d = point_to_segment_distance_meters(drone_pos, p1, p2)
                    if d < min_dist:
                        min_dist = d
                        danger_name = obs.get('name','障碍物')
    if min_dist < safety_radius:
        return False, min_dist, danger_name
    return True, min_dist if min_dist!=float('inf') else None, None

# ==================== 障碍物管理（保持不变） ====================
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
    length = math.hypot(dx, dy)
    if length==0:
        return [start,end]
    perp_x = dy/length
    perp_y = -dx/length
    offset_dist = safety_radius * 10
    lat_rad = math.radians(mid_y)
    lng_scale = 111000 * math.cos(lat_rad)
    lat_scale = 111000
    off_x = perp_x * offset_dist / lng_scale
    off_y = perp_y * offset_dist / lat_scale
    waypoint = [mid_x+off_x, mid_y+off_y]
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

# ==================== 心跳包模拟器（保持不变） ====================
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
        for i in range(len(path)-1):
            self.total_distance += distance(path[i], path[i+1])

    def update_and_generate(self, obstacles_gcj: List[Dict]) -> Optional[HeartbeatData]:
        if not self.simulating or self.path_index >= len(self.path)-1:
            if self.simulating:
                self.simulating = False
            return None
        cur_time = time.time()
        if self.last_update_time is None:
            dt = config.HEARTBEAT_INTERVAL
        else:
            dt = min(0.5, cur_time - self.last_update_time)
        self.last_update_time = cur_time
        start = self.path[self.path_index]
        end = self.path[self.path_index+1]
        seg_len = distance(start, end)
        speed_mps = config.BASE_SPEED_MPS * (self.speed/100)
        move = speed_mps * dt
        self.distance_traveled += move
        if self.total_distance > 0:
            self.progress = min(1.0, self.distance_traveled / self.total_distance)

        if self.distance_traveled >= seg_len and self.distance_traveled>0:
            self.path_index += 1
            self.distance_traveled = 0
            if self.path_index < len(self.path):
                self.current_pos = self.path[self.path_index].copy()
            else:
                self.simulating = False
                return self._generate_heartbeat(True)
        else:
            if seg_len > 0:
                t = max(0, min(1, self.distance_traveled/seg_len))
                lng = start[0] + (end[0]-start[0])*t
                lat = start[1] + (end[1]-start[1])*t
                self.current_pos = [lng, lat]
                safe,_,_ = check_safety_radius(self.current_pos, obstacles_gcj, self.flight_altitude, self.safety_radius)
                if not safe:
                    self.safety_violation = True
        return self._generate_heartbeat(False)

    def _generate_heartbeat(self, arrived):
        flight_t = (datetime.now()-self.start_time).total_seconds() if self.start_time else 0
        remain = max(0, self.total_distance - self.distance_traveled) * 111000
        return HeartbeatData(
            timestamp=datetime.now().strftime("%H:%M:%S"),
            flight_time=flight_t,
            lat=self.current_pos[1],
            lng=self.current_pos[0],
            altitude=self.flight_altitude,
            voltage=round(22.2+random.uniform(-0.5,0.5),1),
            satellites=random.randint(8,14),
            speed=round(config.BASE_SPEED_MPS*(self.speed/100),1),
            progress=self.progress,
            arrived=arrived,
            safety_violation=self.safety_violation,
            remaining_distance=remain
        )

    def export_flight_data(self):
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

# ==================== 后台心跳线程 ====================
def background_heartbeat_worker():
    while True:
        time.sleep(config.HEARTBEAT_INTERVAL)
        if 'simulation_running' in st.session_state and st.session_state.simulation_running:
            sim = st.session_state.get('heartbeat_sim')
            if sim and sim.simulating:
                obstacles = st.session_state.get('obstacles_gcj', [])
                new_hb = sim.update_and_generate(obstacles)
                if new_hb:
                    st.session_state.last_hb_time = time.time()
                if not sim.simulating:
                    st.session_state.simulation_running = False

# ==================== 高德地图 HTML 生成函数 ====================
def create_gaode_map_html(center_lng, center_lat, zoom=16, markers=None, polylines=None, polygons=None, circles=None):
    """
    markers: list of {'lng': lng, 'lat': lat, 'title': str, 'label': str, 'icon_color': str}
    polylines: list of {'path': [[lng,lat],...], 'color': str, 'weight': int, 'dash': bool}
    polygons: list of {'path': [[lng,lat],...], 'color': str, 'fill_color': str, 'popup': str}
    circles: list of {'center': [lng,lat], 'radius': float, 'color': str, 'fill': bool}
    """
    js_markers = []
    if markers:
        for m in markers:
            js_markers.append(f"""
                new AMap.Marker({{
                    position: [{m['lng']}, {m['lat']}],
                    title: '{m['title']}',
                    label: {{ content: '{m.get('label','')}', offset: new AMap.Pixel(0, -20) }},
                    icon: 'https://webapi.amap.com/theme/v1.3/markers/n/mark_{m.get('icon_color','b')}.png'
                }}).setMap(map);
            """)
    js_polylines = []
    if polylines:
        for pl in polylines:
            dash = "strokeStyle: 'dashed'" if pl.get('dash') else ""
            js_polylines.append(f"""
                new AMap.Polyline({{
                    path: {[[p[1], p[0]] for p in pl['path']]},
                    strokeColor: '{pl.get('color','gray')}',
                    strokeWeight: {pl.get('weight',2)},
                    {dash}
                }}).setMap(map);
            """)
    js_polygons = []
    if polygons:
        for pg in polygons:
            js_polygons.append(f"""
                new AMap.Polygon({{
                    path: {[[p[1], p[0]] for p in pg['path']]},
                    strokeColor: '{pg.get('color','red')}',
                    fillColor: '{pg.get('fill_color','red')}',
                    fillOpacity: 0.4,
                    strokeWeight: 2,
                    content: '{pg.get('popup','')}'
                }}).setMap(map);
            """)
    js_circles = []
    if circles:
        for c in circles:
            js_circles.append(f"""
                new AMap.Circle({{
                    center: [{c['center'][0]}, {c['center'][1]}],
                    radius: {c['radius']},
                    strokeColor: '{c.get('color','blue')}',
                    fillColor: '{c.get('color','blue')}',
                    fillOpacity: 0.2
                }}).setMap(map);
            """)
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="initial-scale=1.0, user-scalable=no, width=device-width">
        <title>高德地图</title>
        <style>
            html, body, #container {{ width: 100%; height: 100%; margin: 0; padding: 0; }}
        </style>
    </head>
    <body>
        <div id="container"></div>
        <script src="https://webapi.amap.com/maps?v=2.0&key={AMAP_JS_KEY}"></script>
        <script>
            var map = new AMap.Map('container', {{
                center: [{center_lng}, {center_lat}],
                zoom: {zoom},
                viewMode: '2D',
                layers: [new AMap.TileLayer.Satellite()]
            }});
            {''.join(js_markers)}
            {''.join(js_polylines)}
            {''.join(js_polygons)}
            {''.join(js_circles)}
        </script>
    </body>
    </html>
    """
    return html_content

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
        'bg_thread_started': False
    }
    for k,v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v
    for obs in st.session_state.obstacles_gcj:
        if 'height' not in obs: obs['height'] = 30
        if 'selected' not in obs: obs['selected'] = False
    if not st.session_state.get('bg_thread_started', False):
        thread = threading.Thread(target=background_heartbeat_worker, daemon=True)
        thread.start()
        st.session_state.bg_thread_started = True

def check_straight_blocked(points_gcj, obstacles_gcj, flight_altitude):
    blocked = False
    high_count = 0
    for obs in obstacles_gcj:
        if obs.get('height',30) > flight_altitude:
            high_count += 1
            if line_intersects_polygon(points_gcj['A'], points_gcj['B'], obs.get('polygon',[])):
                blocked = True
    return blocked, high_count

def render_sidebar():
    st.sidebar.title("🎛️ 导航菜单")
    page = st.sidebar.radio("选择功能模块", ["🗺️ 航线规划", "📡 飞行监控", "🚧 障碍物管理"])
    map_type_choice = st.sidebar.radio("🗺️ 地图类型", ["卫星影像", "矢量街道"], index=0)  # 保留选项但实际不再使用，均使用卫星图
    map_type = "satellite"
    st.sidebar.markdown("---")
    st.sidebar.subheader("⚡ 无人机速度设置")
    drone_speed = st.sidebar.slider("飞行速度系数", 10, 100, 50, 5)
    st.sidebar.markdown("---")
    st.sidebar.subheader("✈️ 无人机飞行高度")
    flight_alt = st.sidebar.slider("飞行高度 (m)", 10, 200, 50, 5)
    st.sidebar.markdown("---")
    st.sidebar.subheader("🛡️ 安全半径设置")
    safety_radius = st.sidebar.slider("安全半径 (米)", 1, 20, st.session_state.safety_radius, 1)
    st.sidebar.markdown("---")
    st.sidebar.subheader("💾 自动保存")
    auto_save = st.sidebar.checkbox("自动保存障碍物", value=st.session_state.auto_backup)
    return page, map_type, drone_speed, flight_alt, auto_save

# ==================== 页面渲染函数 ====================
def render_planning_page(map_type, drone_speed, flight_alt, auto_save):
    st.header("🗺️ 航线规划 - 智能避障")
    straight_blocked, high_obstacles = check_straight_blocked(st.session_state.points_gcj, st.session_state.obstacles_gcj, flight_alt)
    if straight_blocked:
        st.warning(f"⚠️ 有 {high_obstacles} 个障碍物高于飞行高度({flight_alt}m)，需要绕行")
    else:
        st.success("✅ 直线航线畅通无阻（所有障碍物高度 ≤ 飞行高度）")
    st.info("📝 使用左侧面板设置起点/终点，规划路径后点击「开始飞行」")
    col1, col2 = st.columns([1, 1.5])
    with col1:
        render_planning_controls(flight_alt, drone_speed, auto_save)
    with col2:
        render_planning_map_view(flight_alt)

def render_planning_controls(flight_alt, drone_speed, auto_save):
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
    dist = math.hypot(b[0]-a[0], b[1]-a[1]) * 111000
    st.caption(f"📏 直线距离: {dist:.0f} 米")

def render_point_settings():
    st.markdown("#### 🟢 起点 A")
    col_a1, col_a2 = st.columns(2)
    with col_a1:
        a_lat = st.number_input("纬度", value=st.session_state.points_gcj['A'][1], format="%.6f", key="a_lat", step=0.000001)
    with col_a2:
        a_lng = st.number_input("经度", value=st.session_state.points_gcj['A'][0], format="%.6f", key="a_lng", step=0.000001)
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
        b_lat = st.number_input("纬度", value=st.session_state.points_gcj['B'][1], format="%.6f", key="b_lat", step=0.000001)
    with col_b2:
        b_lng = st.number_input("经度", value=st.session_state.points_gcj['B'][0], format="%.6f", key="b_lng", step=0.000001)
    if st.button("📍 设置 B 点", use_container_width=True):
        st.session_state.points_gcj['B'] = [b_lng, b_lat]
        st.session_state.planned_path = create_avoidance_path(
            st.session_state.points_gcj['A'], st.session_state.points_gcj['B'],
            st.session_state.obstacles_gcj, st.session_state.last_flight_altitude,
            st.session_state.current_direction, st.session_state.safety_radius)
        st.rerun()

def render_path_strategy(flight_alt):
    st.markdown("**选择绕行方向：**")
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("🔄 最佳航线", use_container_width=True, type="primary" if st.session_state.current_direction=="最佳航线" else "secondary"):
            st.session_state.current_direction = "最佳航线"
            st.session_state.planned_path = create_avoidance_path(
                st.session_state.points_gcj['A'], st.session_state.points_gcj['B'],
                st.session_state.obstacles_gcj, flight_alt, "最佳航线", st.session_state.safety_radius)
            st.success("已切换到最佳航线模式")
            st.rerun()
    with col2:
        if st.button("⬅️ 向左绕行", use_container_width=True, type="primary" if st.session_state.current_direction=="向左绕行" else "secondary"):
            st.session_state.current_direction = "向左绕行"
            st.session_state.planned_path = create_avoidance_path(
                st.session_state.points_gcj['A'], st.session_state.points_gcj['B'],
                st.session_state.obstacles_gcj, flight_alt, "向左绕行", st.session_state.safety_radius)
            st.success("已切换到向左绕行模式")
            st.rerun()
    with col3:
        if st.button("➡️ 向右绕行", use_container_width=True, type="primary" if st.session_state.current_direction=="向右绕行" else "secondary"):
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
            waypoint_count = len(st.session_state.planned_path)-2
            st.success(f"已按照「{st.session_state.current_direction}」规划路径，{waypoint_count}个绕行点")
            st.rerun()

def render_flight_controls(flight_alt, drone_speed):
    col1, col2, col3 = st.columns(3)
    with col1: st.metric("当前飞行高度", f"{flight_alt} m")
    with col2: st.metric("速度系数", f"{drone_speed}%")
    with col3: st.metric("🛡️ 安全半径", f"{st.session_state.safety_radius} 米")
    if st.session_state.planned_path:
        waypoint_count = len(st.session_state.planned_path)-2
        st.metric("🎯 绕行点数量", waypoint_count)
        total_dist = calculate_path_length(st.session_state.planned_path)*111000
        st.caption(f"📏 规划路径总长: {total_dist:.0f} 米")
    col_btn1, col_btn2 = st.columns(2)
    with col_btn1:
        if st.button("▶️ 开始飞行", use_container_width=True, type="primary"):
            if st.session_state.points_gcj['A'] and st.session_state.points_gcj['B']:
                path = st.session_state.planned_path or [st.session_state.points_gcj['A'], st.session_state.points_gcj['B']]
                st.session_state.heartbeat_sim.set_path(path, flight_alt, drone_speed, st.session_state.safety_radius)
                st.session_state.simulation_running = True
                st.session_state.flight_history = []
                waypoint_count = len(path)-2
                st.success(f"🚁 飞行已开始！{'路径中有'+str(waypoint_count)+'个绕行点' if waypoint_count>0 else '直线飞行'}")
                st.rerun()
            else:
                st.error("请先设置起点和终点")
    with col_btn2:
        if st.button("⏹️ 停止飞行", use_container_width=True):
            st.session_state.simulation_running = False
            st.session_state.heartbeat_sim.simulating = False
            st.info("飞行已停止")

def render_planning_map_view(flight_alt):
    st.subheader("🗺️ 规划地图")
    center = st.session_state.points_gcj['A'] or config.SCHOOL_CENTER_GCJ
    center_lng, center_lat = center[0], center[1]
    a = st.session_state.points_gcj['A']
    b = st.session_state.points_gcj['B']
    markers = [
        {'lng': a[0], 'lat': a[1], 'title': '起点 A', 'label': 'A', 'icon_color': 'b'},
        {'lng': b[0], 'lat': b[1], 'title': '终点 B', 'label': 'B', 'icon_color': 'r'}
    ]
    polylines = []
    if st.session_state.planned_path and len(st.session_state.planned_path)>1:
        polylines.append({'path': st.session_state.planned_path, 'color': 'green', 'weight': 4})
    # 直线航线（虚线）
    polylines.append({'path': [a, b], 'color': 'gray', 'weight': 2, 'dash': True})
    polygons = []
    for obs in st.session_state.obstacles_gcj:
        coords = obs.get('polygon',[])
        height = obs.get('height',30)
        if coords and len(coords)>=3:
            color = 'red' if height > flight_alt else 'orange'
            polygons.append({'path': coords, 'color': color, 'fill_color': color, 'popup': f"{obs.get('name')}\n高度:{height}m"})
    # 无人机当前位置（如果正在飞行）
    drone_pos = None
    if st.session_state.simulation_running and st.session_state.heartbeat_sim.history:
        drone_pos = (st.session_state.heartbeat_sim.current_pos[0], st.session_state.heartbeat_sim.current_pos[1])
        markers.append({'lng': drone_pos[0], 'lat': drone_pos[1], 'title': '无人机', 'label': '✈️', 'icon_color': 'blue'})
    circles = []
    if drone_pos:
        circles.append({'center': drone_pos, 'radius': st.session_state.safety_radius, 'color': 'blue'})
    html = create_gaode_map_html(center_lng, center_lat, zoom=16, markers=markers, polylines=polylines, polygons=polygons, circles=circles)
    components_html(html, width=700, height=550)

# ==================== 飞行监控页面 ====================
def render_flight_monitoring_page(map_type, flight_alt, drone_speed):
    st.header("📡 飞行监控 - 实时心跳包")
    if not st.session_state.heartbeat_sim.history:
        st.info("⏳ 等待心跳数据... 请在「航线规划」页面点击「开始飞行」")
        st.info("💡 提示：先设置起点和终点，调整参数，再点击开始飞行")
        return
    latest = st.session_state.heartbeat_sim.history[0]
    progress_percent = int(latest.progress*100)
    st.progress(latest.progress, text=f"飞行进度：{progress_percent}%")
    col1, col2, col3, col4, col5 = st.columns(5)
    with col1: st.metric("⏰ 飞行时间", f"{latest.flight_time:.1f}s")
    with col2: st.metric("📍 当前位置", f"{latest.lat:.6f}, {latest.lng:.6f}")
    with col3: st.metric("📏 飞行高度", f"{latest.altitude} m")
    with col4: st.metric("💨 当前速度", f"{latest.speed} m/s", delta=f"{drone_speed}%")
    with col5: st.metric("📏 剩余距离", f"{latest.remaining_distance:.0f} m")
    col6, col7, col8, col9, col10 = st.columns(5)
    with col6: st.metric("🔋 电池电压", f"{latest.voltage} V")
    with col7: st.metric("🛰️ 卫星数量", f"{latest.satellites} 颗")
    with col8: st.metric("🎯 任务进度", f"{progress_percent}%")
    with col9: st.metric("🛡️ 安全半径", f"{st.session_state.safety_radius} m")
    with col10:
        if latest.arrived: status="✅ 已完成"
        elif st.session_state.simulation_running: status="✈️ 飞行中"
        else: status="⏸️ 已停止"
        st.metric("📌 飞行状态", status)
    if latest.safety_violation:
        st.error("⚠️ 警告：无人机进入安全半径危险区域！请立即检查！")
    if latest.arrived:
        st.success("🎉 无人机已到达目的地！飞行任务完成！")
    st.markdown("---")
    st.subheader("💓 心跳序号 vs 飞行时间 (正比例关系)")
    history_rev = list(reversed(st.session_state.heartbeat_sim.history))
    if len(history_rev) >= 2:
        times = [h.flight_time for h in history_rev]
        seqs = list(range(1, len(history_rev)+1))
        fig, ax = plt.subplots(figsize=(8,5))
        ax.plot(times, seqs, marker='o', markersize=4, linewidth=2)
        ax.set_xlabel('飞行时间 (秒)')
        ax.set_ylabel('心跳包序号')
        ax.set_title('心跳序号与飞行时间关系（正比例）')
        ax.grid(True, linestyle='--', alpha=0.6)
        st.pyplot(fig)
        plt.close(fig)
    else:
        st.info("等待足够的心跳数据（至少2个）...")
    st.markdown("---")
    st.markdown("### 🗺️ 实时位置追踪")
    # 实时地图（使用高德 JS API，但为了实时更新，我们用自动刷新页面来重建地图，简单且保证显示）
    # 此处为了简化，直接重新生成地图，通过 st.rerun 自动刷新（页面每秒刷新）
    # 我们将在主循环中通过 st_autorefresh 实现刷新，这里只显示地图
    center = [st.session_state.heartbeat_sim.current_pos[0], st.session_state.heartbeat_sim.current_pos[1]]
    a = st.session_state.points_gcj['A']
    b = st.session_state.points_gcj['B']
    markers = [
        {'lng': a[0], 'lat': a[1], 'title': '起点 A', 'label': 'A', 'icon_color': 'b'},
        {'lng': b[0], 'lat': b[1], 'title': '终点 B', 'label': 'B', 'icon_color': 'r'},
        {'lng': center[0], 'lat': center[1], 'title': '无人机', 'label': '✈️', 'icon_color': 'blue'}
    ]
    polylines = []
    if st.session_state.planned_path:
        polylines.append({'path': st.session_state.planned_path, 'color': 'green', 'weight': 3})
        polylines.append({'path': [a, b], 'color': 'gray', 'weight': 2, 'dash': True})
    polygons = []
    for obs in st.session_state.obstacles_gcj:
        coords = obs.get('polygon',[])
        height = obs.get('height',30)
        if coords and len(coords)>=3:
            color = 'red' if height > flight_alt else 'orange'
            polygons.append({'path': coords, 'color': color, 'fill_color': color, 'popup': f"{obs.get('name')}\n高度:{height}m"})
    circles = [{'center': center, 'radius': st.session_state.safety_radius, 'color': 'blue'}]
    html = create_gaode_map_html(center[0], center[1], zoom=18, markers=markers, polylines=polylines, polygons=polygons, circles=circles)
    components_html(html, width=900, height=500)
    st.markdown("---")
    st.markdown("### 📈 实时数据图表")
    if len(st.session_state.heartbeat_sim.history) > 1:
        alt_df = pd.DataFrame([{"时间":i, "高度(m)":h.altitude} for i, h in enumerate(st.session_state.heartbeat_sim.history[:30])])
        st.line_chart(alt_df, x="时间", y="高度(m)")
        speed_df = pd.DataFrame([{"时间":i, "速度(m/s)":h.speed} for i, h in enumerate(st.session_state.heartbeat_sim.history[:30])])
        st.line_chart(speed_df, x="时间", y="速度(m/s)")
    st.markdown("### 📋 飞行日志记录")
    display_flight_history()
    col_export1, col_export2, col_export3 = st.columns(3)
    with col_export1:
        if st.button("📊 导出完整飞行数据", use_container_width=True, type="primary"):
            df = st.session_state.heartbeat_sim.export_flight_data()
            if not df.empty:
                csv = df.to_csv(index=False)
                st.download_button("📥 下载CSV文件", data=csv, file_name=f"flight_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv", mime="text/csv", use_container_width=True)
    with col_export2:
        if st.button("🔄 刷新数据", use_container_width=True):
            st.rerun()
    with col_export3:
        if st.button("⏹️ 停止飞行", use_container_width=True):
            st.session_state.simulation_running = False
            st.session_state.heartbeat_sim.simulating = False
            st.success("飞行已停止")
            st.rerun()

def display_flight_history():
    df = st.session_state.heartbeat_sim.export_flight_data()
    if not df.empty:
        display_cols = ['timestamp','flight_time','lat','lng','altitude','speed','voltage','satellites','remaining_distance']
        display_cols = [c for c in display_cols if c in df.columns]
        recent = df[display_cols].head(10)
        rename = {'timestamp':'时间','flight_time':'飞行时间(s)','lat':'纬度','lng':'经度','altitude':'高度(m)','speed':'速度(m/s)','voltage':'电压(V)','satellites':'卫星数','remaining_distance':'剩余距离(m)'}
        recent = recent.rename(columns=rename)
        st.dataframe(recent, use_container_width=True)
        col1, col2, col3, col4 = st.columns(4)
        with col1: st.metric("🏁 最高速度", f"{df['speed'].max():.1f} m/s")
        with col2: st.metric("📈 平均速度", f"{df['speed'].mean():.1f} m/s")
        with col3: st.metric("⛰️ 最高高度", f"{df['altitude'].max():.0f} m")
        with col4: st.metric("⏱️ 总飞行时间", f"{df['flight_time'].max():.1f} s")
    else:
        st.info("暂无飞行数据")

# ==================== 障碍物管理页面 ====================
def render_obstacle_management_page(flight_alt):
    st.header("🚧 障碍物管理")
    col1, col2, col3, col4 = st.columns(4)
    with col1: st.info(f"📊 当前共 {len(st.session_state.obstacles_gcj)} 个障碍物")
    with col2: st.info(f"🛡️ 安全半径: {st.session_state.safety_radius}米")
    with col3: st.info(f"💾 自动保存: {'开启' if st.session_state.auto_backup else '关闭'}")
    with col4:
        backup_count = len([f for f in os.listdir(config.BACKUP_DIR) if f.startswith(config.CONFIG_FILE) and f.endswith('.bak')])
        st.info(f"📦 备份数量: {backup_count}")
    st.markdown("---")
    col_data1, col_data2, col_data3, col_data4, col_data5 = st.columns(5)
    with col_data1:
        if st.button("💾 保存配置", use_container_width=True, type="primary"):
            if save_obstacles(st.session_state.obstacles_gcj):
                st.success(f"✅ 已保存 {len(st.session_state.obstacles_gcj)} 个障碍物")
                st.rerun()
    with col_data2:
        if st.button("📂 加载配置", use_container_width=True):
            loaded = load_obstacles()
            if loaded:
                st.session_state.obstacles_gcj = loaded
                st.session_state.planned_path = create_avoidance_path(
                    st.session_state.points_gcj['A'], st.session_state.points_gcj['B'],
                    loaded, flight_alt, st.session_state.current_direction, st.session_state.safety_radius)
                st.success(f"✅ 已加载 {len(loaded)} 个障碍物")
                st.rerun()
            else:
                st.warning("⚠️ 未找到配置文件")
    with col_data3:
        if st.session_state.obstacles_gcj:
            json_str = json.dumps({'obstacles': st.session_state.obstacles_gcj, 'export_time': datetime.now().isoformat()}, ensure_ascii=False, indent=2)
            st.download_button("📥 导出配置", data=json_str, file_name=f"obstacles_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json", mime="application/json", use_container_width=True)
        else:
            st.button("📥 导出配置", disabled=True, use_container_width=True)
    with col_data4:
        latest_backup = get_latest_backup()
        if latest_backup and st.button("🔄 恢复备份", use_container_width=True):
            if restore_from_backup(latest_backup):
                st.session_state.obstacles_gcj = load_obstacles()
                st.session_state.planned_path = create_avoidance_path(
                    st.session_state.points_gcj['A'], st.session_state.points_gcj['B'],
                    st.session_state.obstacles_gcj, flight_alt, st.session_state.current_direction, st.session_state.safety_radius)
                st.success("✅ 已从备份恢复")
                st.rerun()
            else:
                st.error("❌ 恢复失败")
    with col_data5:
        if st.button("🗑️ 清除全部", use_container_width=True):
            if st.session_state.auto_backup: backup_config()
            st.session_state.obstacles_gcj = []
            save_obstacles([])
            st.session_state.planned_path = create_avoidance_path(
                st.session_state.points_gcj['A'], st.session_state.points_gcj['B'],
                [], flight_alt, st.session_state.current_direction, st.session_state.safety_radius)
            st.success("✅ 已清除所有障碍物")
            st.rerun()
    st.markdown("---")
    col_stats1, col_stats2, col_stats3, col_stats4 = st.columns(4)
    high = sum(1 for o in st.session_state.obstacles_gcj if o.get('height',30)>flight_alt)
    with col_stats1: st.metric("🔴 需避让障碍物", high)
    with col_stats2: st.metric("🟠 安全障碍物", len(st.session_state.obstacles_gcj)-high)
    with col_stats3: st.metric("📍 总顶点数", sum(len(o.get('polygon',[])) for o in st.session_state.obstacles_gcj))
    with col_stats4:
        avg_h = sum(o.get('height',30) for o in st.session_state.obstacles_gcj)/max(1,len(st.session_state.obstacles_gcj))
        st.metric("📏 平均高度", f"{avg_h:.1f}m")
    st.markdown("---")
    st.subheader("🎯 批量操作")
    # 全选复选框
    select_all = st.checkbox("☑️ 全选所有障碍物")
    if select_all:
        for o in st.session_state.obstacles_gcj: o['selected'] = True
    col_b1, col_b2, col_b3, col_b4 = st.columns(4)
    with col_b1:
        if st.button("🗑️ 批量删除", use_container_width=True, type="primary"):
            selected = [i for i,o in enumerate(st.session_state.obstacles_gcj) if o.get('selected',False)]
            if selected:
                for i in reversed(selected): st.session_state.obstacles_gcj.pop(i)
                if st.session_state.auto_backup: save_obstacles(st.session_state.obstacles_gcj)
                st.session_state.planned_path = create_avoidance_path(
                    st.session_state.points_gcj['A'], st.session_state.points_gcj['B'],
                    st.session_state.obstacles_gcj, flight_alt, st.session_state.current_direction, st.session_state.safety_radius)
                st.success(f"✅ 已删除 {len(selected)} 个障碍物")
                st.rerun()
            else:
                st.warning("请先选择要删除的障碍物")
    with col_b2:
        batch_h = st.number_input("批量高度(m)", 1,200,30,5, key="batch_h")
        if st.button("📏 批量设置高度", use_container_width=True):
            selected = [i for i,o in enumerate(st.session_state.obstacles_gcj) if o.get('selected',False)]
            if selected:
                for i in selected: st.session_state.obstacles_gcj[i]['height'] = batch_h
                if st.session_state.auto_backup: save_obstacles(st.session_state.obstacles_gcj)
                st.session_state.planned_path = create_avoidance_path(
                    st.session_state.points_gcj['A'], st.session_state.points_gcj['B'],
                    st.session_state.obstacles_gcj, flight_alt, st.session_state.current_direction, st.session_state.safety_radius)
                st.success(f"✅ 已为 {len(selected)} 个障碍物设置高度为 {batch_h}m")
                st.rerun()
            else:
                st.warning("请先选择障碍物")
    with col_b3:
        if st.button("🏷️ 批量重命名", use_container_width=True):
            selected = [i for i,o in enumerate(st.session_state.obstacles_gcj) if o.get('selected',False)]
            if selected:
                st.session_state.show_rename_dialog = True
            else:
                st.warning("请先选择障碍物")
    with col_b4:
        pass
    if st.session_state.get('show_rename_dialog', False):
        with st.expander("批量重命名", expanded=True):
            prefix = st.text_input("名称前缀", "建筑物")
            start_no = st.number_input("起始编号", 1, 1000, 1, 1)
            suffix = st.text_input("名称后缀", "")
            if st.button("确认重命名"):
                selected = [i for i,o in enumerate(st.session_state.obstacles_gcj) if o.get('selected',False)]
                for idx, i in enumerate(selected):
                    new_name = f"{prefix}{start_no+idx}{suffix}"
                    st.session_state.obstacles_gcj[i]['name'] = new_name
                if st.session_state.auto_backup: save_obstacles(st.session_state.obstacles_gcj)
                st.session_state.show_rename_dialog = False
                st.rerun()
    st.markdown("---")
    tab_list, tab_map = st.tabs(["📋 列表视图", "🗺️ 地图视图"])
    with tab_list:
        render_obstacle_list_view(flight_alt)
    with tab_map:
        render_obstacle_map_view(flight_alt)

def render_obstacle_list_view(flight_alt):
    st.subheader("📝 障碍物列表")
    if not st.session_state.obstacles_gcj:
        st.info("暂无障碍物")
        return
    for idx, obs in enumerate(st.session_state.obstacles_gcj):
        with st.container(border=True):
            col1, col2 = st.columns([1,5])
            with col1:
                checked = st.checkbox("", key=f"sel_{idx}", value=obs.get('selected',False))
                st.session_state.obstacles_gcj[idx]['selected'] = checked
            with col2:
                color = "🔴" if obs.get('height',30) > flight_alt else "🟠"
                st.markdown(f"**{color} {obs.get('name', f'障碍物{idx+1}')}**")
            col_h1, col_h2 = st.columns(2)
            with col_h1: st.caption(f"📏 高度: {obs.get('height',30)}m")
            with col_h2: st.caption(f"📍 顶点: {len(obs.get('polygon',[]))}个")
            new_h = st.number_input("调整高度", value=obs.get('height',30), min_value=1, max_value=200, step=5, key=f"h_{idx}", label_visibility="collapsed")
            if new_h != obs.get('height',30):
                obs['height'] = new_h
                if st.session_state.auto_backup: save_obstacles(st.session_state.obstacles_gcj)
                st.session_state.planned_path = create_avoidance_path(
                    st.session_state.points_gcj['A'], st.session_state.points_gcj['B'],
                    st.session_state.obstacles_gcj, flight_alt, st.session_state.current_direction, st.session_state.safety_radius)
                st.rerun()
            if st.button("🗑️ 删除", key=f"del_{idx}", use_container_width=True):
                st.session_state.obstacles_gcj.pop(idx)
                if st.session_state.auto_backup: save_obstacles(st.session_state.obstacles_gcj)
                st.session_state.planned_path = create_avoidance_path(
                    st.session_state.points_gcj['A'], st.session_state.points_gcj['B'],
                    st.session_state.obstacles_gcj, flight_alt, st.session_state.current_direction, st.session_state.safety_radius)
                st.rerun()

def render_obstacle_map_view(flight_alt):
    st.subheader("🗺️ 地图视图")
    st.caption("✏️ 使用左上角绘制工具绘制新障碍物（此功能需要高德地图绘图插件，暂未集成，请在列表视图中添加或手动编辑JSON）")
    # 显示静态地图
    center = config.SCHOOL_CENTER_GCJ
    a = st.session_state.points_gcj['A']
    b = st.session_state.points_gcj['B']
    markers = [
        {'lng': a[0], 'lat': a[1], 'title': '起点 A', 'label': 'A', 'icon_color': 'b'},
        {'lng': b[0], 'lat': b[1], 'title': '终点 B', 'label': 'B', 'icon_color': 'r'}
    ]
    polygons = []
    for obs in st.session_state.obstacles_gcj:
        coords = obs.get('polygon',[])
        height = obs.get('height',30)
        if coords and len(coords)>=3:
            color = 'red' if height > flight_alt else 'orange'
            polygons.append({'path': coords, 'color': color, 'fill_color': color, 'popup': f"{obs.get('name')}\n高度:{height}m"})
    html = create_gaode_map_html(center[0], center[1], zoom=16, markers=markers, polygons=polygons)
    components_html(html, width=800, height=550)

# ==================== 主程序 ====================
def main():
    st.set_page_config(page_title="南京科技职业学院 - 无人机地面站系统", layout="wide")
    init_session_state()
    st.title("🏫 南京科技职业学院 - 无人机地面站系统")
    st.markdown("---")
    page, map_type, drone_speed, flight_alt, auto_save = render_sidebar()
    st.session_state.auto_backup = auto_save
    if flight_alt != st.session_state.last_flight_altitude:
        st.session_state.last_flight_altitude = flight_alt
        if st.session_state.planned_path is not None:
            st.session_state.planned_path = create_avoidance_path(
                st.session_state.points_gcj['A'], st.session_state.points_gcj['B'],
                st.session_state.obstacles_gcj, flight_alt, st.session_state.current_direction, st.session_state.safety_radius)
            st.rerun()
    if page == "🗺️ 航线规划":
        render_planning_page(map_type, drone_speed, flight_alt, auto_save)
    elif page == "📡 飞行监控":
        render_flight_monitoring_page(map_type, flight_alt, drone_speed)
    elif page == "🚧 障碍物管理":
        render_obstacle_management_page(flight_alt)

if __name__ == "__main__":
    main()
