import streamlit as st
import folium
from streamlit_folium import st_folium, folium_static
import json
import os
import math
import time
import random
from datetime import datetime
import pandas as pd
import matplotlib.pyplot as plt
from streamlit_autorefresh import st_autorefresh

# --------------------------------------------- 配置 ------------------------------------------------
SCHOOL_CENTER_GCJ = [118.749413, 32.234097]
GAODE_TILE = "https://webst01.is.autonavi.com/appmaptile?style=6&x={x}&y={y}&z={z}"
HEARTBEAT_INTERVAL = 0.2
BASE_SPEED = 5.0
CONFIG_FILE = "obstacle_config.json"

# --------------------------------------------- 坐标转换函数 -----------------------------------------
# 导入专业坐标转换库
from coord_convert.transform import wgs2gcj, gcj2wgs

def transform_to_gcj02(lng, lat, from_coord):
    """将输入的坐标转换为GCJ-02坐标系"""
    if from_coord == "WGS-84":
        # 使用专业的 wgs2gcj 函数进行转换
        gcj_lng, gcj_lat = wgs2gcj(lng, lat)
        return gcj_lng, gcj_lat
    # 如果已经是GCJ-02，则直接返回原值
    return lng, lat

def transform_to_display(lng, lat, to_coord):
    """将内部的GCJ-02坐标转换为用户界面所需的坐标系显示"""
    if to_coord == "WGS-84":
        # 使用专业的 gcj2wgs 函数进行转换
        wgs_lng, wgs_lat = gcj2wgs(lng, lat)
        return wgs_lng, wgs_lat
    # 如果用户选择显示GCJ-02，则直接返回原值
    return lng, lat

# --------------------------------------------- 障碍物管理 --------------------------------------------
def load_obstacles():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                obstacles = data.get('obstacles', [])
                for obs in obstacles:
                    if 'height' not in obs:
                        obs['height'] = 30
                    if 'selected' not in obs:
                        obs['selected'] = False
                return obstacles
        except:
            return []
    return []

def save_obstacles(obstacles):
    data = {
        'obstacles': obstacles,
        'count': len(obstacles),
        'save_time': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        'version': 'v13.2'
    }
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# --------------------------------------------- 几何辅助函数 ------------------------------------------
def distance(p1, p2):
    return math.hypot(p1[0]-p2[0], p1[1]-p2[1])

def point_in_polygon(point, polygon):
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
        return (min(p[0], r[0]) <= q[0] <= max(p[0], r[0]) and
                min(p[1], r[1]) <= q[1] <= max(p[1], r[1]))
    o1 = orientation(p1,p2,p3)
    o2 = orientation(p1,p2,p4)
    o3 = orientation(p3,p4,p1)
    o4 = orientation(p3,p4,p2)
    if o1 != o2 and o3 != o4:
        return True
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
        p3 = polygon[i]
        p4 = polygon[(i+1)%n]
        if segments_intersect(p1, p2, p3, p4):
            return True
    return False

def get_blocking_obstacles(start, end, obstacles, flight_alt):
    blocking = []
    for obs in obstacles:
        if obs.get('height', 30) > flight_alt:
            coords = obs.get('polygon', [])
            if coords and line_intersects_polygon(start, end, coords):
                blocking.append(obs)
    return blocking

def meters_to_deg(meters, lat=32.23):
    lat_deg = meters / 111000
    lng_deg = meters / (111000 * math.cos(math.radians(lat)))
    return lng_deg, lat_deg

# --------------------------------------------- 绕行算法 ---------------------------------------------
def compute_blocked_bounds(blocking_obs):
    min_lng = float('inf')
    max_lng = -float('inf')
    min_lat = float('inf')
    max_lat = -float('inf')
    for obs in blocking_obs:
        for p in obs.get('polygon', []):
            min_lng = min(min_lng, p[0])
            max_lng = max(max_lng, p[0])
            min_lat = min(min_lat, p[1])
            max_lat = max(max_lat, p[1])
    return min_lng, max_lng, min_lat, max_lat

