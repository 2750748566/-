import streamlit as st
import time
import pandas as pd
from collections import deque
import matplotlib.pyplot as plt
import folium
from streamlit_folium import st_folium

# ----------------------------- 南京科技职业学院地理信息 -----------------------------
CAMPUS_CENTER = [32.235447, 118.743617]  # GeoHack 精确坐标（WGS84）
CAMPUS_ZOOM = 16                         # 缩放级别，看清校园内部
CAMPUS_NAME = "南京科技职业学院"
CAMPUS_ADDRESS = "南京市江北新区欣乐路188号"

# 校园大致边界多边形（根据学校占地740亩和坐标估算，实际使用时请替换为真实边界）
CAMPUS_BOUNDARY = [
    [32.2370, 118.7405],  # 西北角
    [32.2370, 118.7465],  # 东北角
    [32.2335, 118.7465],  # 东南角
    [32.2335, 118.7405],  # 西南角
    [32.2370, 118.7405],  # 闭合回西北角
]

# 校园主要建筑（坐标需根据实际情况精确调整）
CAMPUS_FACILITIES = [
    {"name": "行政楼", "lat": 32.2358, "lon": 118.7432, "icon": "info-sign", "color": "red"},
    {"name": "教学楼群", "lat": 32.2350, "lon": 118.7445, "icon": "education", "color": "red"},
    {"name": "图书馆", "lat": 32.2345, "lon": 118.7428, "icon": "book", "color": "red"},
    {"name": "学生食堂", "lat": 32.2338, "lon": 118.7435, "icon": "cutlery", "color": "red"},
    {"name": "实验实训中心", "lat": 32.2360, "lon": 118.7440, "icon": "cog", "color": "red"},
    {"name": "体育场", "lat": 32.2365, "lon": 118.7450, "icon": "flag", "color": "red"},
]

# ----------------------------- 初始路径（无人机飞行路线） -----------------------------
DEFAULT_POINTS = [
    {"lat": 32.2322, "lon": 118.7858, "name": "校门"},
    {"lat": 32.2330, "lon": 118.7862, "name": "教学楼A"},
    {"lat": 32.2335, "lon": 118.7855, "name": "图书馆"},
    {"lat": 32.2327, "lon": 118.7848, "name": "食堂"},
]

# ----------------------------- 初始化 session_state -----------------------------
if "points" not in st.session_state:
    st.session_state.points = DEFAULT_POINTS.copy()
if "seq" not in st.session_state:
    st.session_state.seq = 0
    st.session_state.last_received = time.time()
    st.session_state.last_heartbeat_info = "等待心跳..."
    st.session_state.timeout_flag = False
if "history" not in st.session_state:
    st.session_state.history = deque(maxlen=200)
if "positions" not in st.session_state:
    st.session_state.positions = deque(maxlen=100)

# ----------------------------- 生成新数据（主线程执行） -----------------------------
seq = st.session_state.seq + 1
st.session_state.seq = seq
now = time.time()

points = st.session_state.points
if len(points) < 2:
    lat, lon = points[0]["lat"], points[0]["lon"] if points else (CAMPUS_CENTER[0], CAMPUS_CENTER[1])
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

st.session_state.last_received = now
st.session_state.last_heartbeat_info = f"序号: {seq}, 时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now))}"
st.session_state.timeout_flag = False

st.session_state.history.append((now, seq))
st.session_state.positions.append((lat, lon, altitude, seq))

# ----------------------------- 页面布局 -----------------------------
st.title("🚁 无人机心跳监控 + 动态航线")
st.markdown("侧边栏可编辑路径点，地图将显示无人机位置。下方显示调试信息。")

# ----------------------------- 侧边栏：路径编辑器 -----------------------------
st.sidebar.header("✈️ 路径点编辑")
st.sidebar.markdown("你可以添加、删除或修改路径点，修改后地图上的规划路径和无人机轨迹会实时更新。")

