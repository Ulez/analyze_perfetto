import argparse
import sys
import os
from perfetto.trace_processor import TraceProcessor, TraceProcessorConfig

def main():
    parser = argparse.ArgumentParser(description="Perfetto Trace 自动化分析工具")
    parser.add_argument("trace_file", help="Perfetto trace 文件路径")
    parser.add_argument("--t1", type=float, help="起始时间 (秒，可选，默认trace开始)")
    parser.add_argument("--t2", type=float, help="结束时间 (秒，可选，默认trace结束)")
    parser.add_argument("--process", type=str, required=True, help="指定进程名 (如 surfaceflinger)")
    
    args = parser.parse_args()

    tp_executable = os.path.expanduser('~/.local/share/perfetto/prebuilts/trace_processor_shell')
    if not os.path.exists(tp_executable):
        print(f"错误: 找不到解析引擎: {tp_executable}")
        sys.exit(1)

    print(f"正在加载 Trace: {args.trace_file} ...")
    
    try:
        config = TraceProcessorConfig(bin_path=tp_executable)
        tp = TraceProcessor(trace=args.trace_file, config=config)
    except Exception as e:
        print(f"解析 Trace 失败: {e}")
        sys.exit(1)

    # 1. 获取 Trace 基础时间边界
    res_bounds = tp.query("SELECT start_ts, end_ts FROM trace_bounds")
    bounds = next(res_bounds)
    trace_start_ts = bounds.start_ts
    trace_end_ts = bounds.end_ts

    # 2. 确定分析的时间窗口 (纳秒)
    w_start = trace_start_ts + int(args.t1 * 1e9) if args.t1 is not None else trace_start_ts
    w_end = trace_start_ts + int(args.t2 * 1e9) if args.t2 is not None else trace_end_ts
    
    # 安全检查
    w_start = max(w_start, trace_start_ts)
    w_end = min(w_end, trace_end_ts)
    w_dur = w_end - w_start

    print(f"分析窗口: {(w_start - trace_start_ts)/1e9:.3f}s -> {(w_end - trace_start_ts)/1e9:.3f}s (持续 {w_dur / 1e9:.3f} 秒)\n")

    # 3. 核心：系统负载分析 (使用你提供的 SQL 逻辑，并加入时间窗口过滤)
    # 注意：为了支持 t1/t2 裁剪，我们在内部查询中加入了时间过滤条件
    sys_load_sql = f"""
    SELECT
        (SUM_CPU_TIME * 100.0 / TOTAL_CPU_CAPACITY) AS system_cpu_usage_percent,
        SUM_CPU_TIME,
        TOTAL_CPU_CAPACITY
    FROM (
        SELECT
            (SELECT SUM(MAX(0, MIN(ts + dur, {w_end}) - MAX(ts, {w_start})))
             FROM sched_slice s
             JOIN thread t ON s.utid = t.utid
             WHERE t.name NOT LIKE 'swapper%'
             AND s.ts < {w_end} AND s.ts + s.dur > {w_start}) AS SUM_CPU_TIME,
            
            (SELECT {w_dur} * COUNT(DISTINCT cpu) FROM cpu) AS TOTAL_CPU_CAPACITY
    );
    """
    
    print("=" * 60)
    print("【1. 系统整体负载】")
    res_sys = tp.query(sys_load_sql)
    sys_data = next(res_sys)
    if sys_data.system_cpu_usage_percent is not None:
        print(f"全核平均负载: {sys_data.system_cpu_usage_percent:.2f} %")
        # 计算等效活跃核心数
        res_cpu_count = tp.query("SELECT COUNT(DISTINCT cpu) as n FROM cpu")
        cpu_count = next(res_cpu_count).n
        active_cores = (sys_data.SUM_CPU_TIME / w_dur) if w_dur > 0 else 0
        print(f"活跃核心当量: {active_cores:.2f} / {cpu_count} Cores")
    else:
        print("未获取到负载数据，请检查 trace 是否包含 sched_slice 信息")
    print("=" * 60)

    # 4. 各进程 TOP 10 (基于时间窗口裁剪)
    top_proc_sql = f"""
    SELECT 
        IFNULL(p.name, 'Unknown') as name,
        p.pid as pid,
        SUM(MAX(0, MIN(s.ts + s.dur, {w_end}) - MAX(s.ts, {w_start}))) / CAST({w_dur} AS FLOAT) * 100 as cpu_pct
    FROM sched_slice s
    JOIN thread t USING (utid)
    LEFT JOIN process p USING (upid)
    WHERE s.ts < {w_end} AND s.ts + s.dur > {w_start}
    GROUP BY p.upid
    ORDER BY cpu_pct DESC
    LIMIT 10
    """
    print("\n【2. Top 10 进程占用 (单核 100% 为基准)】")
    print(f"{'PID':<10} {'CPU %':<12} {'进程名'}")
    for row in tp.query(top_proc_sql):
        print(f"{str(row.pid):<10} {row.cpu_pct:<12.2f} {row.name}")

    # 5. 指定进程的线程细节
    thread_sql = f"""
    SELECT 
        t.name as name,
        t.tid as tid,
        SUM(MAX(0, MIN(s.ts + s.dur, {w_end}) - MAX(s.ts, {w_start}))) / CAST({w_dur} AS FLOAT) * 100 as cpu_pct
    FROM sched_slice s
    JOIN thread t USING (utid)
    JOIN process p USING (upid)
    WHERE p.name = '{args.process}'
    AND s.ts < {w_end} AND s.ts + s.dur > {w_start}
    GROUP BY t.utid
    ORDER BY cpu_pct DESC
    """
    print(f"\n【3. 进程 '{args.process}' 线程细节】")
    res_threads = tp.query(thread_sql)
    found = False
    for row in res_threads:
        if not found:
            print(f"{'TID':<10} {'CPU %':<12} {'线程名'}")
            found = True
        print(f"{str(row.tid):<10} {row.cpu_pct:<12.2f} {row.name}")
    
    if not found:
        print(f"  在指定窗口内未找到进程 '{args.process}' 的数据。")
    print("=" * 60)

if __name__ == "__main__":
    main()