def find_left_path(start, end, obstacles, flight_alt, safety_radius=5):
    blocking = get_blocking_obstacles(start, end, obstacles, flight_alt)
    if not blocking:
        return [start, end]
    _, _, _, max_lat = compute_blocked_bounds(blocking)
    safe_lat = meters_to_deg(safety_radius * 5)[1]
    y_offset = max_lat + safe_lat
    waypoint_up = [start[0], y_offset]
    waypoint_right = [end[0], y_offset]
    return [start, waypoint_up, waypoint_right, end]

def find_right_path(start, end, obstacles, flight_alt, safety_radius=5):
    blocking = get_blocking_obstacles(start, end, obstacles, flight_alt)
    if not blocking:
        return [start, end]
    _, _, min_lat, _ = compute_blocked_bounds(blocking)
    safe_lat = meters_to_deg(safety_radius * 5)[1]
    y_offset = min_lat - safe_lat
    waypoint_down = [start[0], y_offset]
    waypoint_right = [end[0], y_offset]
    return [start, waypoint_down, waypoint_right, end]

def find_best_path(start, end, obstacles, flight_alt, safety_radius=5):
    left_path = find_left_path(start, end, obstacles, flight_alt, safety_radius)
    right_path = find_right_path(start, end, obstacles, flight_alt, safety_radius)
    left_len = sum(distance(left_path[i], left_path[i+1]) for i in range(len(left_path)-1))
    right_len = sum(distance(right_path[i], right_path[i+1]) for i in range(len(right_path)-1))
    return left_path if left_len <= right_len else right_path

def create_avoidance_path(start, end, obstacles, flight_alt, direction, safety_radius=5):
    if direction == "向左绕行":
        return find_left_path(start, end, obstacles, flight_alt, safety_radius)
    elif direction == "向右绕行":
        return find_right_path(start, end, obstacles, flight_alt, safety_radius)
    else:
        return find_best_path(start, end, obstacles, flight_alt, safety_radius)

# --------------------------------------------- 心跳模拟器 --------------------------------------------
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
        self.end_point = None
        self.total_time = 0.0

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
        self.total_dist = sum(distance(self.path[i], self.path[i+1]) for i in range(len(self.path)-1))
        self.end_point = path[-1][:]
        speed = BASE_SPEED * (self.speed_pct / 100.0)
        self.total_time = self.total_dist / speed if speed > 0 else 0.001
        self._add_heartbeat(seq=1)

    def _add_heartbeat(self, seq=None, arrived=False):
        flight_t = (datetime.now() - self.start_time).total_seconds() if self.start_time else 0
        if seq is None:
            seq = len(self.history) + 1
        hb = HeartbeatData(flight_t, seq, self.current_pos[1], self.current_pos[0], self.altitude)
        self.history.append(hb)
        return hb

    def update_one_step(self):
        if not self.running:
            return None
        now = time.time()
        if self.last_update is None:
            dt = HEARTBEAT_INTERVAL
        else:
            dt = min(HEARTBEAT_INTERVAL, now - self.last_update) if (now - self.last_update) > 0 else HEARTBEAT_INTERVAL
        self.last_update = now
        elapsed = (datetime.now() - self.start_time).total_seconds() if self.start_time else 0
        self.progress = min(1.0, elapsed / self.total_time)
        N = len(self.path)
        if self.progress >= 1.0:
            idx = N - 1
            self.current_pos = self.path[idx][:]
            self.running = False
            return self._add_heartbeat(arrived=True)
        else:
            idx = int(self.progress * (N - 1))
            self.current_pos = self.path[idx][:]
            self.path_idx = idx
            self.traveled = self.progress * self.total_dist
            return self._add_heartbeat()

