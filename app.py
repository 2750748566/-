import streamlit as st
import time
import threading
import pandas as pd
from collections import deque
import datetime
import matplotlib.pyplot as plt

# 用于存储心跳历史的全局数据结构
history = deque(maxlen=200)
history_lock = threading.Lock()

def heartbeat_sender():
    """后台线程：每秒发送一次心跳（模拟自收自发）"""
    seq = 0
    while True:
        time.sleep(1)
        seq += 1
        now = time.time()
        # 更新 session_state 中的实时信息
        st.session_state['last_received'] = now
        st.session_state['current_seq'] = seq
        st.session_state['last_heartbeat_info'] = f"序号: {seq}, 时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now))}"
        st.session_state['timeout_flag'] = False  # 收到心跳，重置超时标志
        
        # 将心跳记录加入历史（线程安全）
        with history_lock:
            history.append((now, seq))

# 初始化 session_state
if 'initialized' not in st.session_state:
    st.session_state['last_received'] = time.time()
    st.session_state['current_seq'] = 0
    st.session_state['last_heartbeat_info'] = "等待心跳..."
    st.session_state['timeout_flag'] = False
    st.session_state['initialized'] = True
    # 启动后台心跳线程
    thread = threading.Thread(target=heartbeat_sender, daemon=True)
    thread.start()

# 页面标题
st.title("🚁 无人机心跳监控")
st.markdown("模拟无人机心跳自收自发，每秒发送一次，3秒未收到则报警")

# 实时显示当前系统时间
current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
st.sidebar.metric("🕒 本地系统时间", current_time)

# 实时显示区域
placeholder = st.empty()
chart_placeholder = st.empty()  # 用于放置图表的占位符

# 主循环：每秒刷新页面，检查超时并更新图表
while True:
    now = time.time()
    last = st.session_state['last_received']
    delta = now - last
    delta_int = int(round(delta))  # 取整数

    # 检查超时
    if delta > 3 and not st.session_state['timeout_flag']:
        st.session_state['timeout_flag'] = True

    # 更新顶部实时指标区域
    with placeholder.container():
        col1, col2 = st.columns(2)
        with col1:
            st.metric("最新心跳", st.session_state['last_heartbeat_info'])
        with col2:
            st.metric("距上次心跳", f"{delta_int} 秒")  # 显示整数秒

        if st.session_state['timeout_flag']:
            st.error("⚠️ 连接超时！超过 3 秒未收到心跳。")
        else:
            st.success("✅ 连接正常")

    # 绘制心跳包数曲线图（横轴为本地时间，精确到秒）
    with history_lock:
        if history:
            # 将历史数据转换为 DataFrame
            df = pd.DataFrame(history, columns=['timestamp', 'seq'])
            # 将时间戳转换为本地时间字符串（格式 HH:MM:SS）
            df['time_str'] = pd.to_datetime(df['timestamp'], unit='s').dt.strftime('%H:%M:%S')
            
            # 使用 matplotlib 绘制折线图
            fig, ax = plt.subplots(figsize=(10, 4))
            ax.plot(df['time_str'], df['seq'], marker='o', markersize=4, linewidth=2, color='#1f77b4')
            ax.set_xlabel('时间 (时:分:秒)')
            ax.set_ylabel('心跳包序号')
            ax.set_title('心跳包数量变化趋势')
            # 自动调整横轴标签，避免重叠
            plt.xticks(rotation=45, ha='right')
            # 如果数据点过多，可自动选择部分标签显示（这里保留全部，但旋转45度）
            plt.tight_layout()
            chart_placeholder.pyplot(fig)
            plt.close(fig)
        else:
            chart_placeholder.info("等待心跳数据...")

    time.sleep(1)  # 每秒刷新一次
