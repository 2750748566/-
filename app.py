import streamlit as st
import time
import threading
import pandas as pd
import numpy as np
from collections import deque
import datetime
import matplotlib.pyplot as plt
import math
import random
import json

# ================== 坐标转换（备用，高德直接使用 GCJ-02） ==================
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
history_heartbeat = deque(maxlen=200)
history_lock = threading.Lock()

shared_state = {
    'last_heartbeat_time': time.time(),
    'last_seq': 0,
    'timeout_flag': False,
    'running': True,
    'current_lng': 118.749413,
    'current_lat': 32.234097,
    'A_lng': 118.749413,
    'A_lat': 32.234097,
    'B_lng': 118.749413,
    'B_lat': 32.236000,
    'progress': 0.0,
    'direction': 1,
    'speed': 0.01,
    'flight_height': 10
}
state_lock = threading.Lock()

def generate_obstacles(A_lng, A_lat, B_lng, B_lat, num=5):
    obstacles = []
    for i in range(1, num+1):
        ratio = i / (num+1)
        lng = A_lng + (B_lng - A_lng) * ratio
        lat = A_lat + (B_lat - A_lat) * ratio
        height = random.uniform(20, 80)
        obstacles.append((lng, lat, height))
    return obstacles

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

# ================== 生成高德地图 HTML（包含前端定时器动态更新） ==================
def generate_map_html(center_lng, center_lat, A_lng, A_lat, B_lng, B_lat, obstacles, zoom=18):
    obstacles_js = [{'lng': lng, 'lat': lat, 'height': h} for (lng, lat, h) in obstacles]
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="initial-scale=1.0, user-scalable=no">
        <title>高德3D地图</title>
        <style>
            html, body, #container {{ width: 100%; height: 100%; margin: 0; padding: 0; }}
        </style>
    </head>
    <body>
        <div id="container"></div>
        <script src="https://webapi.amap.com/maps?v=2.0&key=YOUR_AMAP_KEY"></script>
        <script>
            var map;
            var droneMarker;
            
            function initMap() {{
                map = new AMap.Map('container', {{
                    center: [{center_lng}, {center_lat}],
                    zoom: {zoom},
                    viewMode: '3D',
                    pitch: 60,
                    rotation: 0,
                    layers: [new AMap.TileLayer.Satellite()]
                }});
                
                // 起点 A
                new AMap.Marker({{
                    position: [{A_lng}, {A_lat}],
                    title: '起点 A',
                    label: {{ content: 'A', offset: new AMap.Pixel(0, -20) }},
                    icon: 'https://webapi.amap.com/theme/v1.3/markers/n/mark_b.png'
                }}).setMap(map);
                
                // 终点 B
                new AMap.Marker({{
                    position: [{B_lng}, {B_lat}],
                    title: '终点 B',
                    label: {{ content: 'B', offset: new AMap.Pixel(0, -20) }},
                    icon: 'https://webapi.amap.com/theme/v1.3/markers/n/mark_r.png'
                }}).setMap(map);
                
                // AB 连线
                new AMap.Polyline({{
                    path: [[{A_lat}, {A_lng}], [{B_lat}, {B_lng}]],
                    strokeColor: '#808080',
                    strokeWeight: 3,
                    strokeStyle: 'dashed'
                }}).setMap(map);
                
                // 障碍物
                var obstacles = {json.dumps(obstacles_js)};
                for (var i = 0; i < obstacles.length; i++) {{
                    new AMap.Circle({{
                        center: [obstacles[i].lng, obstacles[i].lat],
                        radius: obstacles[i].height * 0.5,
                        fillColor: '#ff0000',
                        fillOpacity: 0.5,
                        strokeColor: '#aa0000',
                        strokeWeight: 1
                    }}).setMap(map);
                    new AMap.Text({{
                        text: obstacles[i].height.toFixed(0) + 'm',
                        position: [obstacles[i].lng, obstacles[i].lat],
                        offset: new AMap.Pixel(0, -10)
                    }}).setMap(map);
                }}
                
                // 无人机标记
                droneMarker = new AMap.Marker({{
                    position: [{A_lng}, {A_lat}],
                    title: '无人机',
                    icon: 'https://webapi.amap.com/theme/v1.3/markers/n/mark_blue.png',
                    label: {{ content: '✈️', offset: new AMap.Pixel(0, -20) }}
                }});
                droneMarker.setMap(map);
            }}
            
            // 供外部调用的更新函数
            function updateDronePosition(lng, lat) {{
                if (droneMarker) droneMarker.setPosition([lng, lat]);
            }}
            
            // 前端定时器：每秒向 Streamlit 后端请求最新位置（通过 Streamlit 的 setComponentValue）
            // 由于 Streamlit 组件通信较复杂，这里采用简单方案：页面加载后启动 setInterval，
            // 通过 fetch 从后端 API 获取数据。但 Streamlit 没有原生 REST API，因此改为通过
            // 隐藏的 iframe 通信？为了简化，我们将位置数据嵌入到页面中并通过刷新整个页面？
            // 不，我们希望无刷新。因此最佳方式：使用 Streamlit 的 `st.components.v1.html` 配合
            // `streamlit` 的 `Component` 双向通信。但这较复杂。
            // 为了快速实现完全无跳动，我们放弃前端定时器，改为在 Python 端通过 `st.empty()` 每秒
            // 注入一段 JavaScript 来更新位置（不会重建地图），且不会造成地图闪烁。
            // 该方案已在主循环中实现，此处仅定义函数。
            
            initMap();
            window.updateDronePosition = updateDronePosition;
        </script>
    </body>
    </html>
    """
    return html

# ================== Streamlit 主界面 ==================
def main():
    st.set_page_config(page_title="无人机监控系统 - 南京科院 (高德3D无跳动)", layout="wide")
    st.title("🚁 无人机心跳与轨迹监控 (高德3D地图 - 完全不跳动)")
    st.markdown("地图只创建一次，无人机标记通过 JavaScript 动态更新，页面无闪烁。")
    
    # 高德地图 API Key 输入
    amap_key = st.sidebar.text_input("高德地图 API Key", type="password", 
                                     help="请到 https://lbs.amap.com/ 注册获取 Web端(JS API) 的 Key")
    if not amap_key:
        st.sidebar.warning("请输入高德地图 API Key")
        st.stop()
    
    # 启动后台线程
    if 'worker_started' not in st.session_state:
        st.session_state.worker_started = True
        thread = threading.Thread(target=background_worker, daemon=True)
        thread.start()
    
    # 侧边栏控制面板
    st.sidebar.header("🎮 控制面板")
    st.sidebar.subheader("📍 起点 A (GCJ-02)")
    colA1, colA2 = st.sidebar.columns(2)
    with colA1:
        new_A_lat = st.number_input("纬度", value=32.234097, format="%.6f", key="A_lat")
    with colA2:
        new_A_lng = st.number_input("经度", value=118.749413, format="%.6f", key="A_lng")
    if st.sidebar.button("设置 A 点"):
        with state_lock:
            shared_state['A_lng'] = new_A_lng
            shared_state['A_lat'] = new_A_lat
            shared_state['progress'] = 0.0
            shared_state['direction'] = 1
        st.sidebar.success("A点已更新，请刷新页面以重建地图")
    
    st.sidebar.subheader("📍 终点 B (GCJ-02)")
    colB1, colB2 = st.sidebar.columns(2)
    with colB1:
        new_B_lat = st.number_input("纬度", value=32.236000, format="%.6f", key="B_lat")
    with colB2:
        new_B_lng = st.number_input("经度", value=118.749413, format="%.6f", key="B_lng")
    if st.sidebar.button("设置 B 点"):
        with state_lock:
            shared_state['B_lng'] = new_B_lng
            shared_state['B_lat'] = new_B_lat
            shared_state['progress'] = 0.0
            shared_state['direction'] = 1
        st.sidebar.success("B点已更新，请刷新页面以重建地图")
    
    st.sidebar.subheader("✈️ 飞行参数")
    new_height = st.sidebar.number_input("飞行高度 (m)", value=10, step=1, key="height")
    if st.sidebar.button("设置高度"):
        with state_lock:
            shared_state['flight_height'] = new_height
        st.sidebar.success(f"高度设为 {new_height} m")
    
    # 实时状态显示
    st.sidebar.markdown("---")
    st.sidebar.subheader("📡 实时状态")
    local_time_placeholder = st.sidebar.empty()
    heartbeat_info_placeholder = st.sidebar.empty()
    timeout_placeholder = st.sidebar.empty()
    flight_info_placeholder = st.sidebar.empty()
    
    col1, col2 = st.columns([2, 3])
    with col1:
        st.subheader("💓 心跳时序图")
        chart_placeholder = st.empty()
    with col2:
        st.subheader("🗺️ 高德3D卫星地图 (无跳动)")
        map_placeholder = st.empty()
    
    # 生成障碍物
    with state_lock:
        A_lng, A_lat = shared_state['A_lng'], shared_state['A_lat']
        B_lng, B_lat = shared_state['B_lng'], shared_state['B_lat']
    obstacles = generate_obstacles(A_lng, A_lat, B_lng, B_lat, num=5)
    
    school_center_lng = 118.749413
    school_center_lat = 32.234097
    html_map = generate_map_html(school_center_lng, school_center_lat,
                                 A_lng, A_lat, B_lng, B_lat, obstacles, zoom=18)
    html_map = html_map.replace("YOUR_AMAP_KEY", amap_key)
    
    # 嵌入地图（只创建一次）
    with map_placeholder.container():
        import streamlit.components.v1 as components
        components.html(html_map, height=550, scrolling=False)
    
    # 用于动态更新无人机位置的脚本占位符（每次更新会覆盖之前的内容，不会累积）
    script_placeholder = st.empty()
    
    # 主循环：每秒更新心跳数据和曲线，并通过 JS 更新无人机位置（无地图重建）
    while True:
        now = time.time()
        with state_lock:
            last_time = shared_state['last_heartbeat_time']
            seq = shared_state['last_seq']
            timeout_flag = shared_state['timeout_flag']
            cur_lng = shared_state['current_lng']
            cur_lat = shared_state['current_lat']
            progress = shared_state['progress']
            height = shared_state['flight_height']
        
        delta = now - last_time
        delta_int = int(round(delta))
        
        if delta > 3 and not timeout_flag:
            with state_lock:
                shared_state['timeout_flag'] = True
        
        # 侧边栏更新
        local_time_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        local_time_placeholder.metric("🕒 本地时间", local_time_str)
        heartbeat_info_placeholder.metric("最新心跳", f"序号 {seq}  @ {time.strftime('%H:%M:%S', time.localtime(last_time))}")
        if timeout_flag or delta > 3:
            timeout_placeholder.error(f"⚠️ 连接超时！ 已 {delta_int} 秒未收到心跳")
        else:
            timeout_placeholder.success(f"✅ 连接正常  距上次心跳 {delta_int} 秒")
        flight_info_placeholder.metric("飞行进度", f"{progress*100:.1f}%  (A→B往返)")
        
        # 心跳曲线图
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
        
        # 通过 JavaScript 更新无人机位置（无页面刷新，无地图重建）
        js_code = f"""
        <script>
            var iframe = document.querySelector('iframe');
            if (iframe && iframe.contentWindow) {{
                iframe.contentWindow.updateDronePosition({cur_lng}, {cur_lat});
            }}
        </script>
        """
        # 使用 script_placeholder 来替换之前的内容，避免重复添加
        script_placeholder.components.v1.html(js_code, height=0)
        
        time.sleep(1)

if __name__ == "__main__":
    main()