# --------------------------------------------- 地图创建 ---------------------------------------------
def create_planning_map(center_gcj, points_gcj, obstacles, flight_trail, plan_path, drone_pos_gcj, flight_alt):
    m = folium.Map(location=[center_gcj[1], center_gcj[0]], zoom_start=16, tiles=GAODE_TILE, attr='高德')
    # 障碍物
    for obs in obstacles:
        coords = obs.get('polygon', [])
        height = obs.get('height', 30)
        if coords and len(coords) >= 3:
            color = "red" if height > flight_alt else "orange"
            folium.Polygon([[c[1], c[0]] for c in coords], color=color, weight=2, fill=True, fill_color=color, fill_opacity=0.4,
                           popup=f"🚧 {obs.get('name', '障碍物')}\n高度:{height}m").add_to(m)  # 修改：\\n -> \n
    # 起点/终点
    if points_gcj.get('A'):
        folium.Marker([points_gcj['A'][1], points_gcj['A'][0]], popup='起点A', icon=folium.Icon(color='green')).add_to(m)
    if points_gcj.get('B'):
        folium.Marker([points_gcj['B'][1], points_gcj['B'][0]], popup='终点B', icon=folium.Icon(color='red')).add_to(m)
    # 规划路径（绿色）
    if plan_path and len(plan_path) > 1:
        folium.PolyLine([[p[1],p[0]] for p in plan_path], color='green', weight=4).add_to(m)
    # 历史轨迹（橙色）
    if flight_trail:
        folium.PolyLine([[lat,lng] for lng,lat in flight_trail[-100:]], color='orange', weight=2).add_to(m)
    # 当前位置 - 移除了自定义图标和前缀，避免序列化错误
    if drone_pos_gcj:
        folium.Marker([drone_pos_gcj[1], drone_pos_gcj[0]], icon=folium.Icon(color='blue')).add_to(m)
    return m

