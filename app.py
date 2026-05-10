import streamlit as st
import time
import math
from datetime import datetime
import pandas as pd
import matplotlib.pyplot as plt
import folium
from streamlit_folium import st_folium
from streamlit_autorefresh import st_autorefresh

# ------------------------------- 配置 ---------------------------------
SCHOOL_CENTER = [118.749413, 32.234097]        # 南京科技职业学院中心
GAODE_TILE = "https://webst01.is.autonavi.com/appmaptile?style=6&x={x}&y={y}&z={z}"  # 高德卫星图
HEARTBEAT_INTERVAL = 0.2   # 心跳间隔（秒）
BASE_SPEED = 5.0           # 基础速度（米/秒）

def distance(p1, p2):
    return math.hypot(p1[0]-p2[0], p1[1]-p2[1])

# ------------------------------- 主程序 -------------------------------
def main():
    st.set_page_config(layout="wide")
    st.title("🏫 南京科技职业学院 - 无人机地面站 (心跳正比例图像)")

    # 初始化 session_state
    if 'flight_started' not in st.session_state:
        st.session_state.flight_started = False
        st.session_state.start_time = None
        st.session_state.points = {
            'A': [118.746956, 32.232945],
            'B': [118.751589, 32.235204]
        }
        st.session_state.flight_alt = 50
        st.session_state.drone_speed = 50
        st.session_state.progress = 0.0
        st.session_state.flight_trail = []          # 轨迹点 [lng, lat]
        st.session_state.history = []               # 心跳历史 [{'flight_time': float, 'seq': int}]
        st.session_state.page = "航线规划"
        st.session_state.arrived = False
        # 总飞行时间（秒）
        total_dist = distance(st.session_state.points['A'], st.session_state.points['B'])
        speed = BASE_SPEED * (st.session_state.drone_speed / 100.0)
        st.session_state.total_time = total_dist / speed if speed > 0 else 0.001

    # 侧边栏
    with st.sidebar:
        st.header("📌 导航")
        st.session_state.page = st.radio("功能页面", ["🗺️ 航线规划", "📡 飞行监控"])
        st.markdown("---")
        st.subheader("📊 系统状态")
        st.checkbox("A点已设", value=True, disabled=True)
        st.checkbox("B点已设", value=True, disabled=True)
        st.checkbox("飞行进行中", value=st.session_state.flight_started, disabled=True)

    # ========================= 航线规划页面 =========================
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
                # 重新计算总时间
                new_dist = distance(st.session_state.points['A'], st.session_state.points['B'])
                st.session_state.total_time = new_dist / (BASE_SPEED * (st.session_state.drone_speed/100.0))
                st.rerun()

            st.markdown("#### 📍 终点 B")
            col_b1, col_b2 = st.columns(2)
            with col_b1:
                b_lat = st.number_input("纬度", value=st.session_state.points['B'][1], format="%.6f", key="b_lat")
            with col_b2:
                b_lng = st.number_input("经度", value=st.session_state.points['B'][0], format="%.6f", key="b_lng")
            if st.button("设置 B 点", use_container_width=True):
                st.session_state.points['B'] = [b_lng, b_lat]
                new_dist = distance(st.session_state.points['A'], st.session_state.points['B'])
                st.session_state.total_time = new_dist / (BASE_SPEED * (st.session_state.drone_speed/100.0))
                st.rerun()

            st.markdown("---")
            st.subheader("✈️ 飞行参数")
            new_alt = st.slider("飞行高度 (m)", 10, 200, st.session_state.flight_alt, 5)
            if new_alt != st.session_state.flight_alt:
                st.session_state.flight_alt = new_alt
                st.rerun()
            new_speed = st.slider("速度系数 (%)", 10, 100, st.session_state.drone_speed, 5)
            if new_speed != st.session_state.drone_speed:
                st.session_state.drone_speed = new_speed
                # 重新计算总时间
                new_dist = distance(st.session_state.points['A'], st.session_state.points['B'])
                st.session_state.total_time = new_dist / (BASE_SPEED * (new_speed/100.0))
                st.rerun()

            st.markdown("---")
            col_start, col_stop = st.columns(2)
            with col_start:
                if st.button("▶️ 开始飞行", type="primary", use_container_width=True):
                    st.session_state.flight_started = True
                    st.session_state.start_time = datetime.now()
                    st.session_state.progress = 0.0
                    st.session_state.history = []
                    st.session_state.flight_trail = [st.session_state.points['A'][:]]
                    st.session_state.arrived = False
                    st.success("飞行已开始，请切换至「飞行监控」查看进度")
                    st.rerun()
            with col_stop:
                if st.button("⏹️ 停止飞行", use_container_width=True):
                    st.session_state.flight_started = False
                    st.info("飞行已停止")
                    st.rerun()

            if st.session_state.flight_started and st.session_state.start_time:
                elapsed = (datetime.now() - st.session_state.start_time).total_seconds()
                progress = min(1.0, elapsed / st.session_state.total_time)
                st.session_state.progress = progress
                st.metric("实时进度", f"{progress*100:.1f}%")
                if st.session_state.history:
                    st.metric("当前心跳序号", st.session_state.history[-1]['seq'])

        # 地图显示（使用 Folium + 高德瓦片，避免序列化错误，因为地图中没有 Draw 插件等）
        with col_map:
            # 计算当前无人机位置
            drone_pos = None
            if st.session_state.flight_started and st.session_state.start_time:
                elapsed = (datetime.now() - st.session_state.start_time).total_seconds()
                progress = min(1.0, elapsed / st.session_state.total_time)
                if progress < 1.0:
                    lng = st.session_state.points['A'][0] + (st.session_state.points['B'][0] - st.session_state.points['A'][0]) * progress
                    lat = st.session_state.points['A'][1] + (st.session_state.points['B'][1] - st.session_state.points['A'][1]) * progress
                    drone_pos = [lng, lat]
                else:
                    drone_pos = st.session_state.points['B'][:]
            # 构建地图
            m = folium.Map(location=[SCHOOL_CENTER[1], SCHOOL_CENTER[0]], zoom_start=16, tiles=GAODE_TILE, attr='高德')
            # 起点
            folium.Marker([st.session_state.points['A'][1], st.session_state.points['A'][0]], popup='起点A', icon=folium.Icon(color='green')).add_to(m)
            # 终点
            folium.Marker([st.session_state.points['B'][1], st.session_state.points['B'][0]], popup='终点B', icon=folium.Icon(color='red')).add_to(m)
            # 规划路径（直线）
            folium.PolyLine([[st.session_state.points['A'][1], st.session_state.points['A'][0]],
                             [st.session_state.points['B'][1], st.session_state.points['B'][0]]],
                            color='green', weight=4).add_to(m)
            # 历史轨迹
            if st.session_state.flight_trail:
                trail_points = [[lat, lng] for lng, lat in st.session_state.flight_trail]
                folium.PolyLine(trail_points, color='orange', weight=3).add_to(m)
            # 无人机当前位置
            if drone_pos:
                folium.Marker([drone_pos[1], drone_pos[0]], icon=folium.Icon(color='blue', icon='plane', prefix='fa')).add_to(m)
            # 使用 st_folium，但必须保证地图中没有不可序列化的元素（如 Draw 插件）。这里所有元素都是可序列化的。
            st_folium(m, width=700, height=550, returned_objects=[])

    # ========================= 飞行监控页面 =========================
    else:
        st.header("📡 飞行监控 - 实时心跳包")
        st_autorefresh(interval=1000, key="monitor")   # 每秒自动刷新

        if not st.session_state.flight_started:
            st.info("⏳ 飞行未开始。请切换到「航线规划」页面，设置起点终点后点击「开始飞行」。")
            st.stop()

        # 更新进度和心跳历史
        if st.session_state.start_time:
            elapsed = (datetime.now() - st.session_state.start_time).total_seconds()
            progress = min(1.0, elapsed / st.session_state.total_time)
            st.session_state.progress = progress

            # 生成心跳
            expected_seq = int(elapsed / HEARTBEAT_INTERVAL) + 1
            current_seq = len(st.session_state.history)
            for seq in range(current_seq+1, expected_seq+1):
                flight_t = (seq - 1) * HEARTBEAT_INTERVAL
                st.session_state.history.append({'flight_time': flight_t, 'seq': seq})

            # 更新轨迹点（仅在进展明显时添加）
            if progress < 1.0:
                lng = st.session_state.points['A'][0] + (st.session_state.points['B'][0] - st.session_state.points['A'][0]) * progress
                lat = st.session_state.points['A'][1] + (st.session_state.points['B'][1] - st.session_state.points['A'][1]) * progress
                if len(st.session_state.flight_trail) == 0 or distance(st.session_state.flight_trail[-1], [lng, lat]) > 0.00001:
                    st.session_state.flight_trail.append([lng, lat])
            else:
                if not st.session_state.arrived:
                    st.session_state.arrived = True
                    st.session_state.flight_started = False
                    st.success("🎉 无人机已到达目的地！")
                    st.rerun()

        if not st.session_state.history:
            st.warning("等待心跳数据...")
            st.stop()

        latest = st.session_state.history[-1]
        st.progress(st.session_state.progress, text=f"✈️ 飞行进度：{st.session_state.progress*100:.1f}%")

        # 主要指标
        col1, col2, col3, col4 = st.columns(4)
        with col1: st.metric("⏰ 飞行时间", f"{latest['flight_time']:.1f} s")
        with col2: st.metric("💓 当前心跳序号", latest['seq'])
        with col3: st.metric("📏 飞行高度", f"{st.session_state.flight_alt} m")
        with col4: st.metric("⚡ 速度系数", f"{st.session_state.drone_speed}%")

        st.markdown("---")
        st.subheader("💓 心跳序号 vs 飞行时间 (正比例关系)")

        if len(st.session_state.history) >= 2:
            times = [h['flight_time'] for h in st.session_state.history]
            seqs = [h['seq'] for h in st.session_state.history]
            fig, ax = plt.subplots(figsize=(8, 5))
            ax.plot(times, seqs, marker='o', markersize=4, linewidth=2, color='#1f77b4')
            ax.set_xlabel('飞行时间 (秒)', fontsize=12)
            ax.set_ylabel('心跳包序号', fontsize=12)
            ax.set_title('心跳序号与飞行时间关系（正比例）', fontsize=14)
            ax.grid(True, linestyle='--', alpha=0.6)
            st.pyplot(fig)
            plt.close(fig)
        else:
            st.info(f"等待更多心跳数据... (当前 {len(st.session_state.history)} 个)")

        st.markdown("---")
        st.subheader("📈 实时趋势（高度）")
        if len(st.session_state.history) > 1:
            df = pd.DataFrame([{"时间": h['flight_time'], "高度(m)": st.session_state.flight_alt} for h in st.session_state.history[-50:]])
            st.line_chart(df, x="时间", y="高度(m)")
        else:
            st.info("等待更多数据...")

if __name__ == "__main__":
    main()
