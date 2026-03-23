# -
实现无人机通信心跳检测可视化
import threading
import time
import queue
import random
from datetime import datetime

# 用于存放事件记录的列表，每个元素为字典
data_log = []

# 共享变量：最近一次收到心跳的时间（秒级时间戳）
last_received_time = None
# 超时标志，避免重复记录同一超时事件
timeout_occurred = False
# 保护共享变量的锁
lock = threading.Lock()

# 心跳队列，用于发送方与接收方通信
heartbeat_queue = queue.Queue()

# 可选：设置丢包率（0~1），模拟网络不稳定，便于观察超时
# 设为0则无丢包，设为0.2表示20%的包丢失
PACKET_LOSS_RATE = 0.2

def send_heartbeat():
    """
    发送线程：每秒生成一个心跳包（包含序号和时间），放入队列。
    """
    seq = 1
    while not stop_event.is_set():
        # 模拟网络丢包：随机决定是否发送
        if random.random() > PACKET_LOSS_RATE:
            timestamp = time.time()
            packet = (seq, timestamp)
            heartbeat_queue.put(packet)
            print(f"[发送] 序号 {seq:3d} 时间 {datetime.fromtimestamp(timestamp).strftime('%H:%M:%S.%f')[:-3]}")
        else:
            print(f"[发送] 序号 {seq:3d} 丢包")

        seq += 1
        # 等待1秒，但为了响应停止事件，使用小循环
        for _ in range(10):
            if stop_event.is_set():
                break
            time.sleep(0.1)

def receive_heartbeat():
    """
    接收线程：从队列中取出心跳，更新最后接收时间，并记录接收事件。
    """
    global last_received_time, timeout_occurred
    while not stop_event.is_set():
        try:
            # 阻塞等待，最多1秒，以便及时响应停止
            seq, t = heartbeat_queue.get(timeout=1)
            with lock:
                last_received_time = t
                # 一旦收到包，清除超时标志
                if timeout_occurred:
                    timeout_occurred = False
                    print("[监控] 连接恢复")

            # 记录接收事件
            record = {
                'type': 'received',
                'timestamp': t,
                'seq': seq,
                'human_time': datetime.fromtimestamp(t).strftime('%H:%M:%S.%f')[:-3]
            }
            data_log.append(record)
            print(f"[接收] 序号 {seq:3d} 时间 {record['human_time']}")
        except queue.Empty:
            # 队列空，继续循环
            pass

def monitor_timeout():
    """
    监控线程：每0.5秒检查一次最后接收时间，若超过3秒则记录超时事件。
    """
    global last_received_time, timeout_occurred
    while not stop_event.is_set():
        current_time = time.time()
        with lock:
            if last_received_time is not None:
                elapsed = current_time - last_received_time
                if elapsed > 3.0 and not timeout_occurred:
                    timeout_occurred = True
                    # 记录超时事件
                    record = {
                        'type': 'timeout',
                        'timestamp': current_time,
                        'elapsed': elapsed,
                        'human_time': datetime.fromtimestamp(current_time).strftime('%H:%M:%S.%f')[:-3]
                    }
                    data_log.append(record)
                    print(f"[超时] 已超过 {elapsed:.1f} 秒未收到心跳")
        time.sleep(0.5)

def save_data_to_csv(filename="heartbeat_log.csv"):
    """
    将数据列表保存为CSV文件，便于后续可视化。
    """
    import csv
    with open(filename, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['类型', '时间戳', '序号', '人类时间', '超时时长(秒)'])
        for record in data_log:
            if record['type'] == 'received':
                writer.writerow(['接收', record['timestamp'], record['seq'], record['human_time'], ''])
            else:
                writer.writerow(['超时', record['timestamp'], '', record['human_time'], f"{record['elapsed']:.2f}"])
    print(f"数据已保存到 {filename}")

if __name__ == "__main__":
    # 停止事件，用于优雅退出
    stop_event = threading.Event()

    # 创建并启动线程
    sender = threading.Thread(target=send_heartbeat, name="Sender")
    receiver = threading.Thread(target=receive_heartbeat, name="Receiver")
    monitor = threading.Thread(target=monitor_timeout, name="Monitor")

    sender.start()
    receiver.start()
    monitor.start()

    print("模拟开始，按 Ctrl+C 停止...")
    try:
        # 让主线程等待用户中断
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n正在停止...")
        stop_event.set()
        # 等待所有线程结束
        sender.join()
        receiver.join()
        monitor.join()
        print("已停止")

    # 保存数据
    save_data_to_csv()

    # 可选：打印部分数据预览
    print("\n数据记录预览（最后5条）：")
    for record in data_log[-5:]:
        print(record)

    print(f"共记录 {len(data_log)} 个事件")