# --------------------------------------------- 初始化状态 -------------------------------------------
def init():
    DEFAULT_A_GCJ = [118.746426, 32.232384]
    DEFAULT_B_GCJ = [118.750966, 32.236290]
    defaults = {
        'page': '航线规划',
        'points_gcj': {'A': DEFAULT_A_GCJ.copy(), 'B': DEFAULT_B_GCJ.copy()},
        'sim': HeartbeatSim(DEFAULT_A_GCJ.copy()),
        'flight_started': False,
        'latest_hb': None,
        'hb_list': [],
        'flight_trail': [],
        'plan_path': None,
        'flight_alt': 50,
        'drone_speed': 50,
        'safety_radius': 5,
        'avoid_direction': "最佳航线",
        'coord_sys': 'GCJ-02',
        'obstacles': load_obstacles(),
        'pending_obstacle': None,
        'flight_paused': False,
        'point_select_mode': 'A',
        'pending_click_point': None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

# --------------------------------------------- 主程序 ---------------------------------------------
def main():
    st.set_page_config(page_title="南京科技职业学院 - 无人机地面站", layout="wide")
    st.title("🏫 南京科技职业学院 - 无人机地面站系统")
    init()

    with st.sidebar:
        st.header("📌 导航")
        selected_page = st.radio("功能页面", ["航线规划", "飞行监控", "障碍物管理"], index=["航线规划", "飞行监控", "障碍物管理"].index(st.session_state.page))
        st.session_state.page = selected_page
        st.markdown("---")
        st.subheader("🗺️ 坐标系设置")
        coord_choice = st.radio("输入坐标系", ["WGS-84", "GCJ-02(高德/百度)"], index=0 if st.session_state.coord_sys=="WGS-84" else 1)
        st.session_state.coord_sys = "WGS-84" if coord_choice == "WGS-84" else "GCJ-02"
        st.markdown("---")
        st.subheader("📊 系统状态")
        st.checkbox("A点已设", value=st.session_state.points_gcj.get('A') is not None, disabled=True)
        st.checkbox("B点已设", value=st.session_state.points_gcj.get('B') is not None, disabled=True)
        st.checkbox("飞行进行中", value=st.session_state.flight_started, disabled=True)

    # ------------------------------- 障碍物管理页面 ----------------------------------------
    if st.session_state.page == "障碍物管理":
        st.header("🚧 障碍物配置持久化")
        st.caption(f"配置文件: {os.path.abspath(CONFIG_FILE)} | 版本: v13.2")
        st.info("📂 文件保存在程序同目录下，绝对路径如上所示")
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            if st.button("💾 保存到文件", use_container_width=True):
                save_obstacles(st.session_state.obstacles)
                st.success("保存成功")
        with col2:
            if st.button("📂 从文件加载", use_container_width=True):
                st.session_state.obstacles = load_obstacles()
                st.rerun()
        with col3:
            if st.button("🗑️ 清除全部", use_container_width=True):
                st.session_state.obstacles = []
                save_obstacles([])
                st.rerun()
        with col4:
            if st.button("🚀 一键部署", use_container_width=True):
                st.info("此功能用于部署，示例中未实现")
        st.markdown("---")
        st.subheader("📥 下载配置文件到本地")
        if st.button("📥 下载 obstacle_config.json", use_container_width=True):
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, 'rb') as f:
                    st.download_button("点击下载", data=f, file_name=CONFIG_FILE, mime="application/json")
            else:
                st.warning("配置文件不存在，请先保存")
        st.markdown("---")
        st.subheader("➕ 添加新障碍物（手动输入顶点）")
        with st.form("add_obstacle_form"):
            obs_name = st.text_input("障碍物名称", "新障碍物")
            obs_height = st.number_input("高度 (米)", min_value=1, max_value=200, value=30, step=5)
            st.markdown("#### 顶点坐标 (经度,纬度) 每行一个，格式: 118.749,32.234")
            vertices_text = st.text_area("顶点列表", placeholder="118.746956,32.232945\\n118.747500,32.233000\\n118.747200,32.233500")
            submitted = st.form_submit_button("✅ 添加障碍物")
            if submitted and vertices_text.strip():
                vertices = []
                for line in vertices_text.strip().split('\n'):
                    if ',' in line:
                        parts = line.split(',')
                        try:
                            lng = float(parts[0].strip())
                            lat = float(parts[1].strip())
                            vertices.append([lng, lat])
                        except:
                            pass
                if len(vertices) >= 3:
                    if st.session_state.coord_sys == "WGS-84":
                        vertices = [list(wgs84_to_gcj02(lng, lat)) for lng, lat in vertices]
                    new_obs = {
                        "name": obs_name,
                        "polygon": vertices,
                        "height": obs_height,
                        "selected": False,
                        "id": f"obs_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                    }
                    st.session_state.obstacles.append(new_obs)
                    save_obstacles(st.session_state.obstacles)
                    st.success(f"已添加 {obs_name}")
                    st.rerun()
                else:
                    st.error("至少需要3个顶点")
        st.markdown("---")
        st.subheader(f"📋 当前障碍物列表 (共 {len(st.session_state.obstacles)} 个)")
        for idx, obs in enumerate(st.session_state.obstacles):
            with st.expander(f"{obs.get('name', '未命名')} | 高度: {obs.get('height',30)}m"):
                col_a, col_b, col_c = st.columns([1,1,2])
                with col_a:
                    new_h = st.number_input("调整高度", value=obs.get('height',30), key=f"h_{idx}", step=5)
                    if new_h != obs.get('height',30):
                        obs['height'] = new_h
                        save_obstacles(st.session_state.obstacles)
                        st.rerun()
                with col_b:
                    if st.button("🗑️ 删除", key=f"del_{idx}"):
                        st.session_state.obstacles.pop(idx)
                        save_obstacles(st.session_state.obstacles)
                        st.rerun()
                with col_c:
                    st.code(json.dumps(obs.get('polygon', []), indent=2), language='json')
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                save_time = data.get('save_time', '未知')
            st.info(f"📁 文件状态: 共 {len(data.get('obstacles', []))} 个障碍物 | 保存时间: {save_time} | 版本: {data.get('version', '未知')}")
            st.text(f"路径: {os.path.abspath(CONFIG_FILE)}")

    # ------------------------------- 航线规划页面 ----------------------------------------
    elif st.session_state.page == "航线规划":
        st.header("🗺️ 航线规划")
        col_map, col_panel = st.columns([3, 1.2])

        with col_panel:
            st.markdown("### 🎮 控制面板")
            st.markdown("#### 🖱️ 点击地图设置")
            if st.session_state.flight_started:
                st.warning("飞行任务进行中，无法修改航点。请先停止飞行。")
                select_mode = st.radio("选点模式", ["设置起点(A)", "设置终点(B)"], key="mode_disabled", disabled=True)
            else:
                select_mode = st.radio("选点模式", ["设置起点(A)", "设置终点(B)"], index=0 if st.session_state.point_select_mode=='A' else 1)
                st.session_state.point_select_mode = 'A' if select_mode == "设置起点(A)" else 'B'

            st.markdown("#### ✅ 确认选点")
            if st.button("确定并规划航线", use_container_width=True):
                if st.session_state.pending_click_point is not None:
                    if st.session_state.point_select_mode == 'A':
                        st.session_state.points_gcj['A'] = st.session_state.pending_click_point
                        st.success(f"起点 A 已更新为: ({st.session_state.pending_click_point[0]:.6f}, {st.session_state.pending_click_point[1]:.6f})")
                    else:
                        st.session_state.points_gcj['B'] = st.session_state.pending_click_point
                        st.success(f"终点 B 已更新为: ({st.session_state.pending_click_point[0]:.6f}, {st.session_state.pending_click_point[1]:.6f})")
                    st.session_state.plan_path = create_avoidance_path(
                        st.session_state.points_gcj['A'], st.session_state.points_gcj['B'],
                        st.session_state.obstacles, st.session_state.flight_alt,
                        st.session_state.avoid_direction, st.session_state.safety_radius
                    )
                    st.session_state.pending_click_point = None
                    st.rerun()
                else:
                    st.warning("请先在地图上点击一个位置")
            st.markdown("---")

            st.markdown("#### 📍 起点 A")
            disp_a_lng, disp_a_lat = transform_to_display(st.session_state.points_gcj['A'][0], st.session_state.points_gcj['A'][1], st.session_state.coord_sys)
            col_a1, col_a2 = st.columns(2)
            with col_a1:
                a_lat = st.number_input("纬度", value=disp_a_lat, format="%.6f", key="a_lat")
            with col_a2:
                a_lng = st.number_input("经度", value=disp_a_lng, format="%.6f", key="a_lng")
            if st.button("设置 A 点", use_container_width=True):
                gcj_lng, gcj_lat = transform_to_gcj02(a_lng, a_lat, st.session_state.coord_sys)
                st.session_state.points_gcj['A'] = [gcj_lng, gcj_lat]
                st.session_state.plan_path = create_avoidance_path(
                    st.session_state.points_gcj['A'], st.session_state.points_gcj['B'],
                    st.session_state.obstacles, st.session_state.flight_alt,
                    st.session_state.avoid_direction, st.session_state.safety_radius)
                st.rerun()

            st.markdown("#### 📍 终点 B")
            disp_b_lng, disp_b_lat = transform_to_display(st.session_state.points_gcj['B'][0], st.session_state.points_gcj['B'][1], st.session_state.coord_sys)
            col_b1, col_b2 = st.columns(2)
            with col_b1:
                b_lat = st.number_input("纬度", value=disp_b_lat, format="%.6f", key="b_lat")
            with col_b2:
                b_lng = st.number_input("经度", value=disp_b_lng, format="%.6f", key="b_lng")
            if st.button("设置 B 点", use_container_width=True):
                gcj_lng, gcj_lat = transform_to_gcj02(b_lng, b_lat, st.session_state.coord_sys)
                st.session_state.points_gcj['B'] = [gcj_lng, gcj_lat]
                st.session_state.plan_path = create_avoidance_path(
                    st.session_state.points_gcj['A'], st.session_state.points_gcj['B'],
                    st.session_state.obstacles, st.session_state.flight_alt,
                    st.session_state.avoid_direction, st.session_state.safety_radius)
                st.rerun()

            st.markdown("---")
            st.subheader("✈️ 飞行参数")
            new_alt = st.slider("飞行高度 (m)", 10, 200, st.session_state.flight_alt, 5)
            if new_alt != st.session_state.flight_alt:
                st.session_state.flight_alt = new_alt
                st.session_state.plan_path = create_avoidance_path(
                    st.session_state.points_gcj['A'], st.session_state.points_gcj['B'],
                    st.session_state.obstacles, new_alt,
                    st.session_state.avoid_direction, st.session_state.safety_radius)
                st.rerun()
            new_speed = st.slider("速度系数 (%)", 10, 100, st.session_state.drone_speed, 5)
            st.session_state.drone_speed = new_speed
            new_radius = st.slider("安全半径 (米)", 1, 20, st.session_state.safety_radius, 1)
            if new_radius != st.session_state.safety_radius:
                st.session_state.safety_radius = new_radius
                st.session_state.plan_path = create_avoidance_path(
                    st.session_state.points_gcj['A'], st.session_state.points_gcj['B'],
                    st.session_state.obstacles, st.session_state.flight_alt,
                    st.session_state.avoid_direction, new_radius)
                st.rerun()
            st.markdown("---")
            st.subheader("🤖 避障策略")
            direction = st.radio("绕行方向", ["最佳航线", "向左绕行", "向右绕行"], index=["最佳航线", "向左绕行", "向右绕行"].index(st.session_state.avoid_direction))
            if direction != st.session_state.avoid_direction:
                st.session_state.avoid_direction = direction
                st.session_state.plan_path = create_avoidance_path(
                    st.session_state.points_gcj['A'], st.session_state.points_gcj['B'],
                    st.session_state.obstacles, st.session_state.flight_alt,
                    direction, st.session_state.safety_radius)
                st.rerun()
            st.markdown("---")
            col_start, col_stop = st.columns(2)
            with col_start:
                if st.button("▶️ 开始飞行", type="primary", use_container_width=True):
                    a = st.session_state.points_gcj.get('A')
                    b = st.session_state.points_gcj.get('B')
                    if a and b:
                        path = st.session_state.plan_path if st.session_state.plan_path else [a, b]
                        st.session_state.sim = HeartbeatSim(a.copy())
                        st.session_state.sim.set_path(path, st.session_state.flight_alt, st.session_state.drone_speed)
                        st.session_state.latest_hb = st.session_state.sim.history[-1] if st.session_state.sim.history else None
                        st.session_state.hb_list = [st.session_state.latest_hb] if st.session_state.latest_hb else []
                        st.session_state.flight_trail = [[st.session_state.latest_hb.lng, st.session_state.latest_hb.lat]] if st.session_state.latest_hb else []
                        st.session_state.flight_started = True
                        st.session_state.flight_paused = False
                        st.success("飞行已开始，切换至「飞行监控」查看动态")
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
            if st.session_state.plan_path:
                waypoint_count = len(st.session_state.plan_path) - 2
                if waypoint_count > 0:
                    st.info(f"当前航线包含 {waypoint_count} 个绕行点")
                else:
                    st.success("直线航线，无绕行")

        # ---------- 右侧可交互地图 ----------
        with col_map:
            if st.session_state.plan_path is None and st.session_state.points_gcj.get('A') and st.session_state.points_gcj.get('B'):
                st.session_state.plan_path = create_avoidance_path(
                    st.session_state.points_gcj['A'], st.session_state.points_gcj['B'],
                    st.session_state.obstacles, st.session_state.flight_alt,
                    st.session_state.avoid_direction, st.session_state.safety_radius)

            drone_pos_gcj = None
            if st.session_state.flight_started and not st.session_state.flight_paused and st.session_state.latest_hb:
                drone_pos_gcj = [st.session_state.latest_hb.lng, st.session_state.latest_hb.lat]

            folium_map = create_planning_map(
                SCHOOL_CENTER_GCJ, st.session_state.points_gcj,
                st.session_state.obstacles, st.session_state.flight_trail,
                st.session_state.plan_path, drone_pos_gcj,
                st.session_state.flight_alt
            )

            # 不再添加 DivIcon 提示，避免 JSON 序列化错误
            map_output = st_folium(folium_map, width=700, height=550, key="planning_map")

            if (not st.session_state.flight_started) and map_output and map_output.get("last_clicked"):
                lat_click = map_output["last_clicked"]["lat"]
                lng_click = map_output["last_clicked"]["lng"]
                gcj_lng, gcj_lat = wgs84_to_gcj02(lng_click, lat_click)
                clicked_gcj = [gcj_lng, gcj_lat]
                st.session_state.pending_click_point = clicked_gcj
                st.info(f"已暂存{'起点' if st.session_state.point_select_mode == 'A' else '终点'}：({gcj_lng:.6f}, {gcj_lat:.6f})，请点击「确定并规划航线」")
                st.rerun()

    # ------------------------------- 飞行监控页面 ----------------------------------------
    else:
        st.header("📡 飞行实时画面 - 任务执行监控")
        st_autorefresh(interval=3000, key="monitor_auto")
        if not st.session_state.flight_started:
            st.info("⏳ 飞行未开始。请切换到「航线规划」页面，设置起点终点后点击「开始飞行」。")
            st.stop()
        if not st.session_state.flight_paused and st.session_state.sim.running:
            steps = max(1, int(1.0 / HEARTBEAT_INTERVAL))
            for _ in range(steps):
                new_hb = st.session_state.sim.update_one_step()
                if new_hb:
                    st.session_state.latest_hb = new_hb
                    st.session_state.hb_list.insert(0, new_hb)
                    if len(st.session_state.hb_list) > 200:
                        st.session_state.hb_list.pop()
                    st.session_state.flight_trail.append([new_hb.lng, new_hb.lat])
                    if len(st.session_state.flight_trail) > 200:
                        st.session_state.flight_trail.pop(0)
                else:
                    break
        if st.session_state.latest_hb is None:
            st.warning("等待第一个心跳...")
            st.stop()
        hb = st.session_state.latest_hb
        progress = st.session_state.sim.progress
        total_waypoints = len(st.session_state.sim.path)
        current_waypoint = min(st.session_state.sim.path_idx + 1, total_waypoints) if progress < 1.0 else total_waypoints
        speed = BASE_SPEED * (st.session_state.drone_speed / 100.0)
        elapsed = hb.flight_time
        remaining_dist = max(0, (1 - progress) * st.session_state.sim.total_dist * 111000)
        eta_sec = remaining_dist / speed if speed > 0 else 0

        col_btn1, col_btn2, col_btn3, col_btn4 = st.columns(4)
        with col_btn1:
            if st.button("▶️ 开始任务", use_container_width=True):
                if not st.session_state.flight_started:
                    st.session_state.sim = HeartbeatSim(st.session_state.points_gcj['A'].copy())
                    st.session_state.sim.set_path(st.session_state.plan_path, st.session_state.flight_alt, st.session_state.drone_speed)
                    st.session_state.flight_started = True
                    st.session_state.flight_paused = False
                    st.rerun()
                else:
                    st.session_state.flight_paused = False
                    st.rerun()
        with col_btn2:
            if st.button("⏸️ 暂停", use_container_width=True):
                st.session_state.flight_paused = True
                st.rerun()
        with col_btn3:
            if st.button("⏹️ 停止", use_container_width=True):
                st.session_state.flight_started = False
                st.session_state.flight_paused = False
                if st.session_state.sim:
                    st.session_state.sim.running = False
                st.rerun()
        with col_btn4:
            if st.button("🔄 重置", use_container_width=True):
                if st.session_state.plan_path:
                    st.session_state.sim = HeartbeatSim(st.session_state.points_gcj['A'].copy())
                    st.session_state.sim.set_path(st.session_state.plan_path, st.session_state.flight_alt, st.session_state.drone_speed)
                    st.session_state.flight_started = True
                    st.session_state.flight_paused = False
                    st.rerun()
                else:
                    st.error("请先在航线规划页面设置路径")

        col_left, col_right = st.columns([1, 1.5])
        with col_left:
            st.markdown("### 📊 任务状态")
            st.metric("当前航点", f"{current_waypoint} / {total_waypoints}")
            st.progress(progress, text=f"任务进度: {int(progress*100)}%")
            st.metric("飞行速度", f"{speed:.1f} m/s")
            minutes = int(elapsed // 60)
            seconds = int(elapsed % 60)
            st.metric("已用时间", f"{minutes:02d}:{seconds:02d}")
            st.metric("剩余距离", f"{remaining_dist:.0f} m")
            eta_min = int(eta_sec // 60)
            eta_sec_int = int(eta_sec % 60)
            st.metric("预计到达", f"{eta_min:02d}:{eta_sec_int:02d}")
            st.metric("电量模拟", "40%")
            st.markdown("---")
            st.markdown("### 📡 通信链路拓扑与数据流")
            if progress < 1:
                delay = random.uniform(20, 35)
                loss = random.uniform(0, 0.5)
            else:
                delay = 10
                loss = 0
            st.markdown("- **GCS**: 在线")
            st.markdown("- **OBC**: 在线")
            st.markdown("- **FCU**: 在线")
            st.markdown("#### 链路统计:")
            st.markdown(f"- GCS↔OBC: 正常")
            st.markdown(f"- OBC↔FCU: 正常")
            st.markdown(f"- 延迟: ~{delay:.0f}ms")
            st.markdown(f"- 丢包率: {loss:.1f}%")

        with col_right:
            st.subheader("🗺️ 实时飞行地图")
            center = [st.session_state.sim.current_pos[0], st.session_state.sim.current_pos[1]]
            a = st.session_state.points_gcj['A']
            b = st.session_state.points_gcj['B']
            m = folium.Map(location=[center[1], center[0]], zoom_start=18, tiles=GAODE_TILE, attr='高德')
            for obs in st.session_state.obstacles:
                coords = obs.get('polygon', [])
                height = obs.get('height', 30)
                if coords and len(coords) >= 3:
                    color = "red" if height > st.session_state.flight_alt else "orange"
                    folium.Polygon([[c[1], c[0]] for c in coords], color=color, weight=2, fill=True, fill_color=color, fill_opacity=0.4,
                                   popup=f"🚧 {obs.get('name', '障碍物')}\n高度:{height}m").add_to(m)  # 修改：\\n -> \n
            folium.Marker([a[1], a[0]], popup='起点A', icon=folium.Icon(color='green')).add_to(m)
            folium.Marker([b[1], b[0]], popup='终点B', icon=folium.Icon(color='red')).add_to(m)
            if st.session_state.plan_path:
                folium.PolyLine([[p[1],p[0]] for p in st.session_state.plan_path], color='green', weight=4).add_to(m)
            if st.session_state.flight_trail:
                folium.PolyLine([[lat,lng] for lng,lat in st.session_state.flight_trail[-100:]], color='orange', weight=2).add_to(m)
            # 修改：移除自定义图标前缀
            folium.Marker([center[1], center[0]], icon=folium.Icon(color='blue')).add_to(m)
            folium_static(m, width=700, height=500)

        st.markdown("---")
        st.subheader("💓 心跳序号 vs 飞行时间 (正比例关系)")
        history = st.session_state.sim.history
        if len(history) >= 2:
            times = [h.flight_time for h in history]
            seqs = [h.seq for h in history]
            fig, ax = plt.subplots(figsize=(8,4))
            ax.plot(times, seqs, marker='o', markersize=4, linewidth=2)
            ax.set_xlabel('飞行时间 (秒)')
            ax.set_ylabel('心跳包序号')
            ax.set_title('心跳序号与飞行时间关系（正比例）')
            ax.grid(True)
            st.pyplot(fig)
            plt.close(fig)
        else:
            st.info(f"等待更多心跳数据... (当前 {len(history)} 个)")
        st.subheader("📈 实时趋势")
        if len(st.session_state.hb_list) > 1:
            df = pd.DataFrame([{"时间": i, "高度": h.altitude} for i, h in enumerate(st.session_state.hb_list[:50])])
            st.line_chart(df, x="时间", y="高度")
        else:
            st.info("等待更多数据...")

if __name__ == "__main__":
    main()