points_to_edit = st.session_state.points.copy()
for i, point in enumerate(points_to_edit):
    col1, col2, col3, col4 = st.sidebar.columns([3, 2, 2, 1])
    with col1:
        name = col1.text_input(f"名称", value=point["name"], key=f"name_{i}")
    with col2:
        lat = col2.number_input(f"纬度", value=point["lat"], format="%.6f", key=f"lat_{i}")
    with col3:
        lon = col3.number_input(f"经度", value=point["lon"], format="%.6f", key=f"lon_{i}")
    with col4:
        if col4.button("🗑️", key=f"del_{i}"):
            points_to_edit.pop(i)
            st.rerun()
    points_to_edit[i] = {"name": name, "lat": lat, "lon": lon}

new_name = st.sidebar.text_input("名称", value="新地点")
new_lat = st.sidebar.number_input("纬度", value=32.2330, format="%.6f")
new_lon = st.sidebar.number_input("经度", value=118.7860, format="%.6f")
if st.sidebar.button("➕ 添加路径点"):
    points_to_edit.append({"name": new_name, "lat": new_lat, "lon": new_lon})
    st.rerun()

if points_to_edit != st.session_state.points:
    st.session_state.points = points_to_edit
    st.rerun()

st.sidebar.markdown("---")
st.sidebar.info("路径点修改后，无人机会立即沿新路径移动。")

# ----------------------------- 实时显示区 -----------------------------
placeholder = st.empty()
chart_placeholder = st.empty()
map_placeholder = st.empty()
debug_placeholder = st.empty()

# 心跳状态显示
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

# 调试信息
with debug_placeholder.container():
    pos_len = len(st.session_state.positions)
    st.info(f"当前 positions 队列长度: {pos_len}")
    if pos_len == 0:
        st.warning("⚠️ 尚未收到任何位置数据。")

# 心跳折线图
if st.session_state.history:
    df = pd.DataFrame(st.session_state.history, columns=['timestamp', 'seq'])
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

# ----------------------------- 地图（南京科技职业学院定制） -----------------------------
if st.session_state.positions:
    pos_df = pd.DataFrame(st.session_state.positions, columns=['lat', 'lon', 'altitude', 'seq'])
    current_pos = pos_df.iloc[-1]

    # 创建地图，以校园为中心
    m = folium.Map(location=CAMPUS_CENTER, zoom_start=CAMPUS_ZOOM, tiles="OpenStreetMap")

    # 添加校园范围多边形（示意）
    folium.Polygon(
        locations=CAMPUS_BOUNDARY,
        color="green",
        weight=2,
        fill=True,
        fill_color="green",
        fill_opacity=0.15,
        popup=f"{CAMPUS_NAME} 校园范围"
    ).add_to(m)

    # 添加学校主入口标注
    folium.Marker(
        location=[CAMPUS_CENTER[0] - 0.0002, CAMPUS_CENTER[1] + 0.0001],  # 微调位置
        popup=folium.Popup(
            f"<b>{CAMPUS_NAME}</b><br>地址：{CAMPUS_ADDRESS}<br>占地面积：740亩<br>建校：1958年",
            max_width=300
        ),
        icon=folium.Icon(color="blue", icon="home", prefix='glyphicon')
    ).add_to(m)

    # 添加校园主要建筑
    for f in CAMPUS_FACILITIES:
        folium.Marker(
            location=[f["lat"], f["lon"]],
            popup=folium.Popup(f["name"], max_width=200),
            icon=folium.Icon(color=f["color"], icon=f["icon"], prefix='glyphicon')
        ).add_to(m)

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

    # 显示地图（固定 key 以减少重绘）
    st_folium(m, width=800, height=500, key="njpi_map")
else:
    map_placeholder.info("等待位置数据...")

# ----------------------------- 刷新控制 -----------------------------
time.sleep(3)  # 降低刷新频率，减少地图跳动
st.rerun()
