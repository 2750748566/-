import streamlit as st
import time
import math
from datetime import datetime
import pandas as pd
import matplotlib.pyplot as plt
import folium
from streamlit_folium import folium_static
from streamlit_autorefresh import st_autorefresh

# ------------------------------- 配置 ---------------------------------
SCHOOL_CENTER = [118.749413, 32.234097]
GAODE_TILE = "https://webst01.is.autonavi.com/appmaptile?style=6&x={x}&y={y}&z={z}"
BASE_SPEED = 5.0   # 米/秒
HEARTBEAT_INTERVAL = 0.2  # 心跳间隔(秒)

# ------------------------------- 辅助函数 -------------------------------
def distance(p1, p2):
    return math.hypot(p1[0]-p2[0], p1[1]-p2[1])

# ------------------------------- 主程序 -------------------------------
def main():
    st.set_page_config(layout="wide")
    st.title("🏫 南京科技职业学院 - 无人机地面站 (运动轨迹)")

    # 初始化 session_state
    if 'flight_started' not in st.session_state:
        st.session_state.flight_started = False
        st.session_state.progress = 0.0          # 0~1
        st.session_state.start_time = None
        st.session_state.history = []            # 存储心跳 (飞行时间, 序号)
        st.session_state.latest_seq = 0
        st.session_state.flight_trail = []       # 轨迹点列表 [lng, lat]
        # 默认起点和终点 (南京科技职业学院附近)
        st.session_state.points = {
            'A': [118.746956, 32.232945],
            'B': [118.751589, 32.235204]
        }
        st.session_state.drone_pos = st.session_state.points['A'][:]  # 当前位置
        st.session_state.plan_path = [st.session_state.points['A'], st.session_state.points['B']]
        st.session_state.flight_alt = 50
        st.session_state.drone_speed = 50        # 速度系数

    # 侧边栏
    with st.sidebar:
        st.header("📌 导航")
        page = st.radio("功能页面", ["航线规划", "飞行监控"])
        st.markdown("---")
        st.subheader("📊 系统状态")
        st.checkbox("A点已设", value=True, disabled=True)
        st.checkbox("B点已设", value=True, disabled=True)
        st.checkbox("飞行进行中", value=st.session_state.flight_started, disabled=True)

    # ========================= 航线规划页面 =========================
    if page == "航线规划":
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
            if new_speed != st.session_state.drone_speed:
                st.session_state.drone_speed = new_speed
                st.rerun()

            st.markdown("---")
            col_start, col_stop = st.columns(2)
            with col_start:
                if st.button("▶️ 开始飞行", type="primary", use_container_width=True):
                    # 重置飞行状态
                    st.session_state.flight_started = True
                    st.session_state.progress = 0.0
                    st.session_state.start_time = datetime.now()
                    st.session_state.history = []
                    st.session_state.latest_seq = 0
                    st.session_state.drone_pos = st.session_state.points['A'][:]
                    st.session_state.flight_trail = [st.session_state.drone_pos[:]]
                    st.success("飞行已开始，请切换到「飞行监控」查看进度")
                    st.rerun()
            with col_stop:
                if st.button("⏹️ 停止飞行", use_container_width=True):
                    st.session_state.flight_started = False
                    st.info("飞行已停止")
                    st.rerun()

            if st.session_state.flight_started:
                st.metric("实时进度", f"{st.session_state.progress*100:.1f}%")
                st.metric("当前心跳序号", st.session_state.latest_seq)

        # 地图显示
        with col_map:
            # 构建地图
            center = SCHOOL_CENTER
            m = folium.Map(location=[center[1], center[0]], zoom_start=16, tiles=GAODE_TILE, attr='高德')
            # 起点
            folium.Marker([st.session_state.points['A'][1], st.session_state.points['A'][0]], popup='起点A', icon=folium.Icon(color='green')).add_to(m)
            # 终点
            folium.Marker([st.session_state.points['B'][1], st.session_state.points['B'][0]], popup='终点B', icon=folium.Icon(color='red')).add_to(m)
            # 规划直线路径
            folium.PolyLine([[st.session_state.points['A'][1], st.session_state.points['A'][0]],
                             [st.session_state.points['B'][1], st.session_state.points['B'][0]]],
                            color='green', weight=4).add_to(m)
            # 历史轨迹
            if st.session_state.flight_trail:
                trail_points = [[lat, lng] for lng, lat in st.session_state.flight_trail]
                folium.PolyLine(trail_points, color='orange', weight=3).add_to(m)
            # 当前位置
            if st.session_state.flight_started:
                folium.Marker([st.session_state.drone_pos[1], st.session_state.drone_pos[0]],
                              icon=folium.Icon(color='blue', icon='plane', prefix='fa')).add_to(m)
            folium_static(m, width=700, height=550)

    # ========================= 飞行监控页面 =========================
    else:
        st.header("📡 飞行监控 - 实时心跳包")
        # 自动刷新页面 (每秒一次)
        st_autorefresh(interval=1000, key="monitor")

        if not st.session_state.flight_started:
            st.info("⏳ 飞行未开始。请切换到「航线规划」页面，设置起点终点后点击「开始飞行」。")
            st.stop()

        # 计算当前进度和位置
        if st.session_state.start_time:
            elapsed = (datetime.now() - st.session_state.start_time).total_seconds()
            total_dist = distance(st.session_state.points['A'], st.session_state.points['B'])
            speed = BASE_SPEED * (st.session_state.drone_speed / 100.0)
            total_time = total_dist / speed if speed > 0 else 1
            progress = min(1.0, elapsed / total_time)
            st.session_state.progress = progress

            # 计算当前位置 (线性插值)
            if progress < 1.0:
                lng = st.session_state.points['A'][0] + (st.session_state.points['B'][0] - st.session_state.points['A'][0]) * progress
                lat = st.session_state.points['A'][1] + (st.session_state.points['B'][1] - st.session_state.points['A'][1]) * progress
                st.session_state.drone_pos = [lng, lat]
                # 每0.2秒记录一个心跳，但这里simplify：每0.2秒增加一个心跳序号，但记录飞行时间
                # 为了生成心跳历史，我们根据已过时间计算应该有的心跳数量
                expected_seq = int(elapsed / HEARTBEAT_INTERVAL) + 1
                current_seq = len(st.session_state.history)
                for seq in range(current_seq+1, expected_seq+1):
                    # 模拟心跳时间戳（飞行时间）
                    flight_t = (seq - 1) * HEARTBEAT_INTERVAL
                    st.session_state.history.append({
                        'flight_time': flight_t,
                        'seq': seq
                    })
                if st.session_state.history:
                    st.session_state.latest_seq = st.session_state.history[-1]['seq']
                # 记录轨迹点（每0.5秒记录一个，避免太多）
                if len(st.session_state.flight_trail) == 0 or distance(st.session_state.flight_trail[-1], st.session_state.drone_pos) > 0.00001:
                    st.session_state.flight_trail.append(st.session_state.drone_pos[:])
            else:
                if st.session_state.progress >= 1.0:
                    st.session_state.flight_started = False
                    st.success("🏁 无人机已到达终点！")

        # 显示进度和心跳信息
        st.progress(st.session_state.progress, text=f"飞行进度：{st.session_state.progress*100:.1f}%")
        col1, col2, col3, col4 = st.columns(4)
        with col1: st.metric("飞行时间", f"{elapsed:.1f} s" if st.session_state.start_time else "0.0 s")
        with col2: st.metric("当前心跳序号", st.session_state.latest_seq)
        with col3: st.metric("飞行高度", f"{st.session_state.flight_alt} m")
        with col4: st.metric("速度系数", f"{st.session_state.drone_speed}%")

        st.markdown("---")
        st.subheader("💓 心跳序号 vs 飞行时间 (正比例关系)")

        if len(st.session_state.history) >= 2:
            times = [h['flight_time'] for h in st.session_state.history]
            seqs = [h['seq'] for h in st.session_state.history]
            fig, ax = plt.subplots(figsize=(8,5))
            ax.plot(times, seqs, marker='o', markersize=4, linewidth=2)
            ax.set_xlabel('飞行时间 (秒)')
            ax.set_ylabel('心跳包序号')
            ax.set_title('心跳序号与飞行时间关系（正比例）')
            ax.grid(True, linestyle='--', alpha=0.6)
            st.pyplot(fig)
            plt.close(fig)
        else:
            st.info("等待心跳数据...")

        st.markdown("---")
        st.subheader("📈 实时趋势")
        if len(st.session_state.history) > 1:
            df = pd.DataFrame([{"时间": h['flight_time'], "高度": st.session_state.flight_alt} for h in st.session_state.history[-50:]])
            st.line_chart(df, x="时间", y="高度")
        else:
            st.info("等待更多数据...")

if __name__ == "__main__":
    main()
