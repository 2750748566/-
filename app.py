import streamlit as st
import time
import threading

def heartbeat_sender():
    """后台线程：每秒发送一次心跳（模拟自收自发）"""
    seq = 0
    while True:
        time.sleep(1)
        seq += 1
        now = time.time()
        # 更新 session_state 中的心跳信息
        st.session_state['last_received'] = now
        st.session_state['current_seq'] = seq
        st.session_state['last_heartbeat_info'] = f"序号: {seq}, 时间: {time.strftime('%Y-%m-%d %H:%M:%S')}"
        st.session_state['timeout_flag'] = False  # 收到心跳，重置超时标志

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

# 实时显示区域
placeholder = st.empty()

# 主循环：每秒刷新页面，检查超时
while True:
    now = time.time()
    last = st.session_state['last_received']
    delta = now - last

    # 检查超时（仅当未超时时触发）
    if delta > 3 and not st.session_state['timeout_flag']:
        st.session_state['timeout_flag'] = True

    with placeholder.container():
        col1, col2 = st.columns(2)
        with col1:
            st.metric("最新心跳", st.session_state['last_heartbeat_info'])
        with col2:
            st.metric("距上次心跳", f"{delta:.2f} 秒")

        if st.session_state['timeout_flag']:
            st.error("⚠️ 连接超时！超过 3 秒未收到心跳。")
        else:
            st.success("✅ 连接正常")

    time.sleep(1)   # 每秒刷新一次
