import argparse
import sys
import os
from perfetto.trace_processor import TraceProcessor, TraceProcessorConfig

def main():
    parser = argparse.ArgumentParser(description="Perfetto Trace 自动化 CPU 负载分析工具")
    parser.add_argument("trace_file", help="Perfetto trace 文件路径")
    parser.add_argument("--t1", type=float, required=True, help="起始时间 (秒，相对于 trace 开头)")
    parser.add_argument("--t2", type=float, required=True, help="结束时间 (秒，相对于 trace 开头)")
    parser.add_argument("--process", type=str, required=True, help="需要详细分析的指定进程名")
    
    args = parser.parse_args()

    if args.t1 >= args.t2:
        print("错误: t1 必须小于 t2")
        sys.exit(1)

    tp_executable = os.path.expanduser('~/.local/share/perfetto/prebuilts/trace_processor_shell')
    
    if not os.path.exists(tp_executable):
        print(f"错误: 找不到解析引擎: {tp_executable}")
        sys.exit(1)

    print(f"正在加载 Trace (使用本地引擎): {args.trace_file} ...")
    
    try:
        config = TraceProcessorConfig(bin_path=tp_executable)
        tp = TraceProcessor(trace=args.trace_file, config=config)
    except Exception as e:
        print(f"解析 Trace 失败: {e}")
        sys.exit(1)

    # 1. 获取 Trace 基础时间边界
    # 注意：新版 perfetto 库使用 query()
    res_bounds = tp.query("SELECT start_ts, end_ts FROM trace_bounds")
    for row in res_bounds:
        trace_start_ts = row.start_ts
        trace_end_ts = row.end_ts
        break

    # 2. 计算相对时间的纳秒绝对值
    w_start = trace_start_ts + int(args.t1 * 1e9)
    w_end = trace_start_ts + int(args.t2 * 1e9)
    
    if w_start > trace_end_ts or w_end < trace_start_ts:
        print(f"错误: 时间窗口 [{args.t1}s, {args.t2}s] 超出 Trace 范围")
        sys.exit(1)
        
    w_start = max(w_start, trace_start_ts)
    w_end = min(w_end, trace_end_ts)
    w_dur = w_end - w_start

    print(f"分析窗口: {args.t1}s -> {args.t2}s (持续 {w_dur / 1e9:.3f} 秒)\n")

    clipped_sched_cte = f"""
    WITH clipped_sched AS (
        SELECT 
            cpu,
            utid,
            MAX(0, MIN(ts + dur, {w_end}) - MAX(ts, {w_start})) as c_dur
        FROM sched
        WHERE ts < {w_end} AND ts + dur > {w_start} AND dur > 0
    )
    """

    # --- A: 系统整体负载 ---
    res_cpus = tp.query("SELECT COUNT(DISTINCT cpu) as n FROM sched")
    num_cpus = next(res_cpus).n or 8

    sys_load_query = clipped_sched_cte + "SELECT SUM(c_dur) as active_ns FROM clipped_sched"
    res_load = tp.query(sys_load_query)
    total_active_ns = next(res_load).active_ns or 0
    overall_load = (total_active_ns / w_dur) * 100 
    
    print("=" * 60)
    print(f"【1. 系统整体负载】")
    print(f"活动核心当量: {overall_load / 100:.2f} / {num_cpus} Cores")
    print(f"平均 CPU 使用率: {overall_load / num_cpus:.2f} %")
    print("=" * 60)

    # --- B: Top 10 进程 ---
    top_proc_query = clipped_sched_cte + f"""
    SELECT 
        IFNULL(process.name, 'Unknown') as name,
        process.pid as pid,
        SUM(c_dur) / CAST({w_dur} AS FLOAT) * 100 as cpu_pct
    FROM clipped_sched
    JOIN thread USING (utid)
    LEFT JOIN process USING (upid)
    GROUP BY process.upid
    ORDER BY cpu_pct DESC
    LIMIT 10
    """
    print("\n【2. Top 10 进程占用 (100% = 1个核心满载)】")
    print(f"{'PID':<10} {'CPU %':<12} {'进程名'}")
    for row in tp.query(top_proc_query):
        print(f"{str(row.pid):<10} {row.cpu_pct:<12.2f} {row.name}")

    # --- C: 指定进程的线程分析 ---
    target_threads_query = clipped_sched_cte + f"""
    SELECT 
        thread.name as name,
        thread.tid as tid,
        SUM(c_dur) / CAST({w_dur} AS FLOAT) * 100 as cpu_pct
    FROM clipped_sched
    JOIN thread USING (utid)
    JOIN process USING (upid)
    WHERE process.name = '{args.process}'
    GROUP BY thread.utid
    ORDER BY cpu_pct DESC
    """
    print(f"\n【3. 进程 '{args.process}' 线程细节】")
    res_threads = tp.query(target_threads_query)
    found = False
    for row in res_threads:
        if not found:
            print(f"{'TID':<10} {'CPU %':<12} {'线程名'}")
            found = True
        print(f"{str(row.tid):<10} {row.cpu_pct:<12.2f} {row.name}")
    
    if not found:
        print(f"  未找到进程 '{args.process}' 的相关数据。")
    print("=" * 60)

if __name__ == "__main__":
    main